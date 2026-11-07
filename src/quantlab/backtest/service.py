"""Backtest service (BT-001) with reproducibility manifests (BT-002) and re-run comparison (BT-003).

Flow of ``run``:
    strategy (registry) -> resolve parameters -> snapshot copy of strategy files into runs/<id>/algorithm
    -> record strategy version (tree hash of the copy) -> probe engine -> record a ``running`` row
    -> engine start/wait/collect -> QuantLab metrics + cross-check vs engine statistics
    -> write manifest.json / metrics.json -> terminal, immutable row.

Runs are synchronous for now (a background job runner is CORE-007). Data is either the engine's bundled
**sample** data (``data_provenance = "sample"``) or a pinned version of a QuantLab dataset, exported to the
engine format and recorded (dataset identity, version hash, export hash, provenance) in the manifest. Only
``real_historical`` / ``live_recorded`` provenance may ever be presented as real performance.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import quantlab
from quantlab.analytics.metrics import MetricsError, cross_check, equity_metrics
from quantlab.backtest.models import BacktestRun
from quantlab.core.clock import Clock, SystemClock
from quantlab.core.config import Settings
from quantlab.core.ids import new_id
from quantlab.core.logging import get_logger
from quantlab.data.catalog import DataCatalog
from quantlab.data.lean_export import read_export_manifest
from quantlab.data.schema import Provenance
from quantlab.engine.base import (
    AlgorithmLanguage,
    AlgorithmSpec,
    BacktestSpec,
    EngineAdapter,
    EngineError,
    EngineInfo,
    EngineResult,
    RunState,
)
from quantlab.strategies.registry import Strategy, StrategyRegistry, load_manifest, resolve_parameters
from quantlab.strategies.snapshot import copy_snapshot, git_info

log = get_logger(__name__)

MANIFEST_SCHEMA = 1
SAMPLE_DATA_NOTE = "engine-bundled sample data; not suitable for performance claims"


class BacktestError(RuntimeError):
    pass


@dataclass(frozen=True)
class DataBinding:
    """Which market data a run uses."""

    provenance: str
    data_version: str | None = None  # dataset version content hash
    dataset: dict[str, Any] | None = None  # dataset identity
    export_path: Path | None = None
    export_hash: str | None = None
    corporate_actions: int = 0
    normalization: str | None = None

    @property
    def note(self) -> str:
        if self.provenance == "sample":
            return SAMPLE_DATA_NOTE
        if not Provenance(self.provenance).is_real:
            return f"{self.provenance} data; not a performance claim"
        return "real historical data (see dataset source and licence)"

    def manifest(self) -> dict[str, Any]:
        return {
            "provenance": self.provenance,
            "data_version": self.data_version,
            "dataset": self.dataset,
            "export_hash": self.export_hash,
            "note": self.note,
            "corporate_actions": self.corporate_actions,
            "normalization": self.normalization,
        }


SAMPLE = DataBinding("sample")


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(obj: Any) -> str:
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


def engine_identity(info: EngineInfo) -> dict[str, Any]:
    """The parts of EngineInfo that determine behaviour (excludes e.g. the Docker daemon version)."""
    return {"name": info.name, "image": info.image, "image_id": info.image_id}


def result_fingerprint(result: EngineResult) -> dict[str, Any]:
    """Deterministic content of a result used to compare runs."""
    return {
        "status": result.status,
        "runtime_error": result.runtime_error,
        "statistics": dict(result.statistics),
        "orders": [
            [o.id, o.time, o.symbol, o.type, o.direction, o.quantity, o.price, o.status]
            for o in result.orders
        ],
        "equity": [[t, v] for t, v in result.equity],
    }


@dataclass(frozen=True)
class ReproducibilityReport:
    original_id: str
    rerun_id: str
    inputs_identical: bool
    results_identical: bool
    differences: list[str] = field(default_factory=list)

    @property
    def reproduced(self) -> bool:
        return self.inputs_identical and self.results_identical


_OUTCOME_STATUS = {
    RunState.FAILED: "failed",
    RunState.TIMED_OUT: "timed_out",
    RunState.CANCELLED: "cancelled",
}


class BacktestService:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        engine: EngineAdapter | None,
        registry: StrategyRegistry | None = None,
        clock: Clock | None = None,
        catalog: DataCatalog | None = None,
    ) -> None:
        self._settings = settings
        self._sessions = session_factory
        self._engine_adapter = engine  # None: read-only service (queries only)
        self._clock = clock or SystemClock()
        self._registry = registry or StrategyRegistry(self._clock)
        self._catalog = catalog or DataCatalog(settings.data_root)

    # ---- queries -------------------------------------------------------------------------------
    def get(self, run_id: str) -> BacktestRun:
        with self._sessions() as session:
            run = session.get(BacktestRun, run_id)
            if run is None:
                raise BacktestError(f"unknown backtest run {run_id!r}")
            return run

    def list(self, strategy_name: str | None = None, limit: int = 100) -> list[BacktestRun]:
        with self._sessions() as session:
            query = select(BacktestRun).order_by(BacktestRun.id.desc()).limit(limit)
            if strategy_name is not None:
                strategy = self._registry.get(session, strategy_name)
                query = query.where(BacktestRun.strategy_id == strategy.id)
            return list(session.execute(query).scalars())

    # ---- commands ------------------------------------------------------------------------------
    def run(
        self,
        strategy_name: str,
        parameters: dict[str, Any] | None = None,
        timeout_s: float | None = None,
        *,
        dataset: str | None = None,
        dataset_version: str | None = None,
    ) -> BacktestRun:
        """Run a backtest on the engine's sample data, or on a pinned version of ``dataset``."""
        self._engine  # noqa: B018 - fail fast (before snapshotting) when no engine is configured
        run_id = new_id()
        with self._sessions() as session:
            strategy = self._registry.get(session, strategy_name)
            if strategy.archived:
                raise BacktestError(f"strategy {strategy_name!r} is archived")
            resolved = resolve_parameters(load_manifest(strategy.directory), parameters)
            binding = self._bind_data(session, dataset, dataset_version)
            git = git_info(strategy.directory)
            run_dir = self._new_run_dir(run_id)  # only after validation, so failed requests leave nothing
            snapshot = copy_snapshot(strategy.directory, run_dir / "algorithm")
            version = self._registry.record_version(session, strategy, snapshot, git)
            session.commit()
            version_id, tree_hash = version.id, version.tree_hash
            git_meta = {"commit": version.git_commit, "dirty": version.git_dirty}
        return self._execute(
            run_id, run_dir, strategy, version_id, tree_hash, git_meta, resolved, None, timeout_s, binding
        )

    def rerun(self, run_id: str, timeout_s: float | None = None) -> tuple[BacktestRun, ReproducibilityReport]:
        """Run again from the original's stored code snapshot, parameters, data and engine identity."""
        original = self.get(run_id)
        manifest = original.json("manifest")
        current = self._probe_or_raise()
        if engine_identity(current) != manifest["engine"]["identity"]:
            raise BacktestError(
                "cannot reproduce: the engine differs from the original run "
                f"(original {manifest['engine']['identity']}, current {engine_identity(current)})"
            )
        binding = self._rebind_data(manifest["data"])
        new_id_ = new_id()
        new_dir = self._new_run_dir(new_id_)
        snapshot = copy_snapshot(Path(original.run_dir) / "algorithm", new_dir / "algorithm")
        if snapshot.tree_hash != manifest["strategy"]["tree_hash"]:
            raise BacktestError(
                f"stored code snapshot of run {run_id} no longer matches its recorded tree hash"
            )
        with self._sessions() as session:
            strategy = session.get(Strategy, original.strategy_id)
            if strategy is None:
                raise BacktestError(f"strategy of run {run_id} not found")
        rerun = self._execute(
            new_id_,
            new_dir,
            strategy,
            original.strategy_version_id,
            snapshot.tree_hash,
            manifest["strategy"]["git"],
            dict(manifest["parameters"]),
            run_id,
            timeout_s,
            binding,
        )
        return rerun, self.compare(original, rerun)

    @staticmethod
    def compare(original: BacktestRun, rerun: BacktestRun) -> ReproducibilityReport:
        diffs: list[str] = []
        inputs_same = original.inputs_hash == rerun.inputs_hash
        if not inputs_same:
            a, b = original.json("manifest")["inputs"], rerun.json("manifest")["inputs"]
            diffs += [f"input '{k}' differs" for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
        results_same = original.result_hash is not None and original.result_hash == rerun.result_hash
        if not results_same:
            if original.result_hash is None or rerun.result_hash is None:
                diffs.append("one of the runs has no result")
            else:
                sa, sb = original.json("engine_statistics") or {}, rerun.json("engine_statistics") or {}
                stat_diffs = [
                    f"statistic '{k}': {sa.get(k)} != {sb.get(k)}"
                    for k in sorted(set(sa) | set(sb))
                    if sa.get(k) != sb.get(k)
                ]
                diffs += stat_diffs or ["orders, equity curve or status differ (statistics identical)"]
        return ReproducibilityReport(original.id, rerun.id, inputs_same, results_same, diffs)

    # ---- internals -----------------------------------------------------------------------------
    def _new_run_dir(self, run_id: str) -> Path:
        run_dir = self._settings.layout.runs_dir / run_id
        if run_dir.exists():
            raise BacktestError(f"run directory already exists: {run_dir}")
        run_dir.mkdir(parents=True)
        return run_dir

    @property
    def _engine(self) -> EngineAdapter:
        if self._engine_adapter is None:
            raise BacktestError(
                "no engine configured: set engine.lean.image (see CONFIGURATION_REFERENCE.md)"
            )
        return self._engine_adapter

    def _bind_data(self, session: Session, dataset: str | None, version: str | None) -> DataBinding:
        if dataset is None:
            if version is not None:
                raise BacktestError("dataset_version given without dataset")
            return SAMPLE
        path, export_hash, ds, resolved = self._catalog.lean_export(session, dataset, version)
        export = read_export_manifest(path)
        return DataBinding(
            ds.provenance,
            resolved.content_hash,
            ds.identity(),
            path,
            export_hash,
            int(export.get("corporate_actions", 0)),
            str(export.get("normalization")),
        )

    def _rebind_data(self, recorded: dict[str, Any]) -> DataBinding:
        if recorded.get("data_version") is None:
            return SAMPLE
        with self._sessions() as session:
            binding = self._bind_data(session, recorded["dataset"]["name"], recorded["data_version"])
        if binding.export_hash != recorded.get("export_hash") or binding.dataset != recorded.get("dataset"):
            raise BacktestError(
                "cannot reproduce: the dataset version's engine export no longer matches the original"
            )
        return binding

    def _probe_or_raise(self) -> EngineInfo:
        try:
            return self._engine.probe()
        except EngineError as exc:
            raise BacktestError(f"engine unavailable: {exc}") from exc

    def _execute(
        self,
        run_id: str,
        run_dir: Path,
        strategy: Strategy,
        version_id: str,
        tree_hash: str,
        git_meta: dict[str, Any],
        parameters: dict[str, str],
        rerun_of: str | None,
        timeout_s: float | None,
        data: DataBinding,
    ) -> BacktestRun:
        engine_error: str | None = None
        try:
            info = self._engine.probe()
        except EngineError as exc:
            info, engine_error = EngineInfo(name=self._engine.name, launcher="unknown"), str(exc)

        inputs = {
            "strategy_tree_hash": tree_hash,
            "entry_file": strategy.entry_file,
            "language": strategy.language,
            "parameters": parameters,
            "data_provenance": data.provenance,
            "data_version": data.data_version,
            "dataset": data.dataset,
            "data_export_hash": data.export_hash,
            "engine": engine_identity(info),
            "quantlab_version": quantlab.__version__,
        }
        manifest: dict[str, Any] = {
            "schema": MANIFEST_SCHEMA,
            "run_id": run_id,
            "rerun_of": rerun_of,
            "created_at": self._clock.now().isoformat(),
            "inputs": inputs,
            "inputs_hash": _sha256(inputs),
            "strategy": {
                "name": strategy.name,
                "version_id": version_id,
                "tree_hash": tree_hash,
                "git": git_meta,
                "entry_file": strategy.entry_file,
                "language": strategy.language,
            },
            "parameters": parameters,
            "data": data.manifest(),
            "engine": {"identity": engine_identity(info), "info": asdict(info)},
            "random_seed": None,  # LEAN backtests are deterministic for identical inputs
            "metrics_conventions": {
                "periods_per_year": 252,
                "risk_free_rate": 0.0,
                "resampling": "daily UTC last",
            },
        }
        with self._sessions() as session:
            row = BacktestRun(
                id=run_id,
                strategy_id=strategy.id,
                strategy_version_id=version_id,
                rerun_of=rerun_of,
                status="running" if engine_error is None else "engine_error",
                created_at=self._clock.now(),
                finished_at=None if engine_error is None else self._clock.now(),
                parameters=_canonical(parameters),
                data_provenance=data.provenance,
                data_version=data.data_version,
                engine_info=_canonical(asdict(info)),
                inputs_hash=manifest["inputs_hash"],
                error=engine_error,
                manifest=_canonical(manifest),
                run_dir=str(run_dir),
            )
            session.add(row)
            session.commit()
        self._write_json(run_dir / "manifest.json", manifest)
        if engine_error is not None:
            log.warning("backtest.engine_unavailable", run_id=run_id, error=engine_error)
            return self.get(run_id)

        updates = self._run_engine(run_id, run_dir, strategy, parameters, timeout_s, data)
        with self._sessions() as session:
            finished = session.get(BacktestRun, run_id)
            if finished is None:
                raise BacktestError(f"backtest run {run_id} disappeared")
            for key, value in updates.items():
                setattr(finished, key, value)
            finished.finished_at = self._clock.now()
            session.commit()
        log.info("backtest.finished", run_id=run_id, status=updates["status"])
        return self.get(run_id)

    def _run_engine(
        self,
        run_id: str,
        run_dir: Path,
        strategy: Strategy,
        parameters: dict[str, str],
        timeout_s: float | None,
        data: DataBinding,
    ) -> dict[str, Any]:
        spec = BacktestSpec(
            run_id=run_id,
            algorithm=AlgorithmSpec(
                run_dir / "algorithm", strategy.entry_file, AlgorithmLanguage(strategy.language)
            ),
            work_dir=run_dir / "engine",
            parameters=parameters,
            data_dir=data.export_path,
        )
        try:
            handle = self._engine.start_backtest(spec)
        except EngineError as exc:
            return {"status": "engine_error", "error": str(exc)}
        outcome = self._engine.wait(
            handle, timeout_s if timeout_s is not None else self._settings.engine.lean.timeout_s
        )
        if outcome.state is not RunState.SUCCEEDED:
            return {"status": _OUTCOME_STATUS[outcome.state], "error": outcome.detail or None}
        try:
            result = self._engine.collect(handle)
        except ValueError as exc:  # ResultParseError
            return {"status": "failed", "error": f"could not read engine results: {exc}"}

        updates: dict[str, Any] = {
            "status": "succeeded" if result.succeeded else "algorithm_error",
            "engine_status": result.status,
            "runtime_error": result.runtime_error or None,
            "engine_statistics": _canonical(dict(result.statistics)),
            "result_hash": _sha256(result_fingerprint(result)),
        }
        if not result.finished:
            updates["status"], updates["error"] = (
                "failed",
                f"engine exited but reported status {result.status!r}",
            )
        try:
            metrics = equity_metrics(result.equity)
        except MetricsError as exc:
            updates["error"] = f"metrics unavailable: {exc}"
        else:
            checks = cross_check(metrics, dict(result.statistic_values))
            updates["metrics"] = _canonical(metrics.to_dict())
            updates["cross_checks"] = _canonical([c.to_dict() for c in checks])
            self._write_json(
                run_dir / "metrics.json",
                {
                    "metrics": metrics.to_dict(),
                    "cross_checks": [c.to_dict() for c in checks],
                    "data_provenance": data.provenance,
                    "note": data.note,
                },
            )
        return updates

    @staticmethod
    def _write_json(path: Path, obj: Any) -> None:
        path.write_text(json.dumps(obj, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")

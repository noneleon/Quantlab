"""Lifecycle gates (STR-004, DEC-0026). Evidence is checked, never assumed.

* research → backtested: ≥ 1 succeeded backtest of the **current** strategy files (tree hash) on real data.
* backtested → validated: a succeeded walk-forward study of the current files on real data with at least
  ``min_oos_windows`` out-of-sample windows, stitched OOS Sharpe ≥ ``min_oos_sharpe`` and OOS max drawdown ≤
  ``max_oos_drawdown``; plus (``require_overfitting_checks``) a sweep of the current files whose deflated
  Sharpe probability ≥ ``min_dsr_probability`` and PBO ≤ ``max_pbo``.
* validated → paper: the validation must be for the current files (not stale). Records *approval* only — paper
  trading itself arrives in Phase 5.
* paper → live_candidate: needs paper-trading records (Phase 5): always refused for now.
* → retired / → research (demotion): always allowed with a reason.

Thresholds come from ``[lifecycle]`` and are copied into every event's evidence, so a later reader sees
which rules were applied. Nothing here can enable live trading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from quantlab.backtest.models import BacktestRun
from quantlab.backtest.service import BacktestError, BacktestService
from quantlab.core.config import Settings
from quantlab.data.schema import Provenance
from quantlab.optimize.service import OptimizationError, OptimizationService
from quantlab.strategies.lifecycle import FORWARD, LifecycleError, LifecycleEvent, LifecycleLog
from quantlab.strategies.registry import Strategy, StrategyRegistry, StrategyVersion
from quantlab.strategies.snapshot import hash_tree


@dataclass
class GateResult:
    target: str
    passed: bool
    checks: list[dict[str, Any]] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append({"check": name, "ok": ok, "detail": detail})
        if not ok:
            self.passed = False

    def to_dict(self) -> dict[str, Any]:
        return {"target": self.target, "passed": self.passed, "checks": self.checks}


class LifecycleService:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        backtests: BacktestService,
        optimizer: OptimizationService,
        registry: StrategyRegistry | None = None,
        log: LifecycleLog | None = None,
    ) -> None:
        self._settings = settings
        self._sessions = session_factory
        self._backtests = backtests
        self._optimizer = optimizer
        self._registry = registry or StrategyRegistry()
        self._log = log or LifecycleLog()

    # ---- queries ---------------------------------------------------------------------------------
    def status(self, name: str) -> dict[str, Any]:
        with self._sessions() as session:
            strategy = self._registry.get(session, name)
            stage, last = self._log.current(session, strategy)
            history = [self._event_view(e) for e in self._log.history(session, strategy)]
            directory = strategy.directory
        tree = hash_tree(directory).tree_hash
        stale = last is not None and last.tree_hash != tree and stage not in ("research", "retired")
        return {
            "strategy": name,
            "stage": stage,
            "tree_hash": tree,
            "stale": stale,
            "stale_note": (
                f"stage {stage!r} was granted for files {last.tree_hash[:12]}; the strategy has changed "
                "since (re-validate before relying on it)"
                if stale and last is not None
                else None
            ),
            "next_stage": FORWARD.get(stage),
            "history": history,
            "gates": self._settings.lifecycle.model_dump(),
        }

    @staticmethod
    def _event_view(e: LifecycleEvent) -> dict[str, Any]:
        return {
            "seq": e.seq,
            "from": e.from_stage,
            "to": e.to_stage,
            "tree_hash": e.tree_hash,
            "actor": e.actor,
            "reason": e.reason,
            "evidence": e.json("evidence"),
            "at": e.created_at,
        }

    # ---- transitions -----------------------------------------------------------------------------
    def evaluate(
        self,
        name: str,
        target: str,
        *,
        runs: list[str] | None = None,
        walk_forward: str | None = None,
        sweep: str | None = None,
    ) -> GateResult:
        """Check a promotion without recording anything (dry run)."""
        with self._sessions() as session:
            strategy = self._registry.get(session, name)
            stage, last = self._log.current(session, strategy)
            tree = hash_tree(strategy.directory).tree_hash
            result = GateResult(target, True)
            if target in ("retired", "research"):
                result.check("transition", stage != target, f"{stage} → {target}")
                return result
            if FORWARD.get(stage) != target:
                result.check(
                    "transition",
                    False,
                    f"{stage} → {target} is not a forward step (next: {FORWARD.get(stage)})",
                )
                return result
            if target == "backtested":
                self._gate_backtested(session, strategy, tree, runs or [], result)
            elif target == "validated":
                self._gate_validated(session, strategy, tree, walk_forward, sweep, result)
            elif target == "paper":
                fresh = last is not None and last.tree_hash == tree
                result.check(
                    "validation is current",
                    fresh,
                    "validated files match the current files"
                    if fresh
                    else "strategy files changed since validation",
                )
                result.check("note", True, "approval only: paper trading is implemented in Phase 5")
            elif target == "live_candidate":
                result.check(
                    "paper-trading record",
                    False,
                    "paper trading (Phase 5) does not exist yet, so no paper record can satisfy this gate",
                )
            return result

    def promote(
        self,
        name: str,
        target: str,
        *,
        reason: str,
        runs: list[str] | None = None,
        walk_forward: str | None = None,
        sweep: str | None = None,
        actor: str = "owner",
    ) -> tuple[GateResult, LifecycleEvent | None]:
        result = self.evaluate(name, target, runs=runs, walk_forward=walk_forward, sweep=sweep)
        if not result.passed:
            return result, None
        with self._sessions() as session:
            strategy = self._registry.get(session, name)
            tree = hash_tree(strategy.directory).tree_hash
            evidence = {
                "runs": runs or [],
                "walk_forward": walk_forward,
                "sweep": sweep,
                "checks": result.checks,
                "gates": self._settings.lifecycle.model_dump(),
            }
            entry = self._log.record(
                session, strategy, target, tree_hash=tree, reason=reason, evidence=evidence, actor=actor
            )
            session.commit()
            session.expunge(entry)
        return result, entry

    # ---- gates -----------------------------------------------------------------------------------
    def _version_hash(self, session: Session, run: BacktestRun) -> str | None:
        version = session.get(StrategyVersion, run.strategy_version_id)
        return version.tree_hash if version else None

    def _real(self, provenance: str) -> bool:
        return Provenance(provenance).is_real or not self._settings.lifecycle.require_real_data

    def _gate_backtested(
        self, session: Session, strategy: Strategy, tree: str, run_ids: list[str], result: GateResult
    ) -> None:
        result.check("evidence given", bool(run_ids), f"{len(run_ids)} run(s) (--run)")
        for run_id in run_ids:
            run = session.get(BacktestRun, run_id)
            if run is None:
                result.check(f"run {run_id}", False, "unknown run")
                continue
            result.check(f"run {run_id} strategy", run.strategy_id == strategy.id, "belongs to this strategy")
            result.check(f"run {run_id} status", run.status == "succeeded", run.status)
            result.check(
                f"run {run_id} data", self._real(run.data_provenance), f"provenance {run.data_provenance}"
            )
            result.check(
                f"run {run_id} version",
                self._version_hash(session, run) == tree,
                "ran the current strategy files"
                if self._version_hash(session, run) == tree
                else "ran older files",
            )

    def _gate_validated(
        self,
        session: Session,
        strategy: Strategy,
        tree: str,
        wf_id: str | None,
        sweep_id: str | None,
        result: GateResult,
    ) -> None:
        gates = self._settings.lifecycle
        if wf_id is None:
            result.check("walk-forward evidence", False, "a walk-forward study is required (--walk-forward)")
        else:
            self._check_study(session, strategy, tree, wf_id, "walk_forward", result)
            try:
                summary = self._optimizer.get(wf_id).json("summary") or {}
            except OptimizationError:
                summary = {}
            windows = [w for w in summary.get("windows", []) if w.get("oos_run")]
            metrics = summary.get("oos_metrics") or {}
            result.check(
                "OOS windows",
                len(windows) >= gates.min_oos_windows,
                f"{len(windows)} ≥ {gates.min_oos_windows}",
            )
            sharpe = metrics.get("sharpe")
            result.check(
                "OOS Sharpe",
                sharpe is not None and sharpe >= gates.min_oos_sharpe,
                f"{sharpe} ≥ {gates.min_oos_sharpe}",
            )
            dd = metrics.get("max_drawdown")
            result.check(
                "OOS max drawdown",
                dd is not None and dd <= gates.max_oos_drawdown,
                f"{dd} ≤ {gates.max_oos_drawdown}",
            )
        if gates.require_overfitting_checks:
            if sweep_id is None:
                result.check(
                    "sweep evidence", False, "a sweep with overfitting statistics is required (--sweep)"
                )
                return
            self._check_study(session, strategy, tree, sweep_id, "sweep", result)
            try:
                of = (self._optimizer.get(sweep_id).json("summary") or {}).get("overfitting", {})
            except OptimizationError:
                of = {}
            dsr = (of.get("deflated_sharpe") or {}).get("probability")
            result.check(
                "deflated Sharpe",
                dsr is not None and dsr >= gates.min_dsr_probability,
                f"{dsr} ≥ {gates.min_dsr_probability}",
            )
            pbo = (of.get("pbo") or {}).get("pbo")
            result.check("PBO", pbo is not None and pbo <= gates.max_pbo, f"{pbo} ≤ {gates.max_pbo}")

    def _check_study(
        self, session: Session, strategy: Strategy, tree: str, study_id: str, kind: str, result: GateResult
    ) -> None:
        try:
            study = self._optimizer.get(study_id)
        except OptimizationError:
            result.check(f"{kind} {study_id}", False, "unknown optimization")
            return
        result.check(f"{kind} {study_id} kind", study.kind == kind, study.kind)
        result.check(f"{kind} {study_id} strategy", study.strategy_name == strategy.name, study.strategy_name)
        result.check(f"{kind} {study_id} status", study.status == "succeeded", study.status)
        run_ids = [t.run_id for t in self._optimizer.trials(study_id) if t.run_id]
        runs = [session.get(BacktestRun, r) for r in run_ids]
        provenances = sorted({r.data_provenance for r in runs if r is not None})
        result.check(
            f"{kind} {study_id} data",
            bool(provenances) and all(self._real(p) for p in provenances),
            f"provenance {provenances}",
        )
        hashes = {self._version_hash(session, r) for r in runs if r is not None}
        result.check(
            f"{kind} {study_id} version",
            hashes == {tree},
            "all trials ran the current strategy files"
            if hashes == {tree}
            else "trials ran other strategy files",
        )


__all__ = ["BacktestError", "GateResult", "LifecycleError", "LifecycleService"]

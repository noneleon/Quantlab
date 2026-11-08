"""Optimization service (OPT-001, OPT-003, OPT-005, OPT-006): parameter sweeps and walk-forward analysis.

Every trial is an ordinary backtest submitted through ``BacktestService`` (no second simulator, ARCHITECTURE
§5.4):
it gets its own immutable run, snapshot, manifest and data provenance. The first trial runs alone and pins the
dataset version, so all trials of a study see exactly the same data; the rest may run in parallel.

Selection bias is reported, never hidden: every summary states how many trials were tried and carries the
Deflated
Sharpe Ratio of the selected trial, the Probability of Backtest Overfitting (CSCV) across trials and a
parameter
sensitivity analysis. Walk-forward keeps in-sample (IS) and out-of-sample (OOS) runs separate and labelled;
only OOS
runs form the walk-forward equity curve.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from quantlab.analytics.metrics import MetricsError, daily_series, equity_metrics, simple_returns
from quantlab.analytics.robustness import (
    RobustnessError,
    deflated_sharpe,
    probability_of_backtest_overfitting,
    sensitivity,
)
from quantlab.backtest.models import BacktestRun
from quantlab.backtest.service import BacktestError, BacktestService
from quantlab.core.clock import Clock, SystemClock
from quantlab.core.ids import new_id
from quantlab.core.logging import get_logger
from quantlab.data.schema import Provenance
from quantlab.optimize.models import Optimization, OptimizationTrial
from quantlab.optimize.objective import Constraint, Objective
from quantlab.optimize.space import Space
from quantlab.strategies.registry import StrategyRegistry, load_manifest

log = get_logger(__name__)
TRADE_KEYS = ("trades", "win_rate", "profit_factor", "expectancy", "net_pnl")


class OptimizationError(RuntimeError):
    pass


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class StudySpec:
    strategy: str
    space: Space
    objective: Objective
    constraints: tuple[Constraint, ...] = ()
    method: str = "grid"  # grid | random
    samples: int | None = None
    seed: int = 0
    fixed: dict[str, str] = field(default_factory=dict)
    dataset: str | None = None
    dataset_version: str | None = None
    models: dict[str, Any] | None = None
    max_parallel: int = 1
    # Further objectives (multi-objective TPE): the study then reports the Pareto front.
    extra_objectives: tuple[Objective, ...] = ()

    def points(self) -> list[dict[str, str]]:
        if self.method == "grid":
            return self.space.grid()
        if self.method == "tpe":
            return [{d.name: d.values[0] for d in self.space.dimensions}]  # proposals come from Optuna
        if self.method == "random":
            if not self.samples:
                raise OptimizationError("random search needs a number of samples")
            return self.space.sample(self.samples, self.seed)
        raise OptimizationError(f"unknown search method {self.method!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "space": self.space.to_dict(),
            "objective": self.objective.expression,
            "constraints": [c.text for c in self.constraints],
            "method": self.method,
            "samples": self.samples,
            "seed": self.seed,
            "fixed": self.fixed,
            "dataset": self.dataset,
            "dataset_version": self.dataset_version,
            "models": self.models,
            "max_parallel": self.max_parallel,
            "extra_objectives": [o.expression for o in self.extra_objectives],
        }


@dataclass(frozen=True)
class WalkForwardSpec:
    start: date
    end: date
    train_days: int
    test_days: int
    anchored: bool = False
    start_param: str = "start"
    end_param: str = "end"

    def windows(self) -> list[tuple[int, date, date, date, date]]:
        """(index, IS start, IS end, OOS start, OOS end); calendar days, inclusive bounds."""
        if self.train_days < 1 or self.test_days < 1:
            raise OptimizationError("train_days and test_days must be positive")
        out = []
        i = 0
        while True:
            is_start = self.start if self.anchored else self.start + timedelta(days=i * self.test_days)
            is_end = self.start + timedelta(days=self.train_days - 1 + i * self.test_days)
            oos_start = is_end + timedelta(days=1)
            oos_end = oos_start + timedelta(days=self.test_days - 1)
            if oos_end > self.end:
                break
            out.append((i, is_start, is_end, oos_start, oos_end))
            i += 1
        if not out:
            raise OptimizationError(
                f"no complete walk-forward window fits {self.start}..{self.end} "
                f"with {self.train_days} train + {self.test_days} test days"
            )
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "train_days": self.train_days,
            "test_days": self.test_days,
            "anchored": self.anchored,
            "start_param": self.start_param,
            "end_param": self.end_param,
        }


@dataclass
class _Trial:
    seq: int
    parameters: dict[str, str]
    window: int | None = None
    segment: str = "ALL"
    run: BacktestRun | None = None
    status: str = "pending"
    objective: float | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    returns: list[tuple[date, float]] = field(default_factory=list)


class OptimizationService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        backtests: BacktestService,
        registry: StrategyRegistry | None = None,
        clock: Clock | None = None,
        cancel: threading.Event | None = None,
    ) -> None:
        """``cancel`` (CORE-007 jobs): trials not yet started are skipped and the study ends "cancelled"."""
        self._cancel = cancel
        self._sessions = session_factory
        self._backtests = backtests
        self._registry = registry or StrategyRegistry()
        self._clock = clock or SystemClock()

    # ---- queries ----------------------------------------------------------------------------------
    def get(self, optimization_id: str) -> Optimization:
        with self._sessions() as session:
            opt = session.get(Optimization, optimization_id)
            if opt is None:
                raise OptimizationError(f"unknown optimization {optimization_id!r}")
            session.expunge(opt)
            return opt

    def recent(self, limit: int = 100) -> list[Optimization]:
        with self._sessions() as session:
            rows = list(
                session.execute(
                    select(Optimization).order_by(Optimization.created_at.desc()).limit(limit)
                ).scalars()
            )
            for r in rows:
                session.expunge(r)
            return rows

    def trials(self, optimization_id: str) -> list[OptimizationTrial]:
        with self._sessions() as session:
            rows = list(
                session.execute(
                    select(OptimizationTrial)
                    .where(OptimizationTrial.optimization_id == optimization_id)
                    .order_by(OptimizationTrial.seq)
                ).scalars()
            )
            for r in rows:
                session.expunge(r)
            return rows

    # ---- studies ----------------------------------------------------------------------------------
    def sweep(self, spec: StudySpec) -> Optimization:
        points = spec.points()
        self._check_parameters(spec, list(points[0]))
        if spec.extra_objectives and spec.method != "tpe":
            raise OptimizationError("multiple objectives need method 'tpe' (Optuna)")
        opt_id = self._start("sweep", spec.to_dict())
        try:
            if spec.method == "tpe":
                trials, pinned = self._run_tpe(opt_id, spec)
            else:
                trials = [_Trial(i, {**spec.fixed, **p}) for i, p in enumerate(points)]
                pinned = self._run_trials(opt_id, spec, trials, spec.dataset_version)
            summary = self._study_summary(spec, trials)
            summary["dataset_version"] = pinned
        except Exception as exc:
            self._finish(opt_id, "failed", None, str(exc))
            raise
        return self._finish(opt_id, self._final_status(), summary, None)

    def walk_forward(self, spec: StudySpec, wf: WalkForwardSpec) -> Optimization:
        windows = wf.windows()
        points = spec.points()
        self._check_parameters(spec, [*points[0], wf.start_param, wf.end_param])
        if wf.start_param in spec.space.to_dict() or wf.end_param in spec.space.to_dict():
            raise OptimizationError("the date parameters cannot be part of the search space")
        opt_id = self._start("walk_forward", {**spec.to_dict(), "walk_forward": wf.to_dict()})
        try:
            seq = 0
            pinned = spec.dataset_version
            window_rows = []
            oos_trials: list[_Trial] = []
            all_is: list[_Trial] = []
            for index, is_start, is_end, oos_start, oos_end in windows:
                dates_is = {wf.start_param: is_start.isoformat(), wf.end_param: is_end.isoformat()}
                is_trials = [
                    _Trial(seq + k, {**spec.fixed, **p, **dates_is}, index, "IS")
                    for k, p in enumerate(points)
                ]
                seq += len(is_trials)
                pinned = self._run_trials(opt_id, spec, is_trials, pinned)
                all_is.extend(is_trials)
                best = self._best(is_trials)
                row: dict[str, Any] = {
                    "window": index,
                    "is": [is_start.isoformat(), is_end.isoformat()],
                    "oos": [oos_start.isoformat(), oos_end.isoformat()],
                    "is_trials": len(is_trials),
                }
                if best is None:
                    row["status"] = "no valid in-sample trial"
                    window_rows.append(row)
                    continue
                chosen = {k: v for k, v in best.parameters.items() if k not in (wf.start_param, wf.end_param)}
                dates_oos = {wf.start_param: oos_start.isoformat(), wf.end_param: oos_end.isoformat()}
                oos = _Trial(seq, {**chosen, **dates_oos}, index, "OOS")
                seq += 1
                pinned = self._run_trials(opt_id, spec, [oos], pinned)
                oos.returns = [(d, r) for d, r in oos.returns if oos_start <= d <= oos_end]
                oos_trials.append(oos)
                row.update(
                    status=oos.status,
                    parameters={k: v for k, v in chosen.items() if k in spec.space.to_dict()},
                    is_objective=best.objective,
                    oos_objective=oos.objective,
                    is_cagr=best.metrics.get("cagr"),
                    oos_cagr=oos.metrics.get("cagr"),
                    is_run=best.run.id if best.run else None,
                    oos_run=oos.run.id if oos.run else None,
                )
                window_rows.append(row)
            summary = self._walk_forward_summary(spec, window_rows, oos_trials, all_is)
            summary["dataset_version"] = pinned
        except Exception as exc:
            self._finish(opt_id, "failed", None, str(exc))
            raise
        return self._finish(opt_id, self._final_status(), summary, None)

    # ---- internals --------------------------------------------------------------------------------
    def _check_parameters(self, spec: StudySpec, names: list[str]) -> None:
        with self._sessions() as session:
            strategy = self._registry.get(session, spec.strategy)
            manifest = load_manifest(strategy.directory)
        missing = sorted((set(names) | set(spec.fixed)) - set(manifest.parameters))
        if manifest.declared and missing:
            raise OptimizationError(
                f"strategy {spec.strategy!r} does not declare parameter(s) {missing} in strategy.toml "
                f"(declared: {sorted(manifest.parameters)})"
            )

    def _start(self, kind: str, spec: dict[str, Any]) -> str:
        opt_id = new_id()
        with self._sessions() as session:
            session.add(
                Optimization(
                    id=opt_id,
                    kind=kind,
                    strategy_name=spec["strategy"],
                    status="running",
                    created_at=self._clock.now(),
                    spec=_canonical(spec),
                )
            )
            session.commit()
        log.info("optimize.started", optimization_id=opt_id, kind=kind)
        return opt_id

    def _finish(
        self, opt_id: str, status: str, summary: dict[str, Any] | None, error: str | None
    ) -> Optimization:
        with self._sessions() as session:
            opt = session.get(Optimization, opt_id)
            assert opt is not None
            opt.status = status
            opt.finished_at = self._clock.now()
            opt.summary = _canonical(summary) if summary is not None else None
            opt.error = error
            session.commit()
        log.info("optimize.finished", optimization_id=opt_id, status=status)
        return self.get(opt_id)

    def _final_status(self) -> str:
        return "cancelled" if self._cancel is not None and self._cancel.is_set() else "succeeded"

    def _run_trials(
        self, opt_id: str, spec: StudySpec, trials: list[_Trial], pinned: str | None
    ) -> str | None:
        """Run trials (first alone to pin data + record the strategy version), persist each as it finishes."""

        def run_one(t: _Trial, version: str | None) -> _Trial:
            if self._cancel is not None and self._cancel.is_set():
                t.status, t.metrics = "cancelled", {}
                return t
            try:
                t.run = self._backtests.run(
                    spec.strategy,
                    t.parameters,
                    dataset=spec.dataset,
                    dataset_version=version,
                    models=spec.models,
                )
            except BacktestError as exc:
                t.status, t.metrics = "run_failed", {"error": str(exc)}
                return t
            self._score(t, spec)
            return t

        remaining = list(trials)
        # The first trial runs alone: it pins the dataset version and records the strategy version before any
        # parallel trial starts (record_version is also safe under races; this keeps the common path simple).
        if remaining and ((pinned is None and spec.dataset is not None) or spec.max_parallel > 1):
            first = run_one(remaining.pop(0), pinned)
            self._persist(opt_id, first)
            if pinned is None and spec.dataset is not None:
                pinned = first.run.data_version if first.run else None
        if spec.max_parallel > 1 and len(remaining) > 1:
            with ThreadPoolExecutor(max_workers=spec.max_parallel) as pool:
                for done in pool.map(lambda t: run_one(t, pinned), remaining):
                    self._persist(opt_id, done)
        else:
            for t in remaining:
                self._persist(opt_id, run_one(t, pinned))
        return pinned

    def _score(self, t: _Trial, spec: StudySpec) -> None:
        run = t.run
        assert run is not None
        if run.status != "succeeded":
            t.status, t.metrics = "run_failed", {"run_status": run.status, "error": run.error}
            return
        metrics: dict[str, Any] = dict(run.json("metrics") or {})
        try:
            charts = self._backtests.chart_data(run.id)["charts"]
            metrics.update({k: charts["trade_stats"].get(k) for k in TRADE_KEYS})
            series = daily_series(
                (datetime.fromisoformat(f"{d}T00:00:00+00:00"), float(v)) for d, v in charts["equity"]
            )
            rets = simple_returns([v for _, v in series])
            t.returns = [(series[i + 1][0], r) for i, r in enumerate(rets)]
        except (BacktestError, MetricsError) as exc:
            metrics["chart_error"] = str(exc)
        t.metrics = metrics
        t.objective = spec.objective.evaluate(metrics)
        if t.objective is None:
            t.status = "no_objective"
        elif not all(c.holds(metrics) for c in spec.constraints):
            t.status = "rejected"
        else:
            t.status = "ok"

    def _persist(self, opt_id: str, t: _Trial) -> None:
        keep = {
            k: t.metrics.get(k) for k in sorted(t.metrics) if not isinstance(t.metrics.get(k), dict | list)
        }
        with self._sessions() as session:
            session.add(
                OptimizationTrial(
                    id=new_id(),
                    optimization_id=opt_id,
                    seq=t.seq,
                    window=t.window,
                    segment=t.segment,
                    parameters=_canonical(t.parameters),
                    run_id=t.run.id if t.run else None,
                    status=t.status,
                    objective=t.objective,
                    metrics=_canonical(keep),
                    created_at=self._clock.now(),
                )
            )
            session.commit()

    def _run_tpe(self, opt_id: str, spec: StudySpec) -> tuple[list[_Trial], str | None]:
        """OPT-002: seeded Optuna TPE picks points of the same discrete space; trials are normal backtests."""
        try:
            import optuna
        except ImportError as exc:  # pragma: no cover - extra not installed
            raise OptimizationError(
                "method 'tpe' needs the 'optimize' extra: `uv sync --extra optimize`"
            ) from exc
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        if not spec.samples:
            raise OptimizationError("method 'tpe' needs a number of trials (samples)")
        objectives = (spec.objective, *spec.extra_objectives)
        study = optuna.create_study(
            directions=["maximize"] * len(objectives), sampler=optuna.samplers.TPESampler(seed=spec.seed)
        )
        trials: list[_Trial] = []
        seen: dict[str, _Trial] = {}
        pinned = spec.dataset_version
        attempts = 0
        while len(trials) < min(spec.samples, spec.space.size) and attempts < spec.samples * 5:
            attempts += 1
            proposal = study.ask()
            point = {
                d.name: proposal.suggest_categorical(d.name, list(d.values)) for d in spec.space.dimensions
            }
            key = _canonical(point)
            if key in seen:  # already evaluated: tell Optuna the known result, run nothing
                prior = seen[key]
                self._tell(study, proposal, prior, objectives)
                continue
            trial = _Trial(len(trials), {**spec.fixed, **point})
            pinned = self._run_trials(opt_id, spec, [trial], pinned)
            seen[key] = trial
            trials.append(trial)
            self._tell(study, proposal, trial, objectives)
        return trials, pinned

    @staticmethod
    def _tell(study: Any, proposal: Any, trial: _Trial, objectives: tuple[Objective, ...]) -> None:
        import optuna

        values = [o.evaluate(trial.metrics) for o in objectives]
        if trial.status != "ok" or any(v is None for v in values):
            study.tell(proposal, state=optuna.trial.TrialState.FAIL)
        else:
            study.tell(proposal, values if len(values) > 1 else values[0])

    @staticmethod
    def _best(trials: list[_Trial]) -> _Trial | None:
        ok = [t for t in trials if t.status == "ok" and t.objective is not None]
        return max(ok, key=lambda t: (t.objective, -t.seq)) if ok else None

    def _provenance_warnings(self, trials: list[_Trial]) -> list[str]:
        provenances = sorted({t.run.data_provenance for t in trials if t.run is not None})
        warnings = []
        for p in provenances:
            if not Provenance(p).is_real:
                warnings.append(f"trials ran on {p} data: not a performance claim")
        return warnings

    def _overfitting(self, trials: list[_Trial], best: _Trial | None) -> dict[str, Any]:
        out: dict[str, Any] = {}
        ok = [t for t in trials if t.status in ("ok", "rejected") and len(t.returns) >= 3]
        if best is not None and len(best.returns) >= 3 and ok:
            sharpes = []
            for t in ok:
                rs = [r for _, r in t.returns]
                mean = sum(rs) / len(rs)
                sd = (sum((r - mean) ** 2 for r in rs) / (len(rs) - 1)) ** 0.5
                sharpes.append(mean / sd if sd > 0 else 0.0)
            try:
                out["deflated_sharpe"] = deflated_sharpe([r for _, r in best.returns], sharpes).to_dict()
            except RobustnessError as exc:
                out["deflated_sharpe_error"] = str(exc)
        if len(ok) >= 2:
            common = sorted(set.intersection(*(set(d for d, _ in t.returns) for t in ok)))
            if len(common) >= 4:
                maps = [dict(t.returns) for t in ok]
                matrix = [[m[d] for m in maps] for d in common]
                try:
                    pbo = probability_of_backtest_overfitting(matrix)
                    out["pbo"] = {k: v for k, v in pbo.to_dict().items() if k != "logits"}
                except RobustnessError as exc:
                    out["pbo_error"] = str(exc)
            else:
                out["pbo_error"] = "trials share fewer than 4 dates"
        return out

    def _study_summary(self, spec: StudySpec, trials: list[_Trial]) -> dict[str, Any]:
        best = self._best(trials)
        pareto = None
        if spec.extra_objectives:
            objectives = (spec.objective, *spec.extra_objectives)
            evaluated = [(t, [o.evaluate(t.metrics) for o in objectives]) for t in trials if t.status == "ok"]
            vectors = [(t, [float(v) for v in vs if v is not None]) for t, vs in evaluated if None not in vs]
            pareto = [
                {**(_trial_view(t) or {}), "objectives": vs}
                for t, vs in vectors
                if not any(
                    all(a >= b for a, b in zip(other, vs, strict=True)) and other != vs
                    for _, other in vectors
                )
            ]
        ranked = sorted(
            (t for t in trials if t.status == "ok" and t.objective is not None),
            key=lambda t: (-(t.objective or 0.0), t.seq),
        )
        counts: dict[str, int] = {}
        for t in trials:
            counts[t.status] = counts.get(t.status, 0) + 1
        summary: dict[str, Any] = {
            "objective": spec.objective.expression,
            "trials": len(trials),
            "status_counts": counts,
            "best": _trial_view(best),
            "top": [_trial_view(t) for t in ranked[:10]],
            "pareto": pareto,
            "method": spec.method,
            "overfitting": self._overfitting(trials, best),
            "warnings": [
                *self._provenance_warnings(trials),
                f"best of {len(trials)} trials is an in-sample selection: judge it with the deflated Sharpe "
                "ratio, PBO and an out-of-sample (walk-forward) test",
            ],
        }
        varying = [d.name for d in spec.space.dimensions]
        scored: list[dict[str, Any]] = [
            {"parameters": {k: t.parameters[k] for k in varying}, "objective": t.objective}
            for t in trials
            if t.status == "ok"
        ]
        if scored:
            try:
                summary["sensitivity"] = sensitivity(scored)
            except RobustnessError as exc:
                summary["sensitivity_error"] = str(exc)
        return summary

    def _walk_forward_summary(
        self, spec: StudySpec, windows: list[dict[str, Any]], oos: list[_Trial], all_is: list[_Trial]
    ) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "objective": spec.objective.expression,
            "windows": windows,
            "is_trials": len(all_is),
            "oos_runs": len(oos),
            "warnings": self._provenance_warnings([*all_is, *oos]),
        }
        stitched: dict[date, float] = {}
        for t in oos:
            for d, r in t.returns:
                stitched.setdefault(d, r)
        if len(stitched) >= 2:
            equity, value = [], 1.0
            days = sorted(stitched)
            equity.append((datetime.fromisoformat(f"{days[0] - timedelta(days=1)}T00:00:00+00:00"), 1.0))
            for d in days:
                value *= 1 + stitched[d]
                equity.append((datetime.fromisoformat(f"{d}T00:00:00+00:00"), value))
            try:
                summary["oos_metrics"] = equity_metrics(equity).to_dict()
            except MetricsError as exc:
                summary["oos_metrics_error"] = str(exc)
            summary["oos_equity"] = [[t.date().isoformat(), v] for t, v in equity]
        pairs = [
            (w["is_cagr"], w["oos_cagr"])
            for w in windows
            if w.get("is_cagr") is not None and w.get("oos_cagr") is not None
        ]
        mean_is = sum(p[0] for p in pairs) / len(pairs) if pairs else None
        mean_oos = sum(p[1] for p in pairs) / len(pairs) if pairs else None
        summary["walk_forward_efficiency"] = (
            (mean_oos / mean_is) if mean_is and mean_is > 0 and mean_oos is not None else None
        )
        chosen = [_canonical(w.get("parameters")) for w in windows if "parameters" in w]
        summary["parameter_changes"] = sum(1 for a, b in pairwise(chosen) if a != b)
        return summary


def _trial_view(t: _Trial | None) -> dict[str, Any] | None:
    if t is None:
        return None
    return {
        "seq": t.seq,
        "parameters": t.parameters,
        "objective": t.objective,
        "run_id": t.run.id if t.run else None,
        "metrics": {
            k: t.metrics.get(k) for k in ("total_return", "cagr", "sharpe", "max_drawdown", "trades")
        },
    }

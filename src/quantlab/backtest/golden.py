"""Golden-result regression suite (Phase 3c, DEC-0024).

A *golden case* freezes the outcome of a known-good run: inputs hash, result hash, engine identity and key
metrics. ``check`` re-runs the source run from its stored snapshot, data version and execution models,
and compares:

* ``identical``        — same engine and byte-identical results (result hash) — the expected outcome;
* ``within_tolerance`` — results differ (e.g. engine upgrade allowed with ``allow_engine_change``) but every
  metric is within its absolute tolerance;
* ``failed``           — a metric moved beyond tolerance, or the same engine produced different results;
* ``engine_changed``   — the engine differs and ``allow_engine_change`` was not given (nothing was run);
* ``error``            — the rerun could not be performed or did not succeed.

Cases are JSON files (``<name>.json``) in ``QUANTLAB_HOME/golden`` by default or any directory (e.g. a repo
folder for CI with a real LEAN image). A case is never overwritten; record a new name instead.
Golden cases from synthetic or sample data are regression checks, never performance claims.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from quantlab.backtest.service import BacktestError, BacktestService, engine_identity

SCHEMA = "quantlab.golden/1"
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
METRICS = ("total_return", "cagr", "annual_volatility", "sharpe", "max_drawdown", "end_equity")
DEFAULT_TOLERANCE = {
    "total_return": 1e-6,
    "cagr": 1e-6,
    "annual_volatility": 1e-6,
    "sharpe": 1e-4,
    "max_drawdown": 1e-6,
    "end_equity": 0.01,
}


@dataclass(frozen=True)
class GoldenOutcome:
    name: str
    status: str
    source_run: str
    rerun_id: str | None = None
    differences: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in ("identical", "within_tolerance")

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ok": self.ok}


def record(service: BacktestService, run_id: str, name: str, directory: Path) -> Path:
    if not _NAME.match(name):
        raise BacktestError(f"invalid golden case name {name!r}: lowercase letters, digits, '-' or '_'")
    run = service.get(run_id)
    if run.status != "succeeded" or not run.result_hash:
        raise BacktestError(f"only succeeded runs can become golden cases (run {run_id} is {run.status})")
    metrics = run.json("metrics") or {}
    manifest = run.json("manifest")
    path = directory / f"{name}.json"
    if path.exists():
        raise BacktestError(
            f"golden case {name!r} already exists ({path}); golden cases are never overwritten"
        )
    case = {
        "schema": SCHEMA,
        "name": name,
        "source_run": run.id,
        "recorded_at": run.finished_at.isoformat() if run.finished_at else None,
        "strategy": manifest["strategy"]["name"],
        "inputs_hash": run.inputs_hash,
        "result_hash": run.result_hash,
        "engine": manifest["engine"]["identity"],
        "data_provenance": run.data_provenance,
        "data_version": run.data_version,
        "metrics": {k: metrics.get(k) for k in METRICS},
        "tolerance": DEFAULT_TOLERANCE,
        "note": "regression reference, not a performance claim",
    }
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(case, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load(directory: Path) -> list[dict[str, Any]]:
    cases = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        case = json.loads(path.read_text(encoding="utf-8"))
        if case.get("schema") != SCHEMA:
            raise BacktestError(f"{path} is not a golden case ({SCHEMA})")
        cases.append(case)
    return cases


def compare(
    case: dict[str, Any], metrics: dict[str, Any] | None, result_hash: str | None, same_engine: bool
) -> GoldenOutcome:
    diffs = []
    metrics = metrics or {}
    for key, expected in case["metrics"].items():
        actual = metrics.get(key)
        tol = float(case["tolerance"].get(key, 0.0))
        if expected is None or actual is None:
            if expected != actual:
                diffs.append(f"{key}: expected {expected}, got {actual}")
            continue
        if abs(float(actual) - float(expected)) > tol:
            diffs.append(f"{key}: expected {expected}, got {actual} (|diff| > {tol})")
    if result_hash == case["result_hash"]:
        return GoldenOutcome(case["name"], "identical", case["source_run"])
    if same_engine:
        diffs.insert(0, "same engine but different results (result hash changed)")
        return GoldenOutcome(case["name"], "failed", case["source_run"], differences=diffs)
    status = "failed" if diffs else "within_tolerance"
    return GoldenOutcome(case["name"], status, case["source_run"], differences=diffs)


def check(
    service: BacktestService,
    directory: Path,
    names: list[str] | None = None,
    *,
    allow_engine_change: bool = False,
    timeout_s: float | None = None,
) -> list[GoldenOutcome]:
    cases = load(directory)
    if names:
        unknown = sorted(set(names) - {c["name"] for c in cases})
        if unknown:
            raise BacktestError(f"unknown golden case(s) {unknown} in {directory}")
        cases = [c for c in cases if c["name"] in names]
    current = engine_identity(service.probe_engine())
    outcomes = []
    for case in cases:
        same_engine = current == case["engine"]
        if not same_engine and not allow_engine_change:
            outcomes.append(
                GoldenOutcome(
                    case["name"],
                    "engine_changed",
                    case["source_run"],
                    differences=[f"engine {current} differs from recorded {case['engine']}"],
                )
            )
            continue
        try:
            rerun, _ = service.rerun(case["source_run"], timeout_s, require_same_engine=False)
        except BacktestError as exc:
            outcomes.append(GoldenOutcome(case["name"], "error", case["source_run"], differences=[str(exc)]))
            continue
        if rerun.status != "succeeded":
            outcomes.append(
                GoldenOutcome(
                    case["name"],
                    "error",
                    case["source_run"],
                    rerun.id,
                    [f"rerun {rerun.status}: {rerun.error}"],
                )
            )
            continue
        if rerun.inputs_hash != case["inputs_hash"] and same_engine:
            outcomes.append(
                GoldenOutcome(case["name"], "failed", case["source_run"], rerun.id, ["inputs hash changed"])
            )
            continue
        outcome = compare(case, rerun.json("metrics"), rerun.result_hash, same_engine)
        outcomes.append(
            GoldenOutcome(outcome.name, outcome.status, outcome.source_run, rerun.id, outcome.differences)
        )
    return outcomes

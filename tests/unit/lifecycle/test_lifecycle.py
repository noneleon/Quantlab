"""Lifecycle gates (STR-004) on the fake engine.

Fake runs are 'sample' data; tests that exercise the passing path switch off require_real_data explicitly
(synthetic data is never labelled real)."""

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from quantlab.core.config import load_settings
from quantlab.lifecycle.service import LifecycleService
from quantlab.optimize.objective import Objective
from quantlab.optimize.service import OptimizationService, StudySpec, WalkForwardSpec
from quantlab.optimize.space import Space
from quantlab.strategies.lifecycle import LifecycleError, LifecycleEvent, allowed

LENIENT = {"require_real_data": False, "min_dsr_probability": 0.0, "max_pbo": 1.0, "min_oos_sharpe": -99.0}


@pytest.fixture
def lc(env, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_SYNTH", "1")
    (env["strategy_dir"] / "strategy.toml").write_text(
        '[parameters]\nfast = "10"\nslow = "30"\nstart = "2020-01-01"\nend = "2020-12-31"\n'
    )
    backtests = env["service"]()
    optimizer = OptimizationService(env["sessions"], backtests)

    def service(**gates):
        settings = load_settings(overrides={"lifecycle": gates}) if gates else env["settings"]
        return LifecycleService(settings, env["sessions"], backtests, optimizer)

    return service, backtests, optimizer, env


def test_state_machine():
    assert allowed("research", "backtested") and not allowed("research", "validated")
    assert allowed("validated", "research") and allowed("paper", "retired") and allowed("retired", "research")
    assert (
        not allowed("retired", "backtested") and not allowed("research", "research") and not allowed("x", "y")
    )


def test_sample_data_never_promotes_by_default(lc):
    service, backtests, _, _ = lc
    run = backtests.run("sma")
    result, entry = service().promote("sma", "backtested", reason="first run", runs=[run.id])
    assert not result.passed and entry is None
    assert any(c["check"].endswith("data") and not c["ok"] for c in result.checks)


def test_full_path_with_evidence_staleness_and_blocked_live(lc):
    service, backtests, optimizer, env = lc
    svc = service(**LENIENT)
    assert svc.status("sma")["stage"] == "research"
    with pytest.raises(LifecycleError, match="reason"):
        svc.promote("sma", "backtested", reason=" ", runs=[backtests.run("sma").id])

    run = backtests.run("sma")
    result, entry = svc.promote("sma", "backtested", reason="baseline", runs=[run.id])
    assert result.passed and entry.seq == 1

    spec = StudySpec("sma", Space.parse(["fast=6,10,14"]), Objective.parse("sharpe"))
    sweep = optimizer.sweep(spec)
    wf = optimizer.walk_forward(spec, WalkForwardSpec(date(2020, 1, 1), date(2020, 12, 31), 120, 60))
    no_sweep, _ = svc.promote("sma", "validated", reason="wf", walk_forward=wf.id)
    assert not no_sweep.passed and any(c["check"] == "sweep evidence" for c in no_sweep.checks)
    strict = service(require_real_data=False, min_oos_windows=99, max_pbo=0.0)
    failed = strict.evaluate("sma", "validated", walk_forward=wf.id, sweep=sweep.id)
    assert not failed.passed
    assert {c["check"] for c in failed.checks if not c["ok"]} >= {"OOS windows"}
    result, entry = svc.promote("sma", "validated", reason="wf + sweep", walk_forward=wf.id, sweep=sweep.id)
    assert result.passed, result.checks
    assert entry.json("evidence")["gates"]["min_dsr_probability"] == 0.0  # the rules applied are recorded

    (env["strategy_dir"] / "main.py").write_text("# algo v2\n")  # code changed after validation
    st = svc.status("sma")
    assert st["stage"] == "validated" and st["stale"] and "re-validate" in st["stale_note"]
    stale, _ = svc.promote("sma", "paper", reason="go paper")
    assert not stale.passed
    (env["strategy_dir"] / "main.py").write_text("# algo v1\n")
    result, entry = svc.promote("sma", "paper", reason="approved for paper")
    assert result.passed and any("Phase 5" in c["detail"] for c in result.checks)

    blocked, entry = svc.promote("sma", "live_candidate", reason="try")
    assert not blocked.passed and entry is None and "Phase 5" in blocked.checks[0]["detail"]

    _, demoted = svc.promote("sma", "research", reason="material change")
    assert demoted.to_stage == "research" and svc.status("sma")["next_stage"] == "backtested"
    assert [e["to"] for e in svc.status("sma")["history"]] == ["backtested", "validated", "paper", "research"]


def test_evidence_from_other_versions_or_strategies_is_refused(lc):
    service, backtests, _, env = lc
    svc = service(**LENIENT)
    old = backtests.run("sma")
    (env["strategy_dir"] / "main.py").write_text("# algo v3\n")
    result, _ = svc.promote("sma", "backtested", reason="old run", runs=[old.id])
    assert not result.passed and any("older files" in c["detail"] for c in result.checks)
    result, _ = svc.promote("sma", "backtested", reason="no such run", runs=["NOPE"])
    assert not result.passed
    result, _ = svc.promote("sma", "validated", reason="skip ahead")
    assert not result.passed and "not a forward step" in result.checks[0]["detail"]


def test_history_is_append_only(lc):
    service, backtests, _, env = lc
    svc = service(**LENIENT)
    svc.promote("sma", "backtested", reason="x", runs=[backtests.run("sma").id])
    with env["sessions"]() as s:
        e = s.query(LifecycleEvent).first()
        e.reason = "rewritten"
        with pytest.raises(LifecycleError):
            s.flush()
    with env["engine"].connect() as conn, pytest.raises(DatabaseError):
        conn.execute(text("DELETE FROM strategy_lifecycle_events"))

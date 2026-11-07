"""OptimizationService against the fake engine in synthetic mode (FAKE_DOCKER_SYNTH) — synthetic numbers
only."""

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from quantlab.optimize.models import OptimizationRecordError, OptimizationTrial
from quantlab.optimize.objective import Constraint, Objective
from quantlab.optimize.service import OptimizationError, OptimizationService, StudySpec, WalkForwardSpec
from quantlab.optimize.space import Space
from tests.unit.backtest.conftest import IMAGE  # noqa: F401


@pytest.fixture
def opt(env, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_SYNTH", "1")
    (env["strategy_dir"] / "strategy.toml").write_text(
        '[parameters]\nfast = "10"\nslow = "30"\nstart = "2020-01-01"\nend = "2020-12-31"\n'
    )
    return OptimizationService(env["sessions"], env["service"]()), env


def spec(**kw):
    base = dict(strategy="sma", space=Space.parse(["fast=4,10,16"]), objective=Objective.parse("sharpe"))
    return StudySpec(**{**base, **kw})


def test_grid_sweep_selects_best_and_reports_selection_bias(opt):
    service, env = opt
    result = service.sweep(spec())
    s = result.json("summary")
    assert result.status == "succeeded" and s["trials"] == 3 and s["status_counts"] == {"ok": 3}
    assert s["best"]["parameters"]["fast"] == "10"  # synthetic drift peaks at fast=10
    assert s["top"][0]["parameters"]["fast"] == "10"
    dsr = s["overfitting"]["deflated_sharpe"]
    assert dsr["trials"] == 3 and 0 <= dsr["probability"] <= 1
    assert 0 <= s["overfitting"]["pbo"]["pbo"] <= 1
    assert s["sensitivity"]["best"]["parameters"] == {"fast": "10"}
    assert any("sample" in w or "not a performance claim" in w for w in s["warnings"])
    assert any("in-sample selection" in w for w in s["warnings"])
    trials = service.trials(result.id)
    assert len(trials) == 3 and all(t.run_id for t in trials)
    assert {env["service"]().get(t.run_id).json("parameters")["fast"] for t in trials} == {"4", "10", "16"}


def test_trials_are_append_only(opt):
    service, env = opt
    result = service.sweep(spec(space=Space.parse(["fast=10"])))
    with env["sessions"]() as session:
        trial = session.query(OptimizationTrial).first()
        trial.objective = 99.0
        with pytest.raises(OptimizationRecordError):
            session.flush()
    with env["engine"].connect() as conn, pytest.raises(DatabaseError):
        conn.execute(text("DELETE FROM optimization_trials"))
    with env["sessions"]() as session, pytest.raises(OptimizationRecordError):
        session.delete(session.get(type(result), result.id))
        session.flush()


def test_random_search_and_constraints(opt):
    service, _ = opt
    result = service.sweep(
        spec(
            space=Space.parse(["fast=2:18:2"]),
            method="random",
            samples=4,
            seed=3,
            constraints=(Constraint.parse("total_return >= 100"),),  # impossible: every trial rejected
        )
    )
    s = result.json("summary")
    assert s["trials"] == 4 and s["status_counts"] == {"rejected": 4} and s["best"] is None
    with pytest.raises(OptimizationError, match="samples"):
        service.sweep(spec(method="random"))


def test_parallel_trials(opt):
    service, _ = opt
    result = service.sweep(spec(space=Space.parse(["fast=6,8,10,12"]), max_parallel=2))
    assert result.json("summary")["status_counts"] == {"ok": 4}


def test_undeclared_parameters_are_refused(opt):
    service, _ = opt
    with pytest.raises(OptimizationError, match="does not declare"):
        service.sweep(spec(space=Space.parse(["lookback=5,10"])))


def test_walk_forward_rolling_is_oos_separation(opt):
    service, env = opt
    wf = WalkForwardSpec(date(2020, 1, 1), date(2020, 12, 31), train_days=120, test_days=60)
    windows = wf.windows()
    assert len(windows) == 4 and windows[0][1:] == (
        date(2020, 1, 1),
        date(2020, 4, 29),
        date(2020, 4, 30),
        date(2020, 6, 28),
    )
    assert windows[1][1] == date(2020, 3, 1)  # rolling: IS start moves by test_days
    anchored = WalkForwardSpec(date(2020, 1, 1), date(2020, 12, 31), 120, 60, anchored=True).windows()
    assert {w[1] for w in anchored} == {date(2020, 1, 1)} and anchored[1][2] == date(2020, 6, 28)

    result = service.walk_forward(spec(), wf)
    s = result.json("summary")
    assert (
        result.status == "succeeded"
        and len(s["windows"]) == 4
        and s["oos_runs"] == 4
        and s["is_trials"] == 12
    )
    w0 = s["windows"][0]
    assert w0["parameters"] == {"fast": "10"} and w0["oos"] == ["2020-04-30", "2020-06-28"]
    oos_run = env["service"]().get(w0["oos_run"])
    assert (
        oos_run.json("parameters")["start"] == "2020-04-30"
        and oos_run.json("parameters")["end"] == "2020-06-28"
    )
    assert s["oos_equity"][1][0] >= "2020-04-30" and s["oos_metrics"]["observations"] > 100
    segments = {t.segment for t in service.trials(result.id)}
    assert segments == {"IS", "OOS"}
    with pytest.raises(OptimizationError, match="date parameters"):
        service.walk_forward(spec(space=Space.parse(["start=2020-01-01"])), wf)
    with pytest.raises(OptimizationError, match="no complete walk-forward window"):
        WalkForwardSpec(date(2020, 1, 1), date(2020, 2, 1), 120, 60).windows()

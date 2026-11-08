"""Golden-result regression suite against the fake engine (synthetic results, no real LEAN)."""

import json

import pytest

from quantlab.backtest import golden
from quantlab.backtest.service import BacktestError
from tests.unit.backtest.conftest import IMAGE, OTHER_IMAGE


def test_record_and_identical_check(env, tmp_path):
    service = env["service"]()
    run = service.run("sma")
    path = golden.record(service, run.id, "sma-sample", tmp_path / "golden")
    case = json.loads(path.read_text())
    assert case["result_hash"] == run.result_hash and case["engine"]["image"] == IMAGE
    assert case["metrics"]["total_return"] == pytest.approx(run.json("metrics")["total_return"])
    with pytest.raises(BacktestError, match="never overwritten"):
        golden.record(service, run.id, "sma-sample", tmp_path / "golden")
    with pytest.raises(BacktestError, match="invalid golden case name"):
        golden.record(service, run.id, "Bad Name", tmp_path / "golden")

    (outcome,) = golden.check(service, tmp_path / "golden")
    assert outcome.status == "identical" and outcome.ok and outcome.rerun_id
    assert service.get(outcome.rerun_id).rerun_of == run.id


def test_same_engine_different_result_fails(env, tmp_path, monkeypatch):
    service = env["service"]()
    run = service.run("sma")
    golden.record(service, run.id, "case", tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_EQUITY_SHIFT", "50")
    (outcome,) = golden.check(service, tmp_path)
    assert outcome.status == "failed" and not outcome.ok
    assert "result hash changed" in outcome.differences[0] and any(
        "end_equity" in d for d in outcome.differences
    )


def test_engine_change_reported_or_checked_with_tolerance(env, tmp_path, monkeypatch):
    run = env["service"]().run("sma")
    golden.record(env["service"](), run.id, "case", tmp_path)
    upgraded = env["service"](OTHER_IMAGE)
    (outcome,) = golden.check(upgraded, tmp_path)
    assert outcome.status == "engine_changed" and outcome.rerun_id is None
    (outcome,) = golden.check(upgraded, tmp_path, allow_engine_change=True)
    assert outcome.status in ("identical", "within_tolerance")  # fake engine: same numbers on the new image
    monkeypatch.setenv("FAKE_DOCKER_EQUITY_SHIFT", "50")
    (outcome,) = golden.check(upgraded, tmp_path, allow_engine_change=True)
    assert outcome.status == "failed"


def test_only_succeeded_runs_and_unknown_names(env, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_EXIT", "3")
    failed = env["service"]().run("sma")
    with pytest.raises(BacktestError, match="only succeeded runs"):
        golden.record(env["service"](), failed.id, "x", tmp_path)
    with pytest.raises(BacktestError, match="unknown golden case"):
        golden.check(env["service"](), tmp_path, ["missing"])

"""CLI tests, including the Milestone M1 flow end to end against the fake engine (synthetic results).

`--json` output is parsed from stdout only; logs go to stderr.
"""

import json

import pytest
from typer.testing import CliRunner

from quantlab.cli.main import app
from tests.fixtures.m1 import EXAMPLE_STRATEGY, configure_fake_engine

runner = CliRunner()


def ql(*args):
    return runner.invoke(app, [str(a) for a in args], catch_exceptions=False)


@pytest.fixture
def home(ql_home, memory_keyring, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(tmp_path / "fake"))
    r = ql("init")
    assert r.exit_code == 0, r.output
    configure_fake_engine(ql_home)
    return ql_home


def test_commands_before_init_fail_cleanly(ql_home):
    r = ql("strategy", "list")
    assert r.exit_code == 1 and "quantlab init" in r.output


def test_init_output_and_idempotency(ql_home, memory_keyring):
    first = ql("init")
    assert first.exit_code == 0 and "created quantlab.toml" in first.output and "OS keyring" in first.output
    second = ql("init")
    assert "existing quantlab.toml kept" in second.output and "already configured" in second.output


def test_m1_flow(home):
    assert ql("strategy", "add", "sma", EXAMPLE_STRATEGY).exit_code == 0
    listed = json.loads(ql("strategy", "list", "--json").stdout)
    assert [s["name"] for s in listed] == ["sma"]
    shown = json.loads(ql("strategy", "show", "sma", "--json").stdout)
    assert sorted(shown["current_version"]["files"]) == ["main.py", "strategy.toml"]

    run = ql("backtest", "run", "sma", "-p", "fast=5", "--json")
    assert run.exit_code == 0, run.output
    run_id = json.loads(run.stdout)["id"]

    text = ql("backtest", "show", run_id).output
    assert "succeeded" in text and "sample" in text and "total_return" in text
    detail = json.loads(ql("backtest", "show", run_id, "--json").stdout)
    assert detail["manifest"]["parameters"] == {"fast": "5", "slow": "30"}

    rerun = ql("backtest", "rerun", run_id, "--json")
    assert rerun.exit_code == 0, rerun.output
    report = json.loads(rerun.stdout)
    assert report["reproduced"] and report["rerun"]["rerun_of"] == run_id

    history = json.loads(ql("backtest", "list", "--json").stdout)
    assert len(history) == 2 and {h["id"] for h in history} >= {run_id}


def test_backtest_failure_exit_code(home, monkeypatch):
    ql("strategy", "add", "sma", EXAMPLE_STRATEGY)
    monkeypatch.setenv("FAKE_DOCKER_EXIT", "4")
    r = ql("backtest", "run", "sma")
    assert r.exit_code == 2 and "failed" in r.output


def test_bad_inputs(home):
    assert ql("backtest", "run", "missing").exit_code == 1
    ql("strategy", "add", "sma", EXAMPLE_STRATEGY)
    r = ql("backtest", "run", "sma", "-p", "novalue")
    assert r.exit_code == 1 and "key=value" in r.output
    r = ql("backtest", "run", "sma", "-p", "typo=1")
    assert r.exit_code == 1 and "unknown parameter" in r.output
    assert ql("backtest", "show", "nope").exit_code == 1
    assert ql("strategy", "add", "sma", EXAMPLE_STRATEGY).exit_code == 1  # already registered


def test_archive(home):
    ql("strategy", "add", "sma", EXAMPLE_STRATEGY)
    assert ql("strategy", "archive", "sma").exit_code == 0
    assert "no strategies registered" in ql("strategy", "list").output
    assert "[archived]" in ql("strategy", "list", "--all").output


def test_engine_probe(home):
    r = ql("engine", "probe", "--json")
    assert r.exit_code == 0 and json.loads(r.stdout)["image_id"] == "sha256:" + "b" * 64


def test_engine_probe_without_pinned_image(ql_home, memory_keyring):
    ql("init")
    r = ql("engine", "probe")
    assert r.exit_code == 1 and "engine.lean.image is not set" in r.output


def test_api_token_commands(home):
    assert "configured" in ql("api", "token").output
    token = ql("api", "token", "--show").stdout.strip()
    assert len(token) >= 40
    ql("api", "token", "--rotate")
    assert ql("api", "token", "--show").stdout.strip() != token


def test_serve_refuses_non_loopback_without_remote_access(home):
    r = ql("serve", "--host", "0.0.0.0")
    assert r.exit_code == 1 and "remote_access" in r.output


def test_backtest_compare(home):
    ql("strategy", "add", "sma", EXAMPLE_STRATEGY)
    a = json.loads(ql("backtest", "run", "sma", "--json").stdout)["id"]
    b = json.loads(ql("backtest", "run", "sma", "-p", "fast=5", "--json").stdout)["id"]
    report = json.loads(ql("backtest", "compare", a, b, "--json").stdout)
    assert report["same_strategy_code"] and not report["same_parameters"]
    assert report["metrics"]["total_return"]["diff"] == 0.0  # fake engine: identical synthetic results
    assert report["a"]["provenance"] == "sample"
    text = ql("backtest", "compare", a, b).stdout
    assert "not a like-for-like comparison" in text and "total_return" in text


def test_backtest_report(home):
    from pathlib import Path

    ql("strategy", "add", "sma", EXAMPLE_STRATEGY)
    run_id = json.loads(ql("backtest", "run", "sma", "--json").stdout)["id"]
    out = json.loads(ql("backtest", "report", run_id, "--json").stdout)
    assert Path(out["bundle"]).is_file() and Path(out["html"]).is_file() and out["warnings"]
    text = ql("backtest", "report", run_id).stdout
    assert "report:" in text and "warning:" in text
    r = ql("backtest", "report", run_id, "--benchmark", "missing")
    assert r.exit_code == 1 and "missing" in r.output


def test_backtest_run_with_models(home):
    ql("strategy", "add", "sma", EXAMPLE_STRATEGY)
    r = ql("backtest", "run", "sma", "-m", "fee=zero", "-m", "account_type=cash")
    assert r.exit_code == 0 and "models     account_type=cash, fee=zero" in r.stdout
    assert "did not call quantlab_lean.configure" in r.stdout  # example strategy does not opt in yet
    r = ql("backtest", "run", "sma", "-m", "fee=constant")
    assert r.exit_code == 1 and "fee_per_order > 0" in r.output


@pytest.mark.parametrize("name", ["momentum_rotation", "rsi_mean_reversion", "pairs_zscore"])
def test_templates_run_on_fake_engine_and_apply_models(home, name):
    from tests.fixtures.m1 import ROOT

    ql("strategy", "add", name, ROOT / "strategies" / "templates" / name)
    r = ql("backtest", "run", name, "-m", "fee=constant", "-m", "fee_per_order=1")
    assert r.exit_code == 0 and "models     fee=constant, fee_per_order=1.0" in r.stdout
    assert "did not call quantlab_lean.configure" not in r.stdout


def test_golden_cli(home, monkeypatch):
    ql("strategy", "add", "sma", EXAMPLE_STRATEGY)
    run_id = json.loads(ql("backtest", "run", "sma", "--json").stdout)["id"]
    assert "golden case sma-ref" in ql("backtest", "golden", "record", run_id, "sma-ref").stdout
    assert "sma-ref" in ql("backtest", "golden", "list").stdout
    r = ql("backtest", "golden", "check")
    assert r.exit_code == 0 and "identical" in r.stdout
    monkeypatch.setenv("FAKE_DOCKER_EQUITY_SHIFT", "25")
    r = ql("backtest", "golden", "check", "sma-ref")
    assert r.exit_code == 1 and "failed" in r.stdout and "result hash changed" in r.stdout


def _optimizable(tmp_path):
    import shutil

    target = tmp_path / "opt-strategy"
    shutil.copytree(EXAMPLE_STRATEGY, target)
    (target / "strategy.toml").write_text(
        '[strategy]\nentry_file = "main.py"\n\n[parameters]\nfast = 10\nslow = 30\n'
        'start = "2020-01-01"\nend = "2020-12-31"\n'
    )
    return target


def test_optimize_cli(home, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_SYNTH", "1")
    ql("strategy", "add", "opt", _optimizable(tmp_path))
    r = ql(
        "optimize", "sweep", "opt", "-s", "fast=6,10,14", "--objective", "sharpe", "-c", "max_drawdown <= 0.9"
    )
    assert r.exit_code == 0, r.output
    assert "best     {'fast': '10'" in r.stdout and "deflated Sharpe" in r.stdout and "PBO" in r.stdout
    assert "in-sample selection" in r.stdout
    opt_id = r.stdout.split()[1]
    assert opt_id in ql("optimize", "list").stdout
    shown = json.loads(ql("optimize", "show", opt_id, "--json").stdout)
    assert len(shown["trials"]) == 3 and shown["spec"]["constraints"] == ["max_drawdown <= 0.9"]

    r = ql(
        "optimize",
        "walk-forward",
        "opt",
        "-s",
        "fast=6,10",
        "--start",
        "2020-01-01",
        "--end",
        "2020-12-31",
        "--train-days",
        "150",
        "--test-days",
        "90",
    )
    assert r.exit_code == 0, r.output
    assert "window 0" in r.stdout and "OOS (stitched)" in r.stdout and "walk-forward efficiency" in r.stdout

    assert ql("optimize", "sweep", "opt", "-s", "fast").exit_code == 1
    r = ql("optimize", "sweep", "opt", "-s", "fast=1,2", "--objective", "__import__('os')")
    assert r.exit_code == 1 and "not allowed" in r.output


def test_backtest_robustness_cli(home, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_SYNTH", "1")
    ql("strategy", "add", "opt", _optimizable(tmp_path))
    run_id = json.loads(ql("backtest", "run", "opt", "--json").stdout)["id"]
    r = ql("backtest", "robustness", run_id, "--samples", "50", "--seed", "1")
    assert r.exit_code == 0 and "bootstrap (50 paths" in r.stdout and "P(loss)" in r.stdout
    assert "trade Monte Carlo" in r.stdout


def test_optimize_tpe_cli(home, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_SYNTH", "1")
    ql("strategy", "add", "opt", _optimizable(tmp_path))
    r = ql(
        "optimize",
        "sweep",
        "opt",
        "-s",
        "fast=4:16:2",
        "--tpe",
        "4",
        "--seed",
        "5",
        "--extra-objective",
        "-max_drawdown",
    )
    assert r.exit_code == 0, r.output
    assert "pareto" in r.stdout and "trials 4" in r.stdout
    assert ql("optimize", "sweep", "opt", "-s", "fast=4,6", "--tpe", "2", "--random", "2").exit_code == 1

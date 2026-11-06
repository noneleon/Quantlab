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

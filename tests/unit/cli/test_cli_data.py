"""`quantlab data …` end to end (synthetic fixture data, provenance=synthetic)."""

import json

import pytest
from typer.testing import CliRunner

from quantlab.cli.main import app
from tests.unit.data.conftest import SESSIONS, csv_text, daily_rows

runner = CliRunner()


def ql(*args):
    return runner.invoke(app, [str(a) for a in args], catch_exceptions=False)


CREATE = [
    "data",
    "create",
    "spy",
    "--asset-class",
    "equity",
    "--market",
    "usa",
    "--resolution",
    "daily",
    "--provenance",
    "synthetic",
    "--adjustment",
    "raw",
    "--source",
    "test fixture",
]


@pytest.fixture
def home(ql_home, memory_keyring):
    assert ql("init").exit_code == 0
    return ql_home


def test_create_import_show_head_export(home, tmp_path):
    r = ql(*CREATE)
    assert r.exit_code == 0 and "calendar XNYS" in r.output
    csv = tmp_path / "spy.csv"
    csv.write_text(csv_text(daily_rows([d for d in SESSIONS if d != "2024-07-05"])))
    r = ql("data", "import", "spy", csv, "--symbol", "SPY", "--json")
    assert r.exit_code == 0, r.output
    summary = json.loads(r.stdout)
    assert (
        summary["created"] and summary["rows"] == 5 and summary["warnings"] == ["missing_sessions: 1 (SPY)"]
    )

    shown = json.loads(ql("data", "show", "spy", "--json").stdout)
    assert shown["provenance"] == "synthetic" and len(shown["versions"]) == 1
    assert shown["latest_validation"]["completeness"]["SPY"] == pytest.approx(5 / 6, abs=1e-6)
    assert "SPY" in ql("data", "head", "spy", "-n", "2").output
    assert json.loads(ql("data", "list", "--json").stdout)[0]["rows"] == 5

    r = ql("data", "export", "spy", tmp_path / "out")
    assert r.exit_code == 1 and "licence" in r.output
    r = ql("data", "export", "spy", tmp_path / "out", "--acknowledge-license")
    assert r.exit_code == 0 and (tmp_path / "out" / "SPY.csv").is_file()


def test_invalid_import_reports_and_quarantines(home, tmp_path):
    ql(*CREATE)
    rows = daily_rows()
    rows[0] = "2024-07-01,100,90,95,101,0,10"
    csv = tmp_path / "bad.csv"
    csv.write_text(csv_text(rows))
    r = ql("data", "import", "spy", csv, "--symbol", "SPY")
    assert r.exit_code == 1 and "ohlc_consistency" in r.output and "quarantined" in r.output
    assert json.loads(ql("data", "list", "--json").stdout)[0]["rows"] == 0


def test_provenance_is_required(home):
    r = runner.invoke(app, [a for a in CREATE if a not in ("--provenance", "synthetic")])
    assert r.exit_code != 0 and "provenance" in r.output.lower()


def test_unknown_dataset_and_bad_options(home, tmp_path):
    assert ql("data", "show", "nope").exit_code == 1
    ql(*CREATE)
    assert ql("data", "import", "spy", tmp_path / "missing.csv", "--symbol", "SPY").exit_code == 1
    assert ql("data", "import", "spy", tmp_path / "x.csv", "--on-conflict", "merge").exit_code == 1
    assert ql("data", "export", "spy", tmp_path / "o", "--format", "xlsx").exit_code == 1


def test_backtest_on_dataset_via_cli(home, tmp_path, monkeypatch):
    from tests.fixtures.m1 import EXAMPLE_STRATEGY, configure_fake_engine

    monkeypatch.setenv("FAKE_DOCKER_STATE", str(tmp_path / "fake"))
    configure_fake_engine(home)
    ql(*CREATE)
    csv = tmp_path / "spy.csv"
    csv.write_text(csv_text(daily_rows()))
    ql("data", "import", "spy", csv, "--symbol", "SPY")
    assert "export" in ql("data", "lean-export", "spy").output
    ql("strategy", "add", "sma", EXAMPLE_STRATEGY)
    r = ql("backtest", "run", "sma", "--dataset", "spy", "--json")
    assert r.exit_code == 0, r.output
    summary = json.loads(r.stdout)
    assert summary["data_provenance"] == "synthetic" and summary["data_version"]
    shown = ql("backtest", "show", summary["id"]).output
    assert "spy @" in shown and "not a performance claim" in shown


def test_example_daily_strategy_manifest():
    from pathlib import Path

    from quantlab.strategies.registry import load_manifest

    m = load_manifest(Path(__file__).parents[3] / "strategies" / "examples" / "sma_daily")
    assert m.parameters["symbol"] == "SPY" and m.parameters["slow"] == "50"


def test_import_with_actions_and_adjusted_head(home, tmp_path):
    ql(*CREATE)
    csv = tmp_path / "spy.csv"
    csv.write_text(csv_text(daily_rows()))
    actions = tmp_path / "actions.csv"
    actions.write_text("ex_date,kind,value\n2024-07-05,split,2\n")
    r = ql("data", "import", "spy", csv, "--symbol", "SPY", "--actions", actions, "--json")
    assert r.exit_code == 0, r.output
    assert any("1 corporate action" in n for n in json.loads(r.stdout)["notes"])
    listed = json.loads(ql("data", "actions", "spy", "--json").stdout)
    assert listed == [{"symbol": "SPY", "ex_date": "2024-07-05", "kind": "split", "value": 2.0}]
    raw = ql("data", "head", "spy", "-n", "1").stdout
    adjusted = ql("data", "head", "spy", "-n", "1", "--adjust", "split").stdout
    assert "101.0" in raw and "50.5" in adjusted and "(split prices)" in adjusted
    assert ql("data", "head", "spy", "--adjust", "total").exit_code == 1

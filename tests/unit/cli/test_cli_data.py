"""`quantlab data …` end to end (synthetic fixture data, provenance=synthetic)."""

import json

import pytest
from typer.testing import CliRunner

from quantlab.cli.main import app
from tests.unit.data.conftest import SESSIONS, csv_text, daily_rows

runner = CliRunner()


def ql(*args):
    return runner.invoke(app, [str(a) for a in args], catch_exceptions=False)


def ql_input(stdin, *args):
    return runner.invoke(app, [str(a) for a in args], input=stdin, catch_exceptions=False)


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


def test_secret_commands_never_print_values(home):
    r = runner.invoke(app, ["secret", "set", "tiingo/api-key"], input="super-secret-value\n")
    assert r.exit_code == 0 and "super-secret-value" not in r.output and "OS keyring" in r.output
    assert "configured" in ql("secret", "status", "tiingo/api-key").output
    assert ql("secret", "delete", "tiingo/api-key").exit_code == 0
    assert "not configured" in ql("secret", "status", "tiingo/api-key").output
    assert ql("secret", "delete", "tiingo/api-key").exit_code == 1
    assert ql("secret", "set", "Bad Name").exit_code == 1


def test_data_fetch_tiingo_via_cli(home, monkeypatch):
    import httpx

    from tests.unit.data.test_tiingo import PAYLOAD

    assert "unknown dataset" in ql("data", "fetch", "spy", "-s", "SPY").output  # dataset checked first
    ql(*CREATE)
    r = ql("data", "fetch", "spy", "-s", "SPY")
    assert r.exit_code == 1 and "secret set tiingo/api-key" in r.output
    runner.invoke(app, ["secret", "set", "tiingo/api-key"], input="k\n")

    import quantlab.data.providers.tiingo as tiingo

    real_init = tiingo.TiingoProvider.__init__

    def mocked_init(self, api_key, **kw):
        real_init(
            self,
            api_key,
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=PAYLOAD)),
            min_interval_s=0,
            **kw,
        )

    monkeypatch.setattr(tiingo.TiingoProvider, "__init__", mocked_init)
    r = ql("data", "fetch", "spy", "-s", "SPY", "--start", "2024-07-01", "--end", "2024-07-09", "--json")
    assert r.exit_code == 0, r.output
    summary = json.loads(r.stdout)
    assert summary["created"] and summary["rows"] == 5
    assert len(json.loads(ql("data", "actions", "spy", "--json").stdout)) == 2
    assert ql("data", "fetch", "spy", "-s", "SPY", "--provider", "yahoo").exit_code == 1


def test_md_commands_label_simulated_and_refuse_when_live_required(home, tmp_path):
    ql(*CREATE)
    csv = tmp_path / "spy.csv"
    csv.write_text(csv_text(daily_rows()))
    ql("data", "import", "spy", csv, "--symbol", "SPY")
    stream = ql("md", "stream", "SPY", "--dataset", "spy", "--count", "3")
    lines = [line for line in stream.stdout.splitlines() if line.strip()]
    assert stream.exit_code == 0 and len(lines) == 3 and all(line.startswith("[SIMULATED]") for line in lines)
    snap = json.loads(ql("md", "snapshot", "SPY", "--dataset", "spy", "--json").stdout)
    assert snap["quotes"][0]["state"] == "SIMULATED" and snap["rejected"] == []
    refused = ql("md", "stream", "SPY", "--dataset", "spy", "--require-live")
    assert refused.exit_code == 1 and "SIMULATED data rejected" in refused.output
    assert ql("md", "snapshot", "SPY", "--dataset", "spy", "--require-live").exit_code == 1
    status = ql("md", "status", "--dataset", "spy").stdout
    assert "real-time provider:  mock" in status and "data state: SIMULATED" in status
    assert "needs a dataset" in ql("md", "stream", "SPY").output


def test_data_compare_cli(home, tmp_path):
    ql(*CREATE)
    ql(*[a if a != "spy" else "spy2" for a in CREATE])
    csv = tmp_path / "spy.csv"
    csv.write_text(csv_text(daily_rows()))
    ql("data", "import", "spy", csv, "--symbol", "SPY")
    csv.write_text(csv_text(daily_rows()[:-1]))
    ql("data", "import", "spy2", csv, "--symbol", "SPY")
    report = json.loads(ql("data", "compare", "spy", "spy2", "--json").stdout)
    [row] = report["symbols"]
    assert (row["rows_a"], row["rows_b"], row["overlap"], row["close_rel_diff_max"]) == (6, 5, 5, 0.0)
    assert "common" in ql("data", "compare", "spy", "spy2").stdout


def test_since_last_fetch(home, monkeypatch):
    import httpx

    import quantlab.data.providers.tiingo as tiingo
    from tests.unit.data.test_tiingo import PAYLOAD

    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=PAYLOAD)

    real_init = tiingo.TiingoProvider.__init__
    monkeypatch.setattr(
        tiingo.TiingoProvider,
        "__init__",
        lambda self, key, **kw: real_init(
            self, key, transport=httpx.MockTransport(handler), min_interval_s=0, **kw
        ),
    )
    ql(*CREATE)
    runner.invoke(app, ["secret", "set", "tiingo/api-key"], input="k\n")
    assert ql("data", "fetch", "spy", "-s", "SPY", "--since-last").exit_code == 1  # nothing stored yet
    assert (
        ql("data", "fetch", "spy", "-s", "SPY", "--start", "2024-07-01", "--end", "2024-07-08").exit_code == 0
    )
    r = ql("data", "fetch", "spy", "-s", "SPY", "--since-last", "--end", "2024-07-09")
    assert r.exit_code == 0 and "unchanged" in r.stdout  # same synthetic payload -> no new version
    assert seen[-1]["startDate"] == "2024-07-08"  # restarts at the last stored day
    assert ql("data", "fetch", "spy", "-s", "SPY", "--start", "2024-07-01", "--since-last").exit_code == 1


def test_paper_trading_cli_through_risk_gate(home, tmp_path, monkeypatch):
    """Paper orders via the CLI: risk gate, kill switch, idempotency, reconcile (synthetic quotes)."""
    from quantlab.core.config import load_settings

    ql(*CREATE)
    csv = tmp_path / "spy.csv"
    csv.write_text(csv_text(daily_rows()))
    ql("data", "import", "spy", csv, "--symbol", "SPY")
    monkeypatch.setenv("QUANTLAB_MARKET_DATA__MOCK_DATASET", "spy")

    r = ql("paper", "order", "SPY", "buy", "1", "--key", "no-limits")
    assert r.exit_code == 1 and "no risk limits file" in r.output  # nothing trades without limits
    assert ql("risk", "init").exit_code == 0
    assert ql("risk", "init").exit_code == 1  # never overwritten
    limits = load_settings().risk_limits_file
    text = limits.read_text().replace('trading_calendar = "XNYS"', 'trading_calendar = "none"')
    limits.write_text(
        text.replace("max_quote_age_s = 15.0", "max_quote_age_s = 1e10")
    )  # mock replays old bars
    assert "kill switch: not engaged" in ql("risk", "show").stdout

    r = ql("paper", "order", "SPY", "buy", "5", "--key", "k1", "--json")
    out = json.loads(r.stdout)
    assert r.exit_code == 0 and out["state"] == "FILLED" and not out["duplicate"]
    again = json.loads(ql("paper", "order", "SPY", "buy", "5", "--key", "k1", "--json").stdout)
    assert again["duplicate"] and again["order_id"] == out["order_id"]
    positions = json.loads(ql("paper", "positions", "--json").stdout)
    assert positions["positions"][0]["quantity"] == 5
    assert "FILLED" in ql("paper", "orders").stdout
    assert ql("paper", "reconcile").exit_code == 0
    bad_gw = ql("paper", "gateway", "--deployment", "Bad Name")
    assert bad_gw.exit_code == 1 and "deployment names" in bad_gw.output
    sup = ql("paper", "supervise", "--ticks", "1")
    assert sup.exit_code == 0 and "ticks 1" in sup.output, sup.output
    status = json.loads(ql("paper", "supervisor-status", "--json").stdout)
    assert status["running"] is True and status["status"]["ticks"] == 1

    rest = ql("paper", "order", "SPY", "buy", "1", "--key", "rest", "--type", "limit", "--limit-price", "0.5")
    assert rest.exit_code == 0 and "ACCEPTED" in rest.output, rest.output  # works far below the market
    assert ql("risk", "kill-switch", "engage").exit_code == 1  # reason required
    engaged = ql("risk", "kill-switch", "engage", "--reason", "test")
    assert engaged.exit_code == 0 and ": CANCELLED" in engaged.output  # working orders cancelled on engage
    assert "max_drawdown_fraction" in ql("risk", "template").stdout
    r = ql("paper", "order", "SPY", "buy", "1", "--key", "k2")
    assert r.exit_code == 1 and "kill switch" in r.output
    piped = ql_input("DISENGAGE KILL SWITCH\n", "risk", "kill-switch", "disengage", "--reason", "ok")
    assert piped.exit_code == 1 and "interactive terminal" in piped.output  # piped input is refused
    monkeypatch.setattr("quantlab.cli.trading._interactive", lambda: True)  # a person at a terminal
    assert ql_input("yes\n", "risk", "kill-switch", "disengage", "--reason", "ok").exit_code == 1
    ok = ql_input("DISENGAGE KILL SWITCH\n", "risk", "kill-switch", "disengage", "--reason", "ok")
    assert ok.exit_code == 0, ok.output
    decisions = json.loads(ql("risk", "decisions", "--json").stdout)
    # newest first: kill switch, rest, k1, no limits
    assert [d["approved"] for d in decisions] == [False, True, True, False]
    assert any(c["check"] == "kill switch" for c in decisions[0]["failed"])
    assert ql("paper", "order", "SPY", "hold", "1", "--key", "bad").exit_code == 1

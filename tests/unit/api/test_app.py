import pytest
from fastapi.testclient import TestClient

from quantlab.api.app import create_app
from quantlab.bootstrap import AppContext, initialize
from quantlab.core.secret_store import SecretStore
from tests.fixtures.m1 import EXAMPLE_STRATEGY, configure_fake_engine


@pytest.fixture
def ctx(ql_home, memory_keyring, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(tmp_path / "fake"))
    initialize()
    configure_fake_engine(ql_home)
    context = AppContext.open(configure_logging=False)
    yield context
    context.close()


@pytest.fixture
def client(ctx):
    return TestClient(create_app(ctx), base_url="http://127.0.0.1")


def auth(ctx):
    return {"Authorization": f"Bearer {ctx.api_token().get_secret_value()}"}


def test_health_is_public(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


@pytest.mark.parametrize(
    "path",
    [
        "/api/strategies",
        "/api/backtests",
        "/api/backtests/x",
        "/api/backtests/x/charts",
        "/api/backtests/x/report",
    ],
)
@pytest.mark.parametrize("header", [None, "Bearer wrong", "Basic abc", "Bearer"])
def test_endpoints_require_valid_token(client, path, header):
    headers = {"Authorization": header} if header else {}
    r = client.get(path, headers=headers)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


def test_fails_closed_without_configured_token(ctx):
    ctx.secrets = SecretStore(backend=type("K", (), {"get_password": lambda *a: None})(), env={})
    r = TestClient(create_app(ctx), base_url="http://127.0.0.1").get(
        "/api/backtests", headers={"Authorization": "Bearer anything"}
    )
    assert r.status_code == 503


def test_foreign_host_header_rejected(ctx):
    evil = TestClient(create_app(ctx), base_url="http://evil.example.com")
    assert evil.get("/api/health").status_code == 400  # DNS-rebinding protection


def test_strategies_and_backtests(ctx, client):
    with ctx.sessions() as s:
        ctx.registry.register(s, "sma", EXAMPLE_STRATEGY)
        s.commit()
    run = ctx.backtests().run("sma")
    assert [x["name"] for x in client.get("/api/strategies", headers=auth(ctx)).json()] == ["sma"]

    listing = client.get("/api/backtests", headers=auth(ctx)).json()
    assert [x["id"] for x in listing] == [run.id] and listing[0]["data_provenance"] == "sample"
    assert client.get("/api/backtests?strategy=sma", headers=auth(ctx)).status_code == 200
    assert client.get("/api/backtests?strategy=nope", headers=auth(ctx)).status_code == 404
    assert client.get("/api/backtests?limit=0", headers=auth(ctx)).status_code == 422

    detail = client.get(f"/api/backtests/{run.id}", headers=auth(ctx)).json()
    assert detail["status"] == "succeeded" and detail["parameters"] == {"fast": "10", "slow": "30"}
    assert detail["manifest"]["data"]["provenance"] == "sample"
    assert {c["metric"] for c in detail["cross_checks"]} == {"total_return", "cagr", "max_drawdown"}
    assert client.get("/api/backtests/unknown", headers=auth(ctx)).status_code == 404

    charts = client.get(f"/api/backtests/{run.id}/charts", headers=auth(ctx)).json()
    assert charts["data_provenance"] == "sample" and charts["charts"]["equity"] and charts["warnings"]
    assert len(charts["orders"]) == 3 and charts["charts"]["trade_stats"]["trades"] == 1
    report = client.get(f"/api/backtests/{run.id}/report", headers=auth(ctx))
    assert report.status_code == 200 and "attachment" in report.headers["content-disposition"]
    assert "Not a performance claim" in report.text
    assert not (ctx.settings.layout.runs_dir / run.id / "reports").exists()  # GET writes nothing
    assert client.get(f"/api/backtests/{run.id}/charts?benchmark=nope", headers=auth(ctx)).status_code == 404


def test_api_is_read_only(client, ctx):
    for method in ("post", "put", "delete", "patch"):
        r = getattr(client, method)("/api/backtests", headers=auth(ctx))
        assert r.status_code == 405


def test_datasets_endpoint(ctx, client, tmp_path):
    from quantlab.data.providers.base import BarRequest, LicenseInfo
    from quantlab.data.providers.csv_file import CsvFileProvider
    from quantlab.data.schema import AssetClass, PriceAdjustment, Provenance, Resolution
    from tests.unit.data.conftest import csv_text, daily_rows

    assert client.get("/api/datasets").status_code == 401
    csv = tmp_path / "spy.csv"
    csv.write_text(csv_text(daily_rows()))
    with ctx.sessions() as s:
        ctx.data.create_dataset(
            s,
            "spy",
            asset_class=AssetClass.EQUITY,
            market="usa",
            resolution=Resolution.DAILY,
            provenance=Provenance.SYNTHETIC,
            price_adjustment=PriceAdjustment.RAW,
            source="fixture",
            license=LicenseInfo("test", False),
        )
        p = CsvFileProvider(csv, calendar="XNYS", symbol="SPY")
        ctx.data.import_bars(s, "spy", p, p.fetch_bars(BarRequest((), Resolution.DAILY)))
        s.commit()
    [d] = client.get("/api/datasets", headers=auth(ctx)).json()
    assert d["name"] == "spy" and d["provenance"] == "synthetic" and d["versions"] == 1
    assert d["latest_rows"] == 6 and d["latest_validation_ok"] is True


@pytest.fixture
def with_dataset(ctx, tmp_path):
    from quantlab.data.providers.base import BarRequest, LicenseInfo
    from quantlab.data.providers.csv_file import CsvFileProvider
    from quantlab.data.schema import AssetClass, PriceAdjustment, Provenance, Resolution
    from tests.unit.data.conftest import csv_text, daily_rows

    csv = tmp_path / "spy.csv"
    csv.write_text(csv_text(daily_rows()))
    with ctx.sessions() as s:
        ctx.data.create_dataset(
            s,
            "spy",
            asset_class=AssetClass.EQUITY,
            market="usa",
            resolution=Resolution.DAILY,
            provenance=Provenance.SYNTHETIC,
            price_adjustment=PriceAdjustment.RAW,
            source="fixture",
            license=LicenseInfo("test", False),
        )
        p = CsvFileProvider(csv, calendar="XNYS", symbol="SPY")
        ctx.data.import_bars(s, "spy", p, p.fetch_bars(BarRequest((), Resolution.DAILY)))
        s.commit()
    return "spy"


def test_dataset_detail_and_bars(ctx, client, with_dataset):
    detail = client.get("/api/datasets/spy", headers=auth(ctx)).json()
    [v] = detail["version_history"]
    assert v["rows"] == 6 and v["symbols"] == ["SPY"] and v["validation"]["ok"] is True
    assert client.get("/api/datasets/nope", headers=auth(ctx)).status_code == 404

    bars = client.get("/api/datasets/spy/bars?symbol=spy", headers=auth(ctx)).json()
    assert bars["symbol"] == "SPY" and bars["provenance"] == "synthetic" and len(bars["bars"]) == 6
    assert bars["bars"][0]["ts"].startswith("2024-07-01T20:00:00") and bars["truncated"] is False
    tail = client.get("/api/datasets/spy/bars?symbol=SPY&limit=2", headers=auth(ctx)).json()
    assert tail["truncated"] is True and [b["close"] for b in tail["bars"]] == [105.0, 106.0]
    assert client.get("/api/datasets/spy/bars?symbol=SPY", headers={}).status_code == 401
    assert client.get("/api/datasets/spy/bars?symbol=SPY&limit=0", headers=auth(ctx)).status_code == 422


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


def test_ui_not_built_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "web UI has not been built" in r.text


@pytest.fixture
def ui_client(ctx, tmp_path):
    ui = tmp_path / "dist"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("<!doctype html><div id=root>QuantLab UI</div>")
    (ui / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("outside the UI dir")
    return TestClient(create_app(ctx, ui), base_url="http://127.0.0.1")


def test_ui_served_same_origin_with_spa_fallback(ctx, ui_client):
    assert "QuantLab UI" in ui_client.get("/").text
    assert "QuantLab UI" in ui_client.get("/data/spy").text  # client-side route
    js = ui_client.get("/assets/app.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert "default-src 'self'" in ui_client.get("/").headers["content-security-policy"]
    # API paths never fall back to the UI and keep requiring the token
    assert ui_client.get("/api/nope", headers=auth(ctx)).status_code == 404
    assert ui_client.get("/api/datasets").status_code == 401
    # no path traversal out of the UI directory
    assert "outside the UI dir" not in ui_client.get("/../secret.txt").text
    assert "outside the UI dir" not in ui_client.get("/%2e%2e/secret.txt").text


def test_dataset_actions_and_adjusted_bars(ctx, client, tmp_path):
    from dataclasses import replace

    from quantlab.data.providers.base import BarRequest, LicenseInfo
    from quantlab.data.providers.csv_file import CsvFileProvider, load_actions_csv
    from quantlab.data.schema import AssetClass, PriceAdjustment, Provenance, Resolution
    from tests.unit.data.conftest import csv_text, daily_rows

    csv = tmp_path / "spy.csv"
    csv.write_text(csv_text(daily_rows()))
    acts = tmp_path / "a.csv"
    acts.write_text("ex_date,kind,value\n2024-07-05,split,2\n")
    with ctx.sessions() as s:
        ctx.data.create_dataset(
            s,
            "spy",
            asset_class=AssetClass.EQUITY,
            market="usa",
            resolution=Resolution.DAILY,
            provenance=Provenance.SYNTHETIC,
            price_adjustment=PriceAdjustment.RAW,
            source="fixture",
            license=LicenseInfo("test", False),
        )
        p = CsvFileProvider(csv, calendar="XNYS", symbol="SPY")
        ctx.data.import_bars(
            s,
            "spy",
            p,
            replace(p.fetch_bars(BarRequest((), Resolution.DAILY)), actions=load_actions_csv(acts, "SPY")),
        )
        s.commit()
    detail = client.get("/api/datasets/spy", headers=auth(ctx)).json()
    assert detail["corporate_actions"] == [
        {"symbol": "SPY", "ex_date": "2024-07-05", "kind": "split", "value": 2.0}
    ]
    bars = client.get("/api/datasets/spy/bars?symbol=SPY&adjust=split", headers=auth(ctx)).json()
    assert bars["adjustment"] == "split" and bars["bars"][0]["close"] == pytest.approx(50.5)
    assert client.get("/api/datasets/spy/bars?symbol=SPY&adjust=bad", headers=auth(ctx)).status_code == 422


def test_strategy_editor_endpoints(ctx, client):
    h = auth(ctx)
    templates = client.get("/api/strategy-templates", headers=h).json()
    assert {t["name"] for t in templates} == {"momentum_rotation", "rsi_mean_reversion", "pairs_zscore"}
    r = client.post("/api/strategies", json={"name": "my-rsi", "template": "rsi_mean_reversion"}, headers=h)
    assert r.status_code == 201 and r.json()["name"] == "my-rsi"
    assert r.json()["path"].startswith(str(ctx.settings.layout.strategies_dir))
    assert (
        client.post(
            "/api/strategies", json={"name": "my-rsi", "template": "rsi_mean_reversion"}, headers=h
        ).status_code
        == 409
    )
    assert (
        client.post("/api/strategies", json={"name": "x", "template": "nope"}, headers=h).status_code == 404
    )

    files = {f["path"]: f for f in client.get("/api/strategies/my-rsi/files", headers=h).json()}
    assert {"main.py", "strategy.toml"} <= set(files)
    main = client.get("/api/strategies/my-rsi/files/main.py", headers=h).json()
    assert "RsiMeanReversion" in main["content"] and main["sha256"] == files["main.py"]["sha256"]

    new = main["content"] + "\n# edited\n"
    saved = client.put(
        "/api/strategies/my-rsi/files/main.py",
        json={"content": new, "expected_sha256": main["sha256"]},
        headers=h,
    )
    assert saved.status_code == 200 and saved.json()["syntax_error"] is None
    stale = client.put(
        "/api/strategies/my-rsi/files/main.py",
        json={"content": "x", "expected_sha256": main["sha256"]},
        headers=h,
    )
    assert stale.status_code == 409
    bad = client.put(
        "/api/strategies/my-rsi/files/..%2F..%2Fevil.py",
        json={"content": "x", "expected_sha256": None},
        headers=h,
    )
    assert bad.status_code in (404, 422)
    assert client.get("/api/strategies/nope/files", headers=h).status_code == 404
    assert (
        client.put(
            "/api/strategies/my-rsi/files/main.py", json={"content": "x", "expected_sha256": None}
        ).status_code
        == 401
    )


def test_optimization_and_robustness_endpoints(ctx, client, monkeypatch):
    from quantlab.optimize.objective import Objective
    from quantlab.optimize.service import StudySpec
    from quantlab.optimize.space import Space

    with ctx.sessions() as s:
        ctx.registry.register(s, "sma", EXAMPLE_STRATEGY)
        s.commit()
    opt = ctx.optimizer().sweep(StudySpec("sma", Space.parse(["fast=5,10"]), Objective.parse("sharpe")))
    h = auth(ctx)
    listing = client.get("/api/optimizations", headers=h).json()
    assert [o["id"] for o in listing] == [opt.id] and listing[0]["objective"] == "sharpe"
    detail = client.get(f"/api/optimizations/{opt.id}", headers=h).json()
    assert len(detail["trials"]) == 2 and detail["summary"]["trials"] == 2
    assert client.get("/api/optimizations/nope", headers=h).status_code == 404
    run_id = detail["trials"][0]["run_id"]
    rob = client.get(f"/api/backtests/{run_id}/robustness?samples=20", headers=h).json()
    assert rob["run_id"] == run_id and rob["warnings"]
    assert client.get(f"/api/backtests/{run_id}/robustness?samples=999999", headers=h).status_code == 422


def test_compare_endpoint(ctx, client):
    with ctx.sessions() as s:
        ctx.registry.register(s, "sma", EXAMPLE_STRATEGY)
        s.commit()
    a, b = ctx.backtests().run("sma"), ctx.backtests().run("sma", {"fast": "5"})
    h = auth(ctx)
    out = client.get(f"/api/compare?runs={a.id},{b.id}", headers=h).json()
    assert [r["id"] for r in out["runs"]] == [a.id, b.id] and len(out["equity"]) == 2 and out["warnings"]
    assert "fast=5" in out["runs"][1]["label"]
    assert client.get(f"/api/compare?runs={a.id}", headers=h).status_code == 404
    assert client.get(f"/api/compare?runs={a.id},{a.id}", headers=h).status_code == 404


def test_lifecycle_endpoint_is_read_only(ctx, client):
    with ctx.sessions() as s:
        ctx.registry.register(s, "sma", EXAMPLE_STRATEGY)
        s.commit()
    h = auth(ctx)
    assert client.get("/api/strategies", headers=h).json()[0]["stage"] == "research"
    st = client.get("/api/strategies/sma/lifecycle", headers=h).json()
    assert st["stage"] == "research" and st["next_stage"] == "backtested" and st["history"] == []
    assert st["gates"]["require_real_data"] is True
    assert client.get("/api/strategies/nope/lifecycle", headers=h).status_code == 404
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/api/strategies/sma/lifecycle", headers=h).status_code == 405


def test_paper_and_risk_endpoints_are_read_only(ctx, client, tmp_path):
    from datetime import UTC, datetime

    from quantlab.brokers.base import Side
    from quantlab.brokers.paper import PaperBroker
    from quantlab.data.market_data import DataState, Quote
    from quantlab.oms.service import OMS, OrderRequest
    from quantlab.risk.gate import RiskGate

    h = auth(ctx)
    risk = client.get("/api/risk", headers=h).json()
    assert risk["kill_switch"]["engaged"] is False and risk["limits"]["valid"] is False  # no limits file yet

    limits = ctx.settings.risk_limits_file
    limits.write_text(
        "[limits]\nmax_order_quantity = 10\nmax_order_notional = 1000.0\nmax_position_notional = 1000.0\n"
        "max_gross_exposure_fraction = 1.0\nmax_open_orders = 5\nmax_orders_per_minute = 5\n"
        "daily_loss_limit_fraction = 0.05\nmax_drawdown_fraction = 0.10\nmax_rejections_per_hour = 50\n"
        "allow_margin = false\nmax_quote_age_s = 60.0\nmax_spread_bps = 100.0\n"
        "allow_short = false\n"
        'require_live_quotes = false\ntrading_calendar = "none"\n'
    )

    def quote(symbol):  # synthetic SIMULATED quote
        now = datetime.now(UTC)
        return Quote(symbol, DataState.SIMULATED, "test", now, now, bid=9.99, ask=10.01, last=10.0)

    broker = PaperBroker(tmp_path / "p.json", quote, starting_cash=ctx.settings.paper.starting_cash)
    oms = OMS(
        ctx.sessions,
        broker,
        RiskGate(limits),
        quote,
        account="paper",
        starting_cash=ctx.settings.paper.starting_cash,
    )
    filled = oms.submit(OrderRequest("a1", "SPY", Side.BUY, 5))
    rejected = oms.submit(OrderRequest("a2", "SPY", Side.BUY, 50))

    orders = client.get("/api/paper/orders", headers=h).json()
    assert {o["state"] for o in orders} == {"FILLED", "RISK_REJECTED"} and all(
        o["mode"] == "paper" for o in orders
    )
    detail = client.get(f"/api/paper/orders/{filled.order.id}", headers=h).json()
    assert [e["state"] for e in detail["events"]][-1] == "FILLED" and len(detail["fills"]) == 1
    assert detail["risk_decisions"][0]["approved"] is True
    bad = client.get(f"/api/paper/orders/{rejected.order.id}", headers=h).json()
    assert bad["risk_decisions"][0]["approved"] is False
    assert client.get("/api/paper/orders/nope", headers=h).status_code == 404
    portfolio = client.get("/api/paper/portfolio", headers=h).json()
    assert portfolio["mode"] == "paper" and portfolio["positions"][0]["quantity"] == 5
    risk = client.get("/api/risk", headers=h).json()
    assert risk["limits"]["valid"] and len(risk["decisions"]) == 2
    for path in ("/api/paper/orders", "/api/risk", "/api/paper/portfolio"):
        for method in ("post", "put", "delete", "patch"):
            assert getattr(client, method)(path, headers=h).status_code == 405

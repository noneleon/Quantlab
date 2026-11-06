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


@pytest.mark.parametrize("path", ["/api/strategies", "/api/backtests", "/api/backtests/x"])
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


def test_api_is_read_only(client, ctx):
    for method in ("post", "put", "delete", "patch"):
        r = getattr(client, method)("/api/backtests", headers=auth(ctx))
        assert r.status_code == 405

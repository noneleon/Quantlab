"""Tiingo provider against a mock transport.

Payloads are SYNTHETIC values in Tiingo's documented JSON format (field names from Tiingo's EOD docs);
no real Tiingo data is stored in the repository (Tiingo's terms forbid redistribution).
"""

from datetime import UTC, datetime

import httpx
import pandas as pd
import pytest
from pydantic import SecretStr

from quantlab.data.providers.base import BarRequest, DataProvider, ProviderError
from quantlab.data.providers.tiingo import LICENSE, TiingoProvider
from quantlab.data.schema import Resolution

DAILY = BarRequest(
    ("SPY",), Resolution.DAILY, datetime(2024, 7, 1, tzinfo=UTC), datetime(2024, 7, 9, tzinfo=UTC)
)


def record(day, close, div=0.0, split=1.0):
    return {
        "date": f"{day}T00:00:00.000Z",
        "open": close - 1,
        "high": close + 1,
        "low": close - 2,
        "close": close,
        "volume": 1000,
        "adjOpen": 0.0,
        "adjHigh": 0.0,
        "adjLow": 0.0,
        "adjClose": 0.0,
        "adjVolume": 0,
        "divCash": div,
        "splitFactor": split,
    }


PAYLOAD = [
    record("2024-07-01", 100.0),
    record("2024-07-02", 99.0, div=1.0),
    record("2024-07-03", 104.0),
    record("2024-07-05", 52.0, split=2.0),
    record("2024-07-08", 53.0),
]


def provider(handler, **kw):
    sleeps = []
    p = TiingoProvider(
        SecretStr("test-key"),
        calendar="XNYS",
        transport=httpx.MockTransport(handler),
        min_interval_s=0,
        sleep=sleeps.append,
        clock=lambda: datetime(2026, 11, 7, tzinfo=UTC),
        **kw,
    )
    return p, sleeps


def test_request_shape_and_parsing():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = request.url, request.headers.get("authorization")
        return httpx.Response(200, json=PAYLOAD)

    p, _ = provider(handler)
    assert isinstance(p, DataProvider) and p.license is LICENSE and not LICENSE.redistribution_allowed
    raw = p.fetch_bars(DAILY)
    assert seen["auth"] == "Token test-key"
    assert seen["url"].path == "/tiingo/daily/spy/prices" and "token" not in str(seen["url"])
    assert dict(seen["url"].params) == {
        "startDate": "2024-07-01",
        "endDate": "2024-07-09",
        "resampleFreq": "daily",
        "format": "json",
    }
    f = raw.frame
    assert list(f["close"]) == [100.0, 99.0, 104.0, 52.0, 53.0]  # raw, adj* ignored
    assert f["ts"].iloc[0] == pd.Timestamp("2024-07-01T20:00:00Z") and f["ts"].iloc[2] == pd.Timestamp(
        "2024-07-03T17:00Z"
    )
    acts = raw.actions.sort_values("kind").to_dict("records")
    assert [(a["kind"], str(a["ex_date"]), a["value"]) for a in acts] == [
        ("dividend", "2024-07-02", 1.0),
        ("split", "2024-07-05", 2.0),
    ]
    assert raw.source == "tiingo:SPY" and any("Tiingo EOD 2024-07-01..2024-07-09" in n for n in raw.notes)


def test_default_range_is_full_history_until_today():
    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return httpx.Response(200, json=PAYLOAD[:1])

    p, _ = provider(handler)
    p.fetch_bars(BarRequest(("SPY",), Resolution.DAILY))
    assert seen["startDate"] == "1962-01-02" and seen["endDate"] == "2026-11-07"


def test_rate_limit_retry_then_success():
    calls = []

    def handler(request):
        calls.append(1)
        return (
            httpx.Response(429, headers={"Retry-After": "7"})
            if len(calls) == 1
            else httpx.Response(200, json=PAYLOAD)
        )

    p, sleeps = provider(handler)
    assert len(p.fetch_bars(DAILY).frame) == 5
    assert 7.0 in sleeps and len(calls) == 2


@pytest.mark.parametrize(
    ("status", "match"),
    [
        (401, "rejected the API key"),
        (403, "rejected the API key"),
        (404, "unknown ticker"),
        (500, "HTTP 500"),
        (429, "persisted"),
    ],
)
def test_http_errors(status, match):
    p, _ = provider(lambda r: httpx.Response(status, text="nope"))
    with pytest.raises(ProviderError, match=match):
        p.fetch_bars(DAILY)


def test_error_messages_never_contain_the_key():
    p, _ = provider(lambda r: httpx.Response(401, text="bad token test-key"))
    with pytest.raises(ProviderError) as exc:
        p.fetch_bars(DAILY)
    assert "test-key" not in str(exc.value)


@pytest.mark.parametrize("payload", [{"detail": "x"}, [{"date": "2024-07-01T00:00:00.000Z", "close": 1}]])
def test_malformed_payloads(payload):
    p, _ = provider(lambda r: httpx.Response(200, json=payload))
    with pytest.raises(ProviderError):
        p.fetch_bars(DAILY)


def test_only_daily_and_requires_symbols():
    p, _ = provider(lambda r: httpx.Response(200, json=PAYLOAD))
    with pytest.raises(ProviderError, match="daily"):
        p.fetch_bars(BarRequest(("SPY",), Resolution.HOUR))
    with pytest.raises(ProviderError, match="--start"):
        p.fetch_bars(BarRequest(("SPY",), Resolution.MINUTE))
    with pytest.raises(ProviderError, match="symbol"):
        p.fetch_bars(BarRequest((), Resolution.DAILY))
    with pytest.raises(ProviderError, match="empty"):
        TiingoProvider(SecretStr(""), calendar="XNYS")


def test_fetch_into_catalog_stores_bars_and_actions(db_session, tmp_path):
    from quantlab.data.catalog import DataCatalog, DataError
    from quantlab.data.providers.base import LicenseInfo
    from quantlab.data.schema import AssetClass, PriceAdjustment, Provenance

    catalog = DataCatalog(tmp_path / "data")
    for name, redistributable in (("spy", False), ("spy-redist", True)):
        catalog.create_dataset(
            db_session,
            name,
            asset_class=AssetClass.EQUITY,
            market="usa",
            resolution=Resolution.DAILY,
            provenance=Provenance.SYNTHETIC,
            price_adjustment=PriceAdjustment.RAW,
            source="tiingo (mock)",
            license=LicenseInfo("t", redistributable),
        )
    p, _ = provider(lambda r: httpx.Response(200, json=PAYLOAD))
    result = catalog.fetch(db_session, "spy", p, DAILY)
    db_session.commit()
    assert result.created and result.version.row_count == 5 and len(catalog.actions(db_session, "spy")) == 2
    with pytest.raises(DataError, match="redistribut"):
        catalog.fetch(db_session, "spy-redist", p, DAILY)
    with pytest.raises(DataError, match="minute"):
        catalog.fetch(db_session, "spy", p, BarRequest(("SPY",), Resolution.MINUTE))


def minute_records(day, count, start="13:30"):
    base = pd.Timestamp(f"{day}T{start}:00Z")
    return [
        {
            "date": (base + pd.Timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 100,
        }
        for i in range(count)
    ]


def test_minute_bars_shift_start_to_end_and_chunk_requests():
    windows = []

    def handler(request):
        params = dict(request.url.params)
        windows.append((params["startDate"], params["endDate"]))
        assert request.url.path == "/iex/spy/prices" and params["resampleFreq"] == "1min"
        assert params["columns"] == "open,high,low,close,volume"
        return httpx.Response(200, json=minute_records(params["startDate"], 2))

    p, _ = provider(handler)
    raw = p.fetch_bars(
        BarRequest(
            ("SPY",), Resolution.MINUTE, datetime(2024, 6, 1, tzinfo=UTC), datetime(2024, 6, 30, tzinfo=UTC)
        )
    )
    assert windows == [
        ("2024-06-01", "2024-06-14"),
        ("2024-06-15", "2024-06-28"),
        ("2024-06-29", "2024-06-30"),
    ]
    assert raw.frame["ts"].iloc[0] == pd.Timestamp("2024-06-01T13:31:00Z")  # bar start 13:30 -> end 13:31
    assert any("IEX-only volume" in n for n in raw.notes) and raw.actions is None


def test_capped_response_is_split_never_accepted():
    calls = []

    def handler(request):
        params = dict(request.url.params)
        calls.append((params["startDate"], params["endDate"]))
        if params["startDate"] != params["endDate"]:
            return httpx.Response(200, json=minute_records(params["startDate"], 10_000))  # looks truncated
        return httpx.Response(200, json=minute_records(params["startDate"], 390))

    p, _ = provider(handler)
    raw = p.fetch_bars(
        BarRequest(
            ("SPY",), Resolution.MINUTE, datetime(2024, 7, 1, tzinfo=UTC), datetime(2024, 7, 2, tzinfo=UTC)
        )
    )
    assert calls == [("2024-07-01", "2024-07-02"), ("2024-07-01", "2024-07-01"), ("2024-07-02", "2024-07-02")]
    assert len(raw.frame) == 780


def test_single_day_at_cap_is_refused():
    p, _ = provider(lambda r: httpx.Response(200, json=minute_records("2024-07-01", 10_000)))
    with pytest.raises(ProviderError, match="truncated"):
        p.fetch_bars(
            BarRequest(
                ("SPY",),
                Resolution.MINUTE,
                datetime(2024, 7, 1, tzinfo=UTC),
                datetime(2024, 7, 1, tzinfo=UTC),
            )
        )

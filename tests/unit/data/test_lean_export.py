"""LEAN export format tests.

Golden lines below are copied verbatim from LEAN's bundled sample data (QuantConnect LEAN repository,
Apache-2.0: Data/equity/usa/{daily,hour}/spy.zip and Data/equity/usa/minute/spy/20131007_trade.zip).
Converting them into QuantLab's canonical frame and exporting again must reproduce LEAN's bytes exactly.
"""

import io
import json
import zipfile
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from quantlab.data.lean_export import (
    MANIFEST,
    LeanExportError,
    build_lean_files,
    export_version,
    verify_export,
)

NY = ZoneInfo("America/New_York")
DAILY = [
    "20131007 00:00,1674300,1684500,1672500,1674200,81489219",
    "20131008 00:00,1674500,1676200,1653700,1654800,153859703",
]
HOUR = [
    "20131007 09:00,1674300,1678800,1672500,1677200,13131967",
    "20131007 10:00,1677400,1683800,1676100,1681800,15312562",
    "20131007 11:00,1681900,1683800,1679300,1682200,8867641",
]
MINUTE = [
    "38400000,1681500,1681800,1681500,1681600,99204",
    "38460000,1681700,1681800,1681400,1681400,160796",
    "38520000,1681400,1681600,1681200,1681200,121075",
]


def canonical(lines, resolution):
    """Inverse of the LEAN format: LEAN line -> canonical row (bar END in UTC, unscaled prices)."""
    rows = []
    for line in lines:
        stamp, *vals = line.split(",")
        if resolution == "daily":
            day = pd.Timestamp(stamp[:8])
            end = pd.Timestamp(f"{day:%Y-%m-%d} 16:00", tz=NY)  # NYSE close (regular session)
        elif resolution == "hour":
            end = pd.Timestamp(pd.to_datetime(stamp, format="%Y%m%d %H:%M"), tz=NY) + pd.Timedelta(hours=1)
        else:
            end = pd.Timestamp("2013-10-07", tz=NY) + pd.Timedelta(milliseconds=int(stamp) + 60_000)
        o, h, lo, c = (int(v) / 10_000 for v in vals[:4])
        rows.append(
            {
                "symbol": "SPY",
                "ts": end.tz_convert("UTC"),
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": float(vals[4]),
            }
        )
    return pd.DataFrame(rows)


def entry(files, path):
    with zipfile.ZipFile(io.BytesIO(files[path])) as zf:
        [name] = zf.namelist()
        return name, zf.read(name).decode()


@pytest.mark.parametrize(
    ("resolution", "lines", "path", "entry_name"),
    [
        ("daily", DAILY, "equity/usa/daily/spy.zip", "spy.csv"),
        ("hour", HOUR, "equity/usa/hour/spy.zip", "spy.csv"),
        ("minute", MINUTE, "equity/usa/minute/spy/20131007_trade.zip", "20131007_spy_minute_trade.csv"),
    ],
)
def test_round_trip_reproduces_lean_sample_bytes(resolution, lines, path, entry_name):
    files = build_lean_files(
        canonical(lines, resolution),
        asset_class="equity",
        market="usa",
        resolution=resolution,
        calendar="XNYS",
    )
    name, text = entry(files, path)
    assert name == entry_name
    assert text == "\n".join(lines) + "\n"


def test_map_and_factor_files_are_neutral():
    files = build_lean_files(
        canonical(DAILY, "daily"), asset_class="equity", market="usa", resolution="daily", calendar="XNYS"
    )
    assert files["equity/usa/map_files/spy.csv"].decode() == "20131007,spy\n20501231,spy\n"
    assert files["equity/usa/factor_files/spy.csv"].decode() == "20131007,1,1,167.42\n20501231,1,1,0\n"


def test_minute_data_split_per_day():
    frame = canonical(MINUTE, "minute")
    nextday = frame.copy()
    nextday["ts"] = nextday["ts"] + pd.Timedelta(days=1)
    files = build_lean_files(
        pd.concat([frame, nextday]), asset_class="equity", market="usa", resolution="minute", calendar="XNYS"
    )
    assert {p for p in files if "/minute/" in p} == {
        "equity/usa/minute/spy/20131007_trade.zip",
        "equity/usa/minute/spy/20131008_trade.zip",
    }


def test_output_is_byte_deterministic():
    kw = dict(asset_class="equity", market="usa", resolution="daily", calendar="XNYS")
    assert build_lean_files(canonical(DAILY, "daily"), **kw) == build_lean_files(
        canonical(DAILY, "daily"), **kw
    )


@pytest.mark.parametrize(
    ("kw", "match"),
    [
        ({"asset_class": "crypto", "resolution": "daily", "calendar": "24/7"}, "equities only"),
        ({"asset_class": "equity", "resolution": "daily", "calendar": "none"}, "exchange timezone"),
    ],
)
def test_unsupported(kw, match):
    with pytest.raises(LeanExportError, match=match):
        build_lean_files(canonical(DAILY, "daily"), market="usa", **kw)


def test_export_version_is_immutable_and_verifiable(tmp_path):
    identity = {
        "name": "spy",
        "asset_class": "equity",
        "market": "usa",
        "resolution": "daily",
        "calendar": "XNYS",
        "provenance": "synthetic",
        "price_adjustment": "raw",
    }
    dest = tmp_path / "lean" / "spy" / "v1"
    h = export_version(canonical(DAILY, "daily"), dest, dataset=identity, version_hash="abc")
    assert verify_export(dest) == h
    assert json.loads((dest / MANIFEST).read_text())["dataset"]["provenance"] == "synthetic"
    with pytest.raises(LeanExportError, match="immutable"):
        export_version(canonical(DAILY, "daily"), dest, dataset=identity, version_hash="abc")
    (dest / "equity" / "usa" / "daily" / "spy.zip").write_bytes(b"x")
    with pytest.raises(LeanExportError, match="modified"):
        verify_export(dest)

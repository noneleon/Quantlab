"""Deterministic export of a dataset version to LEAN's on-disk data format (DATA-009).

Scope: **equities** (incl. ETFs) at daily, hour, minute and second resolution, trade bars only.
Format verified against LEAN's bundled sample data and parsers (2026-11-06):

* daily/hour: ``equity/<market>/<res>/<sym>.zip`` → ``<sym>.csv``;
  line ``yyyyMMdd HH:mm,open,high,low,close,volume`` with the **bar start** in exchange time
  (daily rows use the session date at ``00:00``);
* minute/second: ``equity/<market>/<res>/<sym>/<yyyyMMdd>_trade.zip`` →
  ``<yyyyMMdd>_<sym>_<res>_trade.csv``; line ``ms since midnight (bar start, exchange time),o,h,l,c,v``;
* prices are integers scaled by 10 000 ("deci-cents"); volume is an integer;
* ``map_files/<sym>.csv``: ``<first date>,<sym>`` and ``20501231,<sym>`` (exchange column optional);
* ``factor_files/<sym>.csv``: ``<first date>,1,1,<first close>`` and ``20501231,1,1,0``. QuantLab stores
  prices as declared by the dataset (normally raw) and applies no LEAN-side adjustment until DATA-008 provides
  corporate actions; algorithms should therefore use ``DataNormalizationMode.RAW`` (recorded in the manifest).

Exports are byte-deterministic (sorted entries, fixed zip timestamps) and written once per version.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from quantlab.data.schema import AssetClass, Resolution

PRICE_SCALE = 10_000
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
SUPPORTED_RESOLUTIONS = (Resolution.DAILY, Resolution.HOUR, Resolution.MINUTE, Resolution.SECOND)
MANIFEST = "quantlab-lean-export.json"


class LeanExportError(ValueError):
    pass


def exchange_timezone(calendar: str) -> str:
    if calendar in ("24/7", "weekdays", "none"):
        raise LeanExportError(
            f"calendar {calendar!r} has no exchange timezone; equity exports need an exchange calendar"
        )
    import exchange_calendars

    return str(exchange_calendars.get_calendar(calendar).tz)


def _zip_bytes(entry: str, text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo(entry, date_time=_ZIP_TIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        zf.writestr(info, text)
    return buffer.getvalue()


def _scaled(series: pd.Series) -> pd.Series:
    return (series * PRICE_SCALE).round().astype("int64")


def _lines(group: pd.DataFrame, columns: list[pd.Series]) -> str:
    scaled = [_scaled(group[c]) for c in ("open", "high", "low", "close")]
    volume = group["volume"].fillna(0).round().astype("int64")
    rows = zip(*columns, *scaled, volume, strict=True)
    return "".join(",".join(str(v) for v in row) + "\n" for row in rows)


def build_lean_files(
    frame: pd.DataFrame, *, asset_class: str, market: str, resolution: str, calendar: str
) -> dict[str, bytes]:
    """Return {relative path: file bytes} in LEAN layout for a canonical bar frame."""
    if AssetClass(asset_class) is not AssetClass.EQUITY:
        raise LeanExportError(f"LEAN export supports equities only for now, not {asset_class!r}")
    res = Resolution(resolution)
    if res not in SUPPORTED_RESOLUTIONS:
        raise LeanExportError(f"unsupported resolution {resolution!r}")
    if frame.empty:
        raise LeanExportError("nothing to export")
    tz = exchange_timezone(calendar)
    base = f"equity/{market.lower()}"
    files: dict[str, bytes] = {}

    for symbol, group in frame.groupby("symbol", sort=True):
        sym = str(symbol).lower()
        group = group.sort_values("ts", kind="stable")
        end_local = pd.to_datetime(group["ts"], utc=True).dt.tz_convert(tz)
        if res is Resolution.DAILY:
            session = end_local.dt.normalize()
            stamps = session.dt.strftime("%Y%m%d 00:00")
            files[f"{base}/daily/{sym}.zip"] = _zip_bytes(f"{sym}.csv", _lines(group, [stamps]))
            first_day: date = session.iloc[0].date()
        else:
            start_local = end_local - pd.Timedelta(seconds=res.seconds)
            if res is Resolution.HOUR:
                stamps = start_local.dt.strftime("%Y%m%d %H:%M")
                files[f"{base}/hour/{sym}.zip"] = _zip_bytes(f"{sym}.csv", _lines(group, [stamps]))
            else:
                midnight = start_local.dt.normalize()
                millis = ((start_local - midnight).dt.total_seconds() * 1000).round().astype("int64")
                days = start_local.dt.strftime("%Y%m%d")
                for day in sorted(days.unique()):
                    mask = (days == day).to_numpy()
                    part = group[mask]
                    files[f"{base}/{res.value}/{sym}/{day}_trade.zip"] = _zip_bytes(
                        f"{day}_{sym}_{res.value}_trade.csv", _lines(part, [millis[mask]])
                    )
            first_day = start_local.iloc[0].date()
        first = first_day.strftime("%Y%m%d")
        first_close = repr(float(group["close"].iloc[0]))
        files[f"{base}/map_files/{sym}.csv"] = f"{first},{sym}\n20501231,{sym}\n".encode()
        files[f"{base}/factor_files/{sym}.csv"] = f"{first},1,1,{first_close}\n20501231,1,1,0\n".encode()
    return files


def export_version(
    frame: pd.DataFrame, destination: Path, *, dataset: dict[str, Any], version_hash: str
) -> str:
    """Write a LEAN export into a new ``destination``; returns the export hash (sha256 over files)."""
    if destination.exists():
        raise LeanExportError(f"LEAN export already exists (exports are immutable): {destination}")
    files = build_lean_files(
        frame,
        asset_class=dataset["asset_class"],
        market=dataset["market"],
        resolution=dataset["resolution"],
        calendar=dataset["calendar"],
    )
    hashes = {path: hashlib.sha256(data).hexdigest() for path, data in sorted(files.items())}
    export_hash = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    tmp = destination.with_name(destination.name + ".partial")
    if tmp.exists():
        raise LeanExportError(f"incomplete previous export found, inspect and remove manually: {tmp}")
    for rel, data in files.items():
        target = tmp / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    manifest = {
        "dataset": dataset,
        "version": version_hash,
        "export_hash": export_hash,
        "files": hashes,
        "price_scale": PRICE_SCALE,
        "normalization": "RAW (no LEAN-side adjustment, factor files neutral)",
    }
    (tmp / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    tmp.rename(destination)
    return export_hash


def verify_export(destination: Path) -> str:
    """Re-hash an existing export; raises if files were modified. Returns the export hash."""
    manifest_path = destination / MANIFEST
    if not manifest_path.is_file():
        raise LeanExportError(f"not a QuantLab LEAN export: {destination}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for rel, expected in manifest["files"].items():
        path = destination / rel
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise LeanExportError(f"LEAN export modified or incomplete: {rel}")
    return str(manifest["export_hash"])

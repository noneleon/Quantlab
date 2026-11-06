"""Versioned Parquet storage and DuckDB reads (DATA-003, DATA-004).

Layout (under ``data_root/parquet``)::

    <dataset>/<version_hash[:20]>/<SYMBOL>.parquet

* Version directories are written once and never modified. A new import writes a new directory.
* The **content hash** is computed from the logical rows (timestamp + float ``repr`` per value), not from
  Parquet bytes, so it is stable across pyarrow versions and operating systems. File sha256s are recorded
  separately to detect tampering/corruption.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from quantlab.data.schema import BAR_COLUMNS

_ARROW_SCHEMA = pa.schema(
    [
        ("symbol", pa.string()),
        ("ts", pa.timestamp("us", tz="UTC")),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),
    ]
)


class StorageError(RuntimeError):
    pass


def symbol_content_hash(frame: pd.DataFrame) -> str:
    """sha256 over the canonical text form of one symbol's rows (sorted by ts)."""
    digest = hashlib.sha256()
    ordered = frame.sort_values("ts", kind="stable")
    stamps = pd.to_datetime(ordered["ts"], utc=True).dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
    columns = [ordered[c].to_numpy() for c in ("open", "high", "low", "close", "volume")]
    for i, stamp in enumerate(stamps):
        digest.update(stamp.encode())
        for col in columns:
            digest.update(b"|" + repr(float(col[i])).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def version_hash(metadata: Mapping[str, Any], symbol_hashes: Mapping[str, str]) -> str:
    payload = {"metadata": dict(metadata), "symbols": dict(sorted(symbol_hashes.items()))}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_version(directory: Path, frame: pd.DataFrame) -> dict[str, str]:
    """Write one Parquet file per symbol into a *new* directory. Returns {relative file: sha256}."""
    if directory.exists():
        raise StorageError(f"version directory already exists (versions are immutable): {directory}")
    tmp = directory.with_name(directory.name + ".partial")
    if tmp.exists():
        raise StorageError(f"incomplete previous write found, inspect and remove manually: {tmp}")
    tmp.mkdir(parents=True)
    files: dict[str, str] = {}
    for symbol, group in frame.groupby("symbol", sort=True):
        table = pa.Table.from_pandas(
            group[list(BAR_COLUMNS)].sort_values("ts", kind="stable"),
            schema=_ARROW_SCHEMA,
            preserve_index=False,
        )
        path = tmp / f"{symbol}.parquet"
        pq.write_table(table, path, compression="zstd")
        files[path.name] = file_sha256(path)
    tmp.rename(directory)  # atomic publish: a version directory is either complete or absent
    return files


def verify_files(directory: Path, files: Mapping[str, str]) -> list[str]:
    """Return problems (missing/modified files); empty if the version is intact."""
    problems = []
    for name, expected in files.items():
        path = directory / name
        if not path.is_file():
            problems.append(f"missing {name}")
        elif file_sha256(path) != expected:
            problems.append(f"modified {name}")
    return problems


def read_bars(
    paths: Iterable[Path],
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Read bars from Parquet files with DuckDB, optionally filtered by [start, end] on ts."""
    import duckdb

    files = [str(p) for p in paths]
    if not files:
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in BAR_COLUMNS})
    conditions, params = [], []
    if start is not None:
        conditions.append("ts >= ?")
        params.append(start.to_pydatetime())
    if end is not None:
        conditions.append("ts <= ?")
        params.append(end.to_pydatetime())
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with duckdb.connect() as con:
        con.execute("SET TimeZone = 'UTC'")
        frame = con.execute(
            f"SELECT symbol, ts, open, high, low, close, volume FROM read_parquet(?) {where} "  # noqa: S608
            "ORDER BY symbol, ts",
            [files, *params],
        ).df()
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    return frame

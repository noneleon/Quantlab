"""Canonical market-data schema and vocabularies (DATA-003, DATA-006). See docs/DATA_DICTIONARY.md.

Canonical bar frame (pandas), one row per (symbol, ts):
    symbol  str                 ticker as used by the market (e.g. "SPY")
    ts      datetime64[UTC]     bar **end** time in UTC (LEAN convention)
    open, high, low, close      float64, **raw** prices unless the dataset declares an adjustment
    volume  float64
"""

from __future__ import annotations

import re
from enum import StrEnum

BAR_COLUMNS = ("symbol", "ts", "open", "high", "low", "close", "volume")
PRICE_COLUMNS = ("open", "high", "low", "close")


class Provenance(StrEnum):
    """Where data came from. Reports must never present anything but REAL_HISTORICAL/LIVE_RECORDED as real."""

    REAL_HISTORICAL = "real_historical"
    LIVE_RECORDED = "live_recorded"
    SYNTHETIC = "synthetic"
    SAMPLE = "sample"

    @property
    def is_real(self) -> bool:
        return self in (Provenance.REAL_HISTORICAL, Provenance.LIVE_RECORDED)


class PriceAdjustment(StrEnum):
    RAW = "raw"
    SPLIT_ADJUSTED = "split_adjusted"
    SPLIT_DIVIDEND_ADJUSTED = "split_dividend_adjusted"


class Resolution(StrEnum):
    DAILY = "daily"
    HOUR = "hour"
    MINUTE = "minute"
    SECOND = "second"

    @property
    def seconds(self) -> int:
        return {"daily": 86400, "hour": 3600, "minute": 60, "second": 1}[self.value]


class AssetClass(StrEnum):
    EQUITY = "equity"  # includes ETFs (LEAN treats them as equities)
    INDEX = "index"
    FOREX = "forex"
    CRYPTO = "crypto"


_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9._^=\-]{0,31}$")


def validate_dataset_name(name: str) -> str:
    if not _NAME.match(name):
        raise ValueError(f"invalid dataset name {name!r}: lowercase letters, digits, '.', '_' or '-'")
    return name


def normalize_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if not _SYMBOL.match(value):
        raise ValueError(f"invalid symbol {symbol!r}")
    return value

"""Risk limits file (RISK-002) — ``QUANTLAB_HOME/risk_limits.toml`` by default (``risk.limits_file``).

There are **no implicit defaults**: every limit must be stated, unknown keys are rejected, and a missing or
invalid file makes the gate reject every order. ``template()`` writes a conservative starting file that the
owner reviews. The sha256 of the file is recorded with every risk decision, so a decision can always be traced
to the exact limits.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class RiskConfigError(RuntimeError):
    pass


class RiskLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_order_quantity: float = Field(gt=0)
    max_order_notional: float = Field(gt=0)
    max_position_notional: float = Field(gt=0)  # per symbol, after the order
    max_gross_exposure_fraction: float = Field(gt=0, le=10)  # gross position value / equity, after the order
    max_open_orders: int = Field(ge=1)
    max_orders_per_minute: int = Field(ge=1)
    daily_loss_limit_fraction: float = Field(gt=0, le=1)  # equity drop vs start of day; then reduce-only
    max_quote_age_s: float = Field(gt=0)
    max_spread_bps: float = Field(gt=0)
    allow_short: bool
    require_live_quotes: bool  # True: SIMULATED / DELAYED / FROZEN quotes are refused (always True for live)
    trading_calendar: str  # e.g. "XNYS"; "none" disables the session check
    allowed_symbols: list[str] | None = None  # None: any symbol


@dataclass(frozen=True)
class LoadedLimits:
    limits: RiskLimits
    sha256: str
    path: Path


def load_limits(path: Path) -> LoadedLimits:
    if not path.is_file():
        raise RiskConfigError(
            f"no risk limits file at {path}: every order is rejected until the owner creates one "
            "(`quantlab risk init` writes a conservative template to review)"
        )
    raw = path.read_bytes()
    try:
        data = tomllib.loads(raw.decode("utf-8"))
        limits = RiskLimits(**data.get("limits", {}))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, ValidationError, TypeError) as exc:
        raise RiskConfigError(f"invalid risk limits in {path}: {exc}") from exc
    unknown = set(data) - {"limits"}
    if unknown:
        raise RiskConfigError(f"unknown sections in {path}: {sorted(unknown)}")
    return LoadedLimits(limits, hashlib.sha256(raw).hexdigest(), path)


TEMPLATE = """# QuantLab risk limits (RISK-002).
# Review every value before trading; these are conservative examples.
# Every order is checked against this file by the central risk gate; its sha256 is stored with each decision.
[limits]
max_order_quantity = 100            # shares per order
max_order_notional = 5000.0         # account currency per order
max_position_notional = 10000.0     # per symbol, after the order
max_gross_exposure_fraction = 0.5   # gross position value / equity, after the order
max_open_orders = 5
max_orders_per_minute = 5
daily_loss_limit_fraction = 0.02    # 2 % below start-of-day equity -> only position-reducing orders
max_quote_age_s = 15.0
max_spread_bps = 50.0
allow_short = false
require_live_quotes = false         # paper with simulated quotes; live trading always requires LIVE quotes
trading_calendar = "XNYS"           # orders only during the regular session ("none" disables the check)
# allowed_symbols = ["SPY", "QQQ"]
"""


def write_template(path: Path) -> Path:
    if path.exists():
        raise RiskConfigError(f"{path} already exists; it is never overwritten")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE, encoding="utf-8")
    load_limits(path)  # the template itself must be valid
    return path

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

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


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
    max_drawdown_fraction: float = Field(
        gt=0, le=1
    )  # equity drop vs the recorded high-water mark; reduce-only
    max_rejections_per_hour: int = Field(
        ge=1
    )  # this many rejected orders in an hour -> cooldown (no new orders)
    allow_margin: bool  # False: buys incl. working buy orders must be covered by cash (reserved buying power)
    max_quote_age_s: float = Field(gt=0)
    max_spread_bps: float = Field(gt=0)
    allow_short: bool
    require_live_quotes: bool  # True: SIMULATED / DELAYED / FROZEN quotes are refused (always True for live)
    trading_calendar: str  # e.g. "XNYS"; "none" disables the session check
    allowed_symbols: list[str] | None = None  # None: any symbol
    # Cash account (RISK-006): sale proceeds are spendable after this many trading days (US equities: T+1).
    # Optional: older files get the safe default; 0 treats proceeds as settled at once.
    settlement_days: int = Field(default=1, ge=0, le=3)

    @model_validator(mode="after")
    def _cash_accounts_cannot_short(self) -> RiskLimits:
        if self.allow_short and not self.allow_margin:
            raise ValueError(
                "allow_short = true needs allow_margin = true (a cash account cannot sell short)"
            )
        return self


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
        raise RiskConfigError(
            f"invalid risk limits in {path}: {exc}\n(limits added in newer versions must be stated too; "
            "`quantlab risk template` prints the current template)"
        ) from exc
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
max_drawdown_fraction = 0.10        # 10 % below the high-water mark (daily granularity) -> reduce-only
max_rejections_per_hour = 10        # cooldown: no new orders while this many were rejected in the last hour
allow_margin = false                # cash account: buys (incl. working buys) must be covered by cash
max_quote_age_s = 15.0
max_spread_bps = 50.0
allow_short = false
require_live_quotes = false         # paper with simulated quotes; live trading always requires LIVE quotes
trading_calendar = "XNYS"           # orders only during the regular session ("none" disables the check)
settlement_days = 1                 # cash account: buys only with settled cash; sale proceeds settle T+1
# allowed_symbols = ["SPY", "QQQ"]
"""


def write_template(path: Path) -> Path:
    if path.exists():
        raise RiskConfigError(f"{path} already exists; it is never overwritten")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE, encoding="utf-8")
    load_limits(path)  # the template itself must be valid
    return path

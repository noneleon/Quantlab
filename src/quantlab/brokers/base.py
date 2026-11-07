"""Broker adapter protocol and the event types adapters emit (BRK-001).

Adapters know nothing about the OMS, risk or portfolio: they accept ``BrokerOrder`` requests and report what
the broker did as ``BrokerEvent`` objects. Fills (``ExecutionReport``) are the only source of position truth
(DEC-0008). A submission whose outcome is unknown (timeout, disconnect) raises ``SubmitUnknown``: the OMS then
marks the order ``SUBMIT_UNKNOWN`` and resolves it only through reconciliation — it never resubmits
automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(StrEnum):
    DAY = "day"
    GTC = "gtc"


class BrokerError(RuntimeError):
    """The broker refused the request (definite outcome)."""


class BrokerUnavailable(BrokerError):
    """Not connected; nothing was sent."""


class SubmitUnknown(RuntimeError):
    """The request may or may not have reached the broker (timeout / disconnect mid-request)."""


@dataclass(frozen=True)
class BrokerOrder:
    order_id: str  # QuantLab order id; adapters pass it to the broker as the client reference
    symbol: str
    side: Side
    quantity: float  # positive
    order_type: OrderType
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: TimeInForce = TimeInForce.DAY


@dataclass(frozen=True)
class OrderAccepted:
    order_id: str
    broker_order_id: str
    ts: datetime


@dataclass(frozen=True)
class OrderRejected:
    order_id: str
    reason: str
    ts: datetime


@dataclass(frozen=True)
class ExecutionReport:
    order_id: str
    exec_id: str  # globally unique for the broker account; a replayed report is ignored by the OMS
    symbol: str
    side: Side
    quantity: float  # positive, this fill only
    price: float
    commission: float
    ts: datetime
    # Data state of the price behind the fill: "LIVE" for a real broker execution; the paper broker reports
    # the state of the quote it used (e.g. "SIMULATED"), so simulated fills stay labelled end to end.
    price_state: str = "LIVE"


@dataclass(frozen=True)
class OrderCancelled:
    order_id: str
    ts: datetime


@dataclass(frozen=True)
class OrderExpired:
    order_id: str
    ts: datetime


BrokerEvent = OrderAccepted | OrderRejected | ExecutionReport | OrderCancelled | OrderExpired


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    quantity: float
    average_price: float


@dataclass(frozen=True)
class SequencedEvent:
    """A broker event with its broker-assigned sequence number (acknowledged by number, never by count)."""

    seq: int
    event: BrokerEvent


@dataclass(frozen=True)
class BrokerSnapshot:
    """Broker facts read together, at one instant (reconciliation compares against a consistent view)."""

    open_order_ids: frozenset[str]
    executions: tuple[ExecutionReport, ...]
    positions: tuple[BrokerPosition, ...]
    cash: float


@runtime_checkable
class BrokerAdapter(Protocol):
    name: str
    mode: str  # "paper" | "live" — QuantLab refuses live adapters until LIVE-003 (DEC-0010)

    def submit(self, order: BrokerOrder) -> None:
        """Send an order. Raises BrokerError / BrokerUnavailable (definitely not placed) or SubmitUnknown."""
        ...

    def cancel(self, order_id: str) -> None: ...

    def events(self) -> list[SequencedEvent]:
        """Unacknowledged events in broker order (returned again until ``acknowledge`` covers them)."""
        ...

    def acknowledge(self, through_seq: int) -> None:
        """The caller durably processed every event numbered up to and including ``through_seq``."""
        ...

    def positions(self) -> list[BrokerPosition]: ...

    def cash(self) -> float: ...

    def open_order_ids(self) -> set[str]:
        """QuantLab order ids the broker considers working (reconciliation)."""
        ...

    def executions(self) -> list[ExecutionReport]:
        """All executions the broker knows for this session/account (reconciliation)."""
        ...

    def snapshot(self) -> BrokerSnapshot:
        """Open order ids, executions, positions and cash from one consistent read (reconciliation)."""
        ...

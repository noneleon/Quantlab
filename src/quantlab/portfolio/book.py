"""Portfolio book from confirmed fills (PORT-001/002, DEC-0008).

A ``Book`` is a pure projection of execution reports (never of submitted orders): positions with average cost,
cash, realised P&L (average-cost method) and commissions. Marking to quotes gives market value, unrealised
P&L, equity and gross exposure. The OMS feeds it the fills it has stored; reconciliation compares it with the
broker.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Position:
    symbol: str
    quantity: float = 0.0
    average_price: float = 0.0
    realized_pnl: float = 0.0


@dataclass(frozen=True)
class Fill:
    symbol: str
    quantity: float  # signed: + buy, - sell
    price: float
    commission: float


@dataclass
class Book:
    starting_cash: float
    cash: float = 0.0
    commissions: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)

    @classmethod
    def from_fills(cls, starting_cash: float, fills: Iterable[Fill]) -> Book:
        book = cls(starting_cash, cash=starting_cash)
        for f in fills:
            book.apply(f)
        return book

    def apply(self, f: Fill) -> None:
        p = self.positions.setdefault(f.symbol, Position(f.symbol))
        self.cash -= f.quantity * f.price + f.commission
        self.commissions += f.commission
        same_direction = p.quantity == 0 or (p.quantity > 0) == (f.quantity > 0)
        if same_direction:
            total = p.quantity + f.quantity
            p.average_price = (p.average_price * abs(p.quantity) + f.price * abs(f.quantity)) / abs(total)
            p.quantity = total
            return
        closing = min(abs(f.quantity), abs(p.quantity))
        direction = 1.0 if p.quantity > 0 else -1.0
        p.realized_pnl += (f.price - p.average_price) * closing * direction
        remaining = p.quantity + f.quantity
        if abs(remaining) < 1e-12:
            p.quantity, p.average_price = 0.0, 0.0
        elif (remaining > 0) == (p.quantity > 0):
            p.quantity = remaining  # partial close keeps the average cost
        else:
            p.quantity, p.average_price = remaining, f.price  # flipped: new position at the fill price

    def snapshot(self, marks: Mapping[str, float] | None = None) -> dict[str, Any]:
        marks = marks or {}
        rows = []
        market_value = gross = unrealized = 0.0
        for sym, p in sorted(self.positions.items()):
            if p.quantity == 0 and p.realized_pnl == 0:
                continue
            mark = marks.get(sym)
            value = p.quantity * mark if mark is not None else None
            upnl = (mark - p.average_price) * p.quantity if mark is not None else None
            if value is not None and upnl is not None:
                market_value += value
                gross += abs(value)
                unrealized += upnl
            rows.append({**asdict(p), "mark": mark, "market_value": value, "unrealized_pnl": upnl})
        unmarked = [r["symbol"] for r in rows if r["quantity"] and r["mark"] is None]
        equity = self.cash + market_value if not unmarked else None
        return {
            "starting_cash": self.starting_cash,
            "cash": self.cash,
            "commissions": self.commissions,
            "positions": rows,
            "market_value": market_value if not unmarked else None,
            "gross_exposure": gross if not unmarked else None,
            "unrealized_pnl": unrealized if not unmarked else None,
            "realized_pnl": sum(p.realized_pnl for p in self.positions.values()),
            "equity": equity,
            "unmarked_symbols": unmarked,
        }

    def quantity(self, symbol: str) -> float:
        p = self.positions.get(symbol)
        return p.quantity if p else 0.0

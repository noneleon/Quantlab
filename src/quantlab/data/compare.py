"""Cross-provider dataset comparison (DATA-020): evidence for "compatible results" across providers.

Two datasets (e.g. Tiingo vs IBKR history of the same symbols) are compared per symbol on their common bar
end times: coverage, rows only in one source, relative close differences and volume ratio. If the sources
share session dates but almost no timestamps, a timestamp-convention mismatch is reported instead of a silent
"no overlap".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SymbolComparison:
    symbol: str
    rows_a: int
    rows_b: int
    overlap: int
    only_a: int
    only_b: int
    first_common: str | None
    last_common: str | None
    close_rel_diff_mean: float | None
    close_rel_diff_max: float | None
    volume_ratio_median: float | None  # b / a (IEX-only vs consolidated volume differs by design)
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compare_bars(a: pd.DataFrame, b: pd.DataFrame, *, tolerance: float = 0.001) -> list[SymbolComparison]:
    """Compare two canonical bar frames symbol by symbol."""
    results = []
    for symbol in sorted(set(a["symbol"]) | set(b["symbol"])):
        sa = a[a["symbol"] == symbol].drop_duplicates("ts").set_index("ts")
        sb = b[b["symbol"] == symbol].drop_duplicates("ts").set_index("ts")
        common = sa.index.intersection(sb.index)
        notes: list[str] = []
        if sa.empty or sb.empty:
            notes.append("symbol present in only one dataset")
        rel = vol = None
        if len(common):
            ca, cb = sa.loc[common, "close"], sb.loc[common, "close"]
            rel = (cb - ca).abs() / ca.abs()
            va, vb = sa.loc[common, "volume"], sb.loc[common, "volume"]
            ratio = (vb / va).replace([np.inf, -np.inf], np.nan).dropna()
            vol = float(ratio.median()) if not ratio.empty else None
            if float(rel.max()) > tolerance:
                notes.append(
                    f"{int((rel > tolerance).sum())} common bar(s) differ by more than {tolerance:.2%}"
                )
        elif not sa.empty and not sb.empty:
            days_a = set(pd.to_datetime(sa.index, utc=True).date)
            days_b = set(pd.to_datetime(sb.index, utc=True).date)
            if days_a & days_b:
                notes.append("same dates but no common timestamps: check bar-start vs bar-end conventions")
        results.append(
            SymbolComparison(
                symbol=str(symbol),
                rows_a=len(sa),
                rows_b=len(sb),
                overlap=len(common),
                only_a=len(sa.index.difference(sb.index)),
                only_b=len(sb.index.difference(sa.index)),
                first_common=common.min().isoformat() if len(common) else None,
                last_common=common.max().isoformat() if len(common) else None,
                close_rel_diff_mean=float(rel.mean()) if rel is not None else None,
                close_rel_diff_max=float(rel.max()) if rel is not None else None,
                volume_ratio_median=vol,
                notes=tuple(notes),
            )
        )
    return results

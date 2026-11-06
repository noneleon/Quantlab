"""Parser for the result files LEAN writes in a local backtest (ENG-005).

Files in the results folder (named by ``algorithm-id``):
    <id>.json               full result: charts, orders, statistics, runtimeStatistics, state,
                            algorithmConfiguration
    <id>-summary.json       summary (statistics), written at the end
    <id>-order-events.json  order events

Format knowledge comes from LEAN's result handler and order enums (Common/Orders/OrderTypes.cs) and
the Lean CLI's local-result reader, both checked 2026-11-06. Values are kept as LEAN reports them;
QuantLab's own metrics are computed separately in ``quantlab.analytics`` and cross-checked.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quantlab.engine.base import EngineOrder, EngineResult

EQUITY_CHART = "Strategy Equity"
EQUITY_SERIES = "Equity"

# LEAN enums (Common/Orders/OrderTypes.cs).
ORDER_TYPES = (
    "Market",
    "Limit",
    "StopMarket",
    "StopLimit",
    "MarketOnOpen",
    "MarketOnClose",
    "OptionExercise",
    "LimitIfTouched",
    "ComboMarket",
    "ComboLimit",
    "ComboLegLimit",
    "TrailingStop",
)
ORDER_DIRECTIONS = ("Buy", "Sell", "Hold")
ORDER_STATUSES = {
    0: "New",
    1: "Submitted",
    2: "PartiallyFilled",
    3: "Filled",
    5: "Canceled",
    6: "None",
    7: "Invalid",
    8: "CancelPending",
    9: "UpdateSubmitted",
}
FINISHED_STATUSES = frozenset(
    {"Completed", "RuntimeError", "Runtime Error", "Stopped", "Liquidated", "Deleted"}
)


class ResultParseError(ValueError):
    """Result files missing, incomplete (engine still writing / crashed) or malformed."""


def _parse_time(value: Any) -> datetime | None:
    """LEAN times are unix seconds or ISO-8601 strings; naive strings are UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ResultParseError(f"invalid time value {value!r}")
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResultParseError(f"invalid time value {value!r}") from exc
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _point(value: Any) -> tuple[datetime, float] | None:
    """Chart points are [t, v], [t, o, h, l, c] (close used) or {"x": t, "y": v}."""
    if isinstance(value, Mapping):
        t, v = value.get("x"), value.get("y")
    elif isinstance(value, list) and len(value) >= 2:
        t, v = value[0], value[-1]
    else:
        return None
    if t is None or v is None:
        return None
    ts = _parse_time(t)
    return (ts, float(v)) if ts is not None else None


def _equity(charts: Mapping[str, Any]) -> tuple[tuple[datetime, float], ...]:
    series = ((charts.get(EQUITY_CHART) or {}).get("series") or {}).get(EQUITY_SERIES) or {}
    points = [p for p in (_point(v) for v in series.get("values") or []) if p is not None]
    return tuple(sorted(points, key=lambda p: p[0]))


def _enum(table: tuple[str, ...], value: Any) -> str:
    if isinstance(value, int) and 0 <= value < len(table):
        return table[value]
    return str(value)


def _order(data: Mapping[str, Any]) -> EngineOrder:
    symbol = data.get("symbol") or {}
    status = data.get("status")
    return EngineOrder(
        id=int(data.get("id") or 0),
        time=_parse_time(data.get("time")),
        symbol=str(symbol.get("value", "")) if isinstance(symbol, Mapping) else str(symbol),
        type=_enum(ORDER_TYPES, data.get("type") or 0),
        direction=_enum(ORDER_DIRECTIONS, data.get("direction") or 0),
        quantity=float(data.get("quantity") or 0.0),
        price=float(data.get("price") or 0.0),
        status=ORDER_STATUSES.get(status, str(status)) if isinstance(status, int) else str(status),
        tag=str(data.get("tag") or ""),
    )


def _orders(raw: Any) -> tuple[EngineOrder, ...]:
    """Local results store orders keyed by id; tolerate a list as well."""
    items = list(raw.values()) if isinstance(raw, Mapping) else list(raw or [])
    return tuple(sorted((_order(o) for o in items), key=lambda o: o.id))


def _str_map(raw: Any) -> dict[str, str]:
    return {str(k): str(v) for k, v in (raw or {}).items()} if isinstance(raw, Mapping) else {}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ResultParseError(f"result file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ResultParseError(f"result file is incomplete or malformed: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ResultParseError(f"unexpected result file structure in {path}")
    return data


def parse_result_data(
    data: Mapping[str, Any], algorithm_id: str, source_files: tuple[Path, ...] = ()
) -> EngineResult:
    state = data.get("state") or {}
    configuration = data.get("algorithmConfiguration") or {}
    status = str(state.get("Status") or "Running")
    return EngineResult(
        algorithm_id=algorithm_id,
        status=status,
        finished=status in FINISHED_STATUSES,
        runtime_error=str(state.get("RuntimeError") or ""),
        statistics=_str_map(data.get("statistics")),
        runtime_statistics=_str_map(data.get("runtimeStatistics")),
        orders=_orders(data.get("orders")),
        equity=_equity(data.get("charts") or {}),
        start=_parse_time(configuration.get("startDate")),
        end=_parse_time(configuration.get("endDate")),
        parameters=_str_map(configuration.get("parameters")),
        source_files=source_files,
    )


def parse_results_dir(results_dir: Path, algorithm_id: str) -> EngineResult:
    """Parse ``<algorithm_id>.json`` (required) and merge statistics from ``-summary.json`` if present."""
    main = results_dir / f"{algorithm_id}.json"
    data = _load_json(main)
    sources: list[Path] = [main]
    summary_path = results_dir / f"{algorithm_id}-summary.json"
    if summary_path.is_file():
        summary = _load_json(summary_path)
        sources.append(summary_path)
        if not data.get("statistics") and summary.get("statistics"):
            data = {**data, "statistics": summary["statistics"]}
    return parse_result_data(data, algorithm_id, tuple(sources))


_NUMBER = re.compile(
    r"^\s*(?P<sign>-)?\s*\$?\s*(?P<num>-?[0-9][0-9,]*(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?)\s*(?P<pct>%)?\s*$"
)


def stat_value(text: str) -> float | None:
    """Convert a LEAN statistic string to a float: '-0.160%' -> -0.0016, '$99,839.57' -> 99839.57.

    Percentages are returned as fractions. Returns None for non-numeric values (e.g. 'N/A').
    """
    match = _NUMBER.match(text)
    if match is None:
        return None
    value = float(match.group("num").replace(",", ""))
    if match.group("sign"):
        value = -value
    return value / 100.0 if match.group("pct") else value

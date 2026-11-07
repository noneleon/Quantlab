"""Shared CLI helpers: options, error handling, context management, JSON output."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer

from quantlab.backtest.service import BacktestError
from quantlab.bootstrap import AppContext, BootstrapError
from quantlab.core.config import ConfigError
from quantlab.data.calendars import CalendarError
from quantlab.data.catalog import DataError
from quantlab.data.lean_export import LeanExportError
from quantlab.data.market_data import MarketDataError
from quantlab.data.providers.base import ProviderError
from quantlab.data.storage import StorageError
from quantlab.engine import EngineError
from quantlab.strategies.registry import StrategyError
from quantlab.strategies.snapshot import SnapshotError

HomeOpt = Annotated[
    Path | None, typer.Option("--home", help="QuantLab home (default: QUANTLAB_HOME / OS default).")
]
JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable JSON output.")]

# Errors that are the user's to fix: reported as one line, exit code 1, no traceback.
EXPECTED: tuple[type[Exception], ...] = (
    BootstrapError,
    ConfigError,
    StrategyError,
    SnapshotError,
    BacktestError,
    EngineError,
    DataError,
    ProviderError,
    CalendarError,
    StorageError,
    LeanExportError,
    MarketDataError,
)


def fail(message: str) -> NoReturn:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


@contextmanager
def context(home: Path | None) -> Iterator[AppContext]:
    try:
        ctx = AppContext.open(home)
    except EXPECTED as exc:
        fail(str(exc))
    try:
        yield ctx
    except EXPECTED as exc:
        fail(str(exc))
    finally:
        ctx.close()


def emit_json(obj: Any) -> None:
    typer.echo(json.dumps(obj, indent=2, sort_keys=True, default=str))

"""Research API for notebooks (RES-002).

    from quantlab.research import Research
    qr = Research.open()                       # uses QUANTLAB_HOME
    bars = qr.history("spy-daily", symbols=["SPY"], start="2020-01-01")
    closes = qr.closes("spy-daily")            # wide frame: index = ts (UTC), columns = symbols
    stats = qr.distribution(closes["SPY"])     # quantlab.analytics.statistics
    m = qr.metrics(closes["SPY"])              # quantlab.analytics.metrics (same code as backtests)

Rules this module guarantees:
* Data comes only from the versioned catalog — the same versions backtests use (no second data path).
* Every frame carries ``frame.attrs["quantlab"] = {dataset, version, provenance, price_adjustment}``.
* Non-real provenance (synthetic/sample) prints a warning when loaded, so it is never mistaken for real data.
* Read-only: nothing here imports, modifies or deletes data, runs backtests or touches trading.
"""

from __future__ import annotations

import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from quantlab.analytics import statistics
from quantlab.analytics.metrics import EquityMetrics, equity_metrics
from quantlab.bootstrap import AppContext
from quantlab.data.corporate_actions import Adjustment
from quantlab.data.schema import Provenance


class NonRealDataWarning(UserWarning):
    """Emitted when research loads data whose provenance is not real (synthetic or sample)."""


def _ts(value: str | datetime | pd.Timestamp | None) -> datetime | None:
    if value is None:
        return None
    ts = pd.Timestamp(value)
    utc: datetime = (ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")).to_pydatetime()
    return utc


class Research:
    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx

    @classmethod
    def open(cls, home: Path | str | None = None) -> Research:
        return cls(AppContext.open(home, configure_logging=False))

    def close(self) -> None:
        self._ctx.close()

    def __enter__(self) -> Research:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ---- catalog -------------------------------------------------------------------------------
    def datasets(self) -> pd.DataFrame:
        rows = []
        with self._ctx.sessions() as session:
            for d in self._ctx.data.list_datasets(session):
                latest = self._ctx.data.latest(session, d)
                rows.append(
                    {
                        **d.identity(),
                        "source": d.source,
                        "versions": len(self._ctx.data.versions(session, d)),
                        "latest_version": latest.content_hash[:20] if latest else None,
                        "rows": latest.row_count if latest else 0,
                    }
                )
        return pd.DataFrame(rows)

    def history(
        self,
        dataset: str,
        *,
        symbols: list[str] | None = None,
        start: Any = None,
        end: Any = None,
        version: str | None = None,
        adjust: Adjustment = "raw",
    ) -> pd.DataFrame:
        """Canonical bars of a dataset version; ``adjust`` = raw | split | split_dividend (back-adjusted)."""
        return self._load(
            dataset, symbols=symbols, start=start, end=end, version=version, adjust=adjust, stacklevel=3
        )

    def _load(
        self,
        dataset: str,
        *,
        symbols: list[str] | None,
        start: Any,
        end: Any,
        version: str | None,
        adjust: Adjustment,
        stacklevel: int,
    ) -> pd.DataFrame:
        with self._ctx.sessions() as session:
            ds = self._ctx.data.get(session, dataset)
            frame, resolved = self._ctx.data.read(
                session,
                dataset,
                version=version,
                symbols=symbols,
                start=_ts(start),
                end=_ts(end),
                adjust=adjust,
            )
            meta = {
                "dataset": ds.name,
                "version": resolved.content_hash,
                "provenance": ds.provenance,
                "price_adjustment": ds.price_adjustment,
                "resolution": ds.resolution,
                "adjustment": adjust,
            }
        if not Provenance(meta["provenance"]).is_real:
            warnings.warn(
                f"dataset {dataset!r} has provenance {meta['provenance']!r}: not real market data",
                NonRealDataWarning,
                stacklevel=stacklevel,  # point at the notebook cell, not this module
            )
        frame.attrs["quantlab"] = meta
        return frame

    def closes(self, dataset: str, **kwargs: Any) -> pd.DataFrame:
        """Wide close-price frame: index = ts (UTC), one column per symbol (``adjust=`` as in ``history``)."""
        bars = self._load(
            dataset,
            symbols=kwargs.get("symbols"),
            start=kwargs.get("start"),
            end=kwargs.get("end"),
            version=kwargs.get("version"),
            adjust=kwargs.get("adjust", "raw"),
            stacklevel=3,
        )
        wide = bars.pivot(index="ts", columns="symbol", values="close").sort_index()
        wide.columns.name = None
        wide.attrs["quantlab"] = bars.attrs["quantlab"]
        return wide

    # ---- analytics (delegates; no formulas here) ------------------------------------------------
    @staticmethod
    def returns(prices: pd.DataFrame | pd.Series, kind: str = "simple") -> Any:
        return statistics.returns(prices, kind=kind)  # type: ignore[arg-type]

    @staticmethod
    def distribution(series: pd.Series, *, is_returns: bool = False, **kwargs: Any) -> dict[str, float]:
        """Distribution statistics of a price series' returns (``is_returns=True`` for a return series)."""
        r = series if is_returns else statistics.returns(series)
        return statistics.distribution(r, **kwargs)

    @staticmethod
    def correlation(prices: pd.DataFrame, **kwargs: Any) -> pd.DataFrame:
        return statistics.correlation(statistics.returns(prices), **kwargs)

    @staticmethod
    def rolling_volatility(prices: pd.Series | pd.DataFrame, **kwargs: Any) -> Any:
        return statistics.rolling_volatility(statistics.returns(prices), **kwargs)

    @staticmethod
    def metrics(series: pd.Series, **kwargs: Any) -> EquityMetrics:
        """Same equity-curve metrics as backtests (``quantlab.analytics.metrics``) for any value series."""
        clean = series.dropna()
        points = [(pd.Timestamp(ts).to_pydatetime(), float(v)) for ts, v in clean.items()]
        return equity_metrics(points, **kwargs)

    # ---- backtest history (read-only) ----------------------------------------------------------
    def backtests(self, strategy: str | None = None, limit: int = 100) -> pd.DataFrame:
        rows = []
        for run in self._ctx.read_only_backtests().list(strategy, limit):
            metrics = run.json("metrics") or {}
            rows.append(
                {
                    "id": run.id,
                    "status": run.status,
                    "created_at": run.created_at,
                    "data_provenance": run.data_provenance,
                    "data_version": run.data_version,
                    "total_return": metrics.get("total_return"),
                    "sharpe": metrics.get("sharpe"),
                    "max_drawdown": metrics.get("max_drawdown"),
                    "rerun_of": run.rerun_of,
                }
            )
        return pd.DataFrame(rows)

    def actions(self, dataset: str, version: str | None = None) -> pd.DataFrame:
        """Corporate actions recorded in a dataset version."""
        with self._ctx.sessions() as session:
            return self._ctx.data.actions(session, dataset, version)

    def backtest(self, run_id: str) -> dict[str, Any]:
        run = self._ctx.read_only_backtests().get(run_id)
        return {
            "id": run.id,
            "status": run.status,
            "manifest": run.json("manifest"),
            "metrics": run.json("metrics"),
            "cross_checks": run.json("cross_checks"),
            "engine_statistics": run.json("engine_statistics"),
        }

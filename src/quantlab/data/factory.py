"""Build market-data providers from configuration (DEC-0021).

Switching from Tiingo (development) to IBKR (production) is a configuration change only:

    [market_data]
    historical = "ibkr"
    realtime = "ibkr"
    require_live = true

Strategies are unaffected: backtests read catalog versions (whatever provider filled them) and live trading
will consume ``RealtimeProvider`` quotes through the OMS (Phase 5).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from quantlab.core.config import Settings
from quantlab.core.secret_store import SecretNotFoundError, SecretStore
from quantlab.data.catalog import DataCatalog
from quantlab.data.market_data import MarketDataError, QuoteGuard, RealtimeProvider
from quantlab.data.providers.base import DataProvider, ProviderError
from quantlab.data.providers.ibkr import IBKRProvider


def ibkr_provider(settings: Settings, calendar: str = "XNYS") -> IBKRProvider:
    """Read-only market-data connection to TWS / IB Gateway (paper port while trading.mode is paper)."""
    from quantlab.data.providers.ib_async_client import IbAsyncClient

    ib = settings.brokers.ibkr
    port = ib.port_paper if settings.trading.mode == "paper" else ib.port_live
    md = settings.market_data
    return IBKRProvider(
        IbAsyncClient(),
        host=ib.host,
        port=port,
        client_id=ib.market_data_client_id,
        calendar=calendar,
        data_type=md.ibkr_data_type,
        require_live=md.require_live,
        stale_after_s=md.stale_after_s,
    )


def historical_provider(
    settings: Settings, secrets: SecretStore, calendar: str, name: str | None = None
) -> DataProvider:
    """The configured (or explicitly named) historical provider."""
    choice = name or settings.market_data.historical
    if choice == "tiingo":
        from quantlab.data.providers.tiingo import API_KEY_REF, TiingoProvider

        try:
            key = secrets.get(API_KEY_REF)
        except SecretNotFoundError as exc:
            raise ProviderError(
                "Tiingo API key not configured; run `quantlab secret set tiingo/api-key`"
            ) from exc
        return TiingoProvider(key, calendar=calendar)
    if choice == "ibkr":
        return ibkr_provider(settings, calendar)
    raise ProviderError(f"unknown historical provider {choice!r}")


def realtime_provider(
    settings: Settings,
    catalog: DataCatalog,
    session: Session,
    *,
    name: str | None = None,
    dataset: str | None = None,
) -> RealtimeProvider:
    """The configured (or explicitly named) real-time provider."""
    choice = name or settings.market_data.realtime
    if choice == "mock":
        source = dataset or settings.market_data.mock_dataset
        if source is None:
            raise MarketDataError("mock streaming needs a dataset (market_data.mock_dataset or --dataset)")
        from quantlab.data.providers.mock_stream import MockStreamingProvider

        frame, version = catalog.read(session, source)
        return MockStreamingProvider(frame, source=f"{source}@{version.content_hash[:12]}")
    if choice == "ibkr":
        return ibkr_provider(settings)
    raise MarketDataError(f"unknown real-time provider {choice!r}")


def quote_guard(settings: Settings, require_live: bool | None = None) -> QuoteGuard:
    return QuoteGuard.for_mode(settings.market_data.require_live if require_live is None else require_live)

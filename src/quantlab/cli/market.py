"""``quantlab md`` commands: real-time market data with an explicit data-state label on every line."""

from __future__ import annotations

from itertools import islice
from typing import Annotated

import typer

from quantlab.cli.common import HomeOpt, JsonOpt, context, emit_json, fail
from quantlab.data.market_data import DataState, DataStateError, Quote

md_app = typer.Typer(
    no_args_is_help=True, help="Real-time market data (state always shown: LIVE/DELAYED/...)."
)

STATE_COLOUR = {
    DataState.LIVE: typer.colors.GREEN,
    DataState.DELAYED: typer.colors.YELLOW,
    DataState.FROZEN: typer.colors.BLUE,
    DataState.SIMULATED: typer.colors.MAGENTA,
}
ProviderOpt = Annotated[str | None, typer.Option(help="Real-time provider (default: market_data.realtime).")]
DatasetOpt = Annotated[str | None, typer.Option(help="Dataset to replay with the mock provider.")]
RequireLive = Annotated[
    bool | None,
    typer.Option(
        "--require-live/--allow-non-live", help="Reject anything but LIVE data (default from config)."
    ),
]


def _line(q: Quote) -> str:
    def f(v: float | None) -> str:
        return "-" if v is None else f"{v:,.4f}"

    ts = q.exchange_ts.isoformat() if q.exchange_ts else "-"
    flags = f"  [{','.join(q.flags)}]" if q.flags else ""
    return (
        f"[{q.state.value:<9}] {q.symbol:<8} bid {f(q.bid)}  ask {f(q.ask)}  last {f(q.last)}  "
        f"vol {f(q.volume)}  {ts}  ({q.source}){flags}"
    )


def _as_dict(q: Quote) -> dict[str, object]:
    return {
        "symbol": q.symbol,
        "state": q.state.value,
        "bid": q.bid,
        "ask": q.ask,
        "last": q.last,
        "volume": q.volume,
        "exchange_ts": q.exchange_ts,
        "received_ts": q.received_ts,
        "source": q.source,
        "flags": list(q.flags),
    }


@md_app.command("snapshot")
def md_snapshot(
    symbols: list[str],
    provider: ProviderOpt = None,
    dataset: DatasetOpt = None,
    require_live: RequireLive = None,
    home: HomeOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Latest quote per symbol."""
    from quantlab.data.factory import quote_guard, realtime_provider

    with context(home) as ctx, ctx.sessions() as session:
        source = realtime_provider(ctx.settings, ctx.data, session, name=provider, dataset=dataset)
        guard = quote_guard(ctx.settings, require_live)
        try:
            quotes = source.snapshot(symbols)
        finally:
            source.close()
    rejected = []
    for q in quotes:
        try:
            guard.check(q)
        except DataStateError as exc:
            rejected.append(str(exc))
    if as_json:
        emit_json({"quotes": [_as_dict(q) for q in quotes], "rejected": rejected})
    else:
        for q in quotes:
            typer.secho(_line(q), fg=STATE_COLOUR[q.state])
    if rejected:
        fail("; ".join(rejected))


@md_app.command("stream")
def md_stream(
    symbols: list[str],
    count: Annotated[int, typer.Option(help="Stop after N quotes.")] = 20,
    provider: ProviderOpt = None,
    dataset: DatasetOpt = None,
    require_live: RequireLive = None,
    home: HomeOpt = None,
) -> None:
    """Stream quotes; stops on the first quote the guard rejects (never silently degrades to delayed data)."""
    from quantlab.data.factory import quote_guard, realtime_provider

    with context(home) as ctx, ctx.sessions() as session:
        source = realtime_provider(ctx.settings, ctx.data, session, name=provider, dataset=dataset)
        guard = quote_guard(ctx.settings, require_live)
        try:
            for q in islice(source.stream(symbols), count):
                try:
                    guard.check(q)
                except DataStateError as exc:
                    fail(f"stream stopped: {exc}")
                typer.secho(_line(q), fg=STATE_COLOUR[q.state])
        finally:
            source.close()


@md_app.command("status")
def md_status(provider: ProviderOpt = None, dataset: DatasetOpt = None, home: HomeOpt = None) -> None:
    """Configured providers and the state of the data they deliver."""
    from quantlab.data.factory import realtime_provider

    with context(home) as ctx, ctx.sessions() as session:
        md = ctx.settings.market_data
        typer.echo(f"historical provider: {md.historical}")
        typer.echo(f"real-time provider:  {provider or md.realtime}  (require_live={md.require_live})")
        source = realtime_provider(ctx.settings, ctx.data, session, name=provider, dataset=dataset)
        try:
            st = source.status()
        finally:
            source.close()
    state = st.state.value if st.state else "-"
    typer.secho(
        f"status: {'connected' if st.connected else 'disconnected'}  data state: {state}  {st.detail}",
        fg=STATE_COLOUR.get(st.state) if st.state else None,
    )

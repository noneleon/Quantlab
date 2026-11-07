"""``quantlab risk`` and ``quantlab paper`` — paper trading through the OMS and the central risk gate (Phase
5a).

There is no live trading command. Every order goes through the OMS (risk gate first); the kill switch can be
engaged with a reason and disengaged only with the typed confirmation phrase. These are human commands: AI
agents must not run them (CLAUDE.md §4).
"""

from __future__ import annotations

import json
from typing import Annotated, Any

import typer

from quantlab.cli.common import HomeOpt, JsonOpt, context, emit_json, fail
from quantlab.oms import queries as oms_queries

risk_app = typer.Typer(no_args_is_help=True, help="Risk limits, kill switch and risk decisions.")
kill_app = typer.Typer(no_args_is_help=True, help="Kill switch: block all new orders.")
risk_app.add_typer(kill_app, name="kill-switch")
paper_app = typer.Typer(
    no_args_is_help=True, help="Paper trading (simulated broker) through the OMS + risk gate."
)
ReasonOpt = Annotated[str, typer.Option(help="Why (recorded in the audit log).")]


@risk_app.command("init")
def risk_init(home: HomeOpt = None) -> None:
    """Write a conservative risk limits template (never overwrites). Review it before trading."""
    from quantlab.risk.limits import RiskConfigError, write_template

    with context(home) as ctx:
        try:
            path = write_template(ctx.settings.risk_limits_file)
        except RiskConfigError as exc:
            fail(str(exc))
    typer.echo(f"risk limits template: {path} — review every value")


@risk_app.command("show")
def risk_show(home: HomeOpt = None, as_json: JsonOpt = False) -> None:
    """Current limits (and their sha256) and the kill switch state."""
    from quantlab.risk.limits import RiskConfigError, load_limits

    with context(home) as ctx, ctx.sessions() as session:
        last = ctx.kill_switch().state(session)
        kill = {"engaged": bool(last and last.engaged), "reason": last.reason if last else None}
        try:
            loaded = load_limits(ctx.settings.risk_limits_file)
            limits: dict[str, Any] | None = loaded.limits.model_dump()
            info = {"file": str(loaded.path), "sha256": loaded.sha256}
        except RiskConfigError as exc:
            limits, info = None, {"error": str(exc)}
    if as_json:
        emit_json({"kill_switch": kill, "limits": limits, **info})
        return
    colour = typer.colors.RED if kill["engaged"] else typer.colors.GREEN
    typer.secho(f"kill switch: {'ENGAGED' if kill['engaged'] else 'not engaged'}", fg=colour)
    if limits is None:
        typer.secho(f"limits: {info['error']}", fg=typer.colors.RED)
        return
    typer.echo(f"limits: {info['file']} (sha256 {info['sha256'][:16]})")
    for k, v in limits.items():
        typer.echo(f"  {k:<28} {v}")


@kill_app.command("engage")
def kill_engage(reason: ReasonOpt = "", home: HomeOpt = None) -> None:
    """Block every new order immediately."""
    if not reason.strip():
        fail("--reason is required")
    with context(home) as ctx, ctx.sessions() as session:
        ctx.kill_switch().engage(session, actor="owner", reason=reason)
        session.commit()
    typer.secho("kill switch ENGAGED: no new orders will pass the risk gate", fg=typer.colors.RED)


@kill_app.command("disengage")
def kill_disengage(reason: ReasonOpt = "", home: HomeOpt = None) -> None:
    """Allow orders again. Asks interactively for the typed confirmation phrase (no command-line option)."""
    from quantlab.risk.gate import DISENGAGE_CONFIRMATION, RiskError

    confirm = typer.prompt(
        f'Type "{DISENGAGE_CONFIRMATION}" to allow new orders again', default="", show_default=False
    )
    with context(home) as ctx, ctx.sessions() as session:
        try:
            ctx.kill_switch().disengage(session, actor="owner", reason=reason, confirmation=confirm)
        except RiskError as exc:
            fail(str(exc))
        session.commit()
    typer.echo("kill switch disengaged")


@risk_app.command("decisions")
def risk_decisions(limit: int = 20, home: HomeOpt = None, as_json: JsonOpt = False) -> None:
    """Recent risk decisions (approved and rejected), newest first."""
    from sqlalchemy import select

    from quantlab.risk.models import RiskDecision

    with context(home) as ctx, ctx.sessions() as session:
        rows = list(
            session.execute(
                select(RiskDecision).order_by(RiskDecision.created_at.desc()).limit(limit)
            ).scalars()
        )
        out: list[dict[str, Any]] = [
            {
                "order_id": r.order_id,
                "approved": r.approved,
                "at": r.created_at,
                "failed": [c for c in r.json("checks") if not c["ok"]],
            }
            for r in rows
        ]
    if as_json:
        emit_json(out)
        return
    for r in out:
        status = "APPROVED" if r["approved"] else "REJECTED"
        reasons = "; ".join(f"{c['check']}: {c['detail']}" for c in r["failed"])
        typer.echo(f"{r['at']:%Y-%m-%d %H:%M:%S}  {r['order_id']}  {status}  {reasons}")


@paper_app.command("order")
def paper_order(
    symbol: str,
    side: Annotated[str, typer.Argument(help="buy | sell")],
    quantity: float,
    key: Annotated[str, typer.Option(help="Idempotency key: the same key never creates a second order.")],
    order_type: Annotated[str, typer.Option("--type", help="market | limit | stop | stop_limit")] = "market",
    limit_price: Annotated[float | None, typer.Option(help="Limit price.")] = None,
    stop_price: Annotated[float | None, typer.Option(help="Stop price.")] = None,
    tif: Annotated[str, typer.Option(help="day | gtc")] = "day",
    home: HomeOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Submit a PAPER order (simulated broker) through the OMS and the risk gate."""
    from quantlab.brokers.base import OrderType, Side, TimeInForce
    from quantlab.oms.service import OrderRequest

    try:
        request = OrderRequest(
            key,
            symbol,
            Side(side),
            quantity,
            OrderType(order_type),
            limit_price,
            stop_price,
            TimeInForce(tif),
        )
    except ValueError as exc:
        fail(str(exc))
    with context(home) as ctx, ctx.sessions() as session:
        result = ctx.paper_oms(session).submit(request)
    rejected: list[str] = result.decision.reasons if result.decision and not result.decision.approved else []
    payload: dict[str, Any] = {
        "order_id": result.order.id,
        "state": result.state.value,
        "duplicate": result.duplicate,
        "rejected_by_risk": rejected,
    }
    if as_json:
        emit_json(payload)
    else:
        typer.echo(
            f"[PAPER] order {payload['order_id']}  {payload['state']}"
            + ("  (duplicate key)" if result.duplicate else "")
        )
        for r in rejected:
            typer.secho(f"  risk: {r}", fg=typer.colors.RED)
    if result.state.value in ("RISK_REJECTED", "REJECTED"):
        raise typer.Exit(1)


@paper_app.command("cancel")
def paper_cancel(order_id: str, home: HomeOpt = None) -> None:
    """Cancel a working paper order."""
    with context(home) as ctx, ctx.sessions() as session:
        state = ctx.paper_oms(session).cancel(order_id)
    typer.echo(f"[PAPER] order {order_id}: {state.value}")


@paper_app.command("orders")
def paper_orders(limit: int = 50, home: HomeOpt = None, as_json: JsonOpt = False) -> None:
    """Paper orders and their current state (newest first)."""
    with context(home) as ctx, ctx.sessions() as session:
        oms = ctx.paper_oms(session)
        oms.process_events()
        rows = [
            {"id": o.id, "key": o.client_order_key, "symbol": o.symbol, "side": o.side, "quantity": o.quantity,  # noqa: E501
             "type": o.order_type, "state": st.value, "created_at": o.created_at}
            for o, st in oms.orders(session, limit)
        ]  # fmt: skip
    if as_json:
        emit_json(rows)
        return
    for r in rows:
        typer.echo(
            f"{r['created_at']:%Y-%m-%d %H:%M}  {r['id']}  {r['side']:<4} {r['quantity']:>8} "
            f"{r['symbol']:<6} {r['type']:<10} {r['state']}"
        )


@paper_app.command("positions")
def paper_positions(home: HomeOpt = None, as_json: JsonOpt = False) -> None:
    """Confirmed paper book (from fills only) marked to current quotes."""
    with context(home) as ctx, ctx.sessions() as session:
        oms = ctx.paper_oms(session)
        oms.process_events()
        book = oms.book(session)
        snap = book.snapshot(oms.marks([s for s, p in book.positions.items() if p.quantity]))
        warning = oms_queries.simulation_warning(oms_queries.fill_price_states(session, oms.account))
    if as_json:
        emit_json({**snap, "warning": warning})
        return
    if warning:
        typer.secho(f"warning: {warning}", fg=typer.colors.YELLOW)
    equity = "unknown (unmarked positions)" if snap["equity"] is None else f"{snap['equity']:,.2f}"
    typer.echo(f"[PAPER] cash {snap['cash']:,.2f}  equity {equity}")
    for p in snap["positions"]:
        typer.echo(
            f"  {p['symbol']:<8} {p['quantity']:>10} avg {p['average_price']:.4f}  "
            f"realized {p['realized_pnl']:.2f}"
        )


@paper_app.command("reconcile")
def paper_reconcile(home: HomeOpt = None, as_json: JsonOpt = False) -> None:
    """Compare the OMS with the paper broker: fills, unknown submissions, positions, cash, open orders."""
    with context(home) as ctx, ctx.sessions() as session:
        report = ctx.paper_oms(session).reconcile()
    data = {
        "clean": report.clean,
        "fills_added": report.fills_added,
        "resolved": report.resolved,
        "position_mismatches": report.position_mismatches,
        "cash_difference": report.cash_difference,
        "open_order_mismatches": report.open_order_mismatches,
        "anomalies": report.anomalies,
    }
    if as_json:
        emit_json(data)
    else:
        typer.echo(json.dumps(data, indent=2, default=str))
    if not report.clean:
        raise typer.Exit(1)

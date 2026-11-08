"""``quantlab risk`` and ``quantlab paper`` — paper trading through the OMS and the central risk gate (Phase
5a).

There is no live trading command. Every order goes through the OMS (risk gate first); the kill switch can be
engaged with a reason and disengaged only with the typed confirmation phrase. These are human commands: AI
agents must not run them (CLAUDE.md §4).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
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


def _interactive() -> bool:
    """True when stdin and stdout are TTYs (nothing piped in).

    Defence in depth, not proof of a human: a program driving a pseudo-terminal (``script``, ``expect``, tmux)
    also passes. The real boundary is that AI agents must never disengage the kill switch (CLAUDE.md §4), the
    typed phrase, and the audit entry; the recorded channel is self-reported by the caller (KI-0068).
    """
    return sys.stdin.isatty() and sys.stdout.isatty()


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


@risk_app.command("template")
def risk_template() -> None:
    """Print the current limits template (to add limits introduced by newer versions to your file)."""
    from quantlab.risk.limits import TEMPLATE

    typer.echo(TEMPLATE, nl=False)


@kill_app.command("engage")
def kill_engage(
    reason: ReasonOpt = "",
    cancel_orders: Annotated[
        bool,
        typer.Option(
            "--cancel-orders/--keep-orders",
            help="Also cancel every working paper order (default). Positions are never flattened.",
        ),
    ] = True,
    home: HomeOpt = None,
) -> None:
    """Block every new order immediately (and cancel working paper orders unless --keep-orders)."""
    if not reason.strip():
        fail("--reason is required")
    with context(home) as ctx:
        with ctx.sessions() as session:  # engaged and committed first: nothing below can delay it
            ctx.kill_switch().engage(session, actor="owner", reason=reason, channel="cli")
            session.commit()
        typer.secho("kill switch ENGAGED: no new orders will pass the risk gate", fg=typer.colors.RED)
        if not cancel_orders:
            return
        try:
            with ctx.sessions() as session:
                results = ctx.paper_oms(session).cancel_all(reason=f"kill switch: {reason.strip()}")
        except Exception as exc:  # the switch stays engaged; tell the owner to cancel by hand
            fail(f"kill switch is engaged, but cancelling working orders failed: {exc}")
        for oid, outcome in results.results.items():
            typer.echo(f"  {oid}: {outcome}")
        if not results.complete:
            for oid, state in results.still_working.items():
                typer.secho(f"  STILL WORKING {oid}: {state}", fg=typer.colors.RED)
            fail(
                "kill switch engaged, but some orders may still be working: run `quantlab paper reconcile` "
                "and cancel them by hand"
            )
        typer.echo(f"{len(results.results)} working order(s) cancelled; nothing left working")


@kill_app.command("disengage")
def kill_disengage(reason: ReasonOpt = "", home: HomeOpt = None) -> None:
    """Allow orders again. Asks interactively for the typed confirmation phrase (no command-line option)."""
    from quantlab.risk.gate import DISENGAGE_CONFIRMATION, RiskError

    if not _interactive():
        fail(
            "the kill switch can only be disengaged by a person at an interactive terminal "
            "(no piped input, scripts or AI tools; CLAUDE.md §4)"
        )
    confirm = typer.prompt(
        f'Type "{DISENGAGE_CONFIRMATION}" to allow new orders again', default="", show_default=False
    )
    with context(home) as ctx, ctx.sessions() as session:
        try:
            ctx.kill_switch().disengage(
                session, actor="owner", reason=reason, confirmation=confirm, channel="interactive terminal"
            )
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
    from quantlab.oms.service import OrderRequest, OrderType, Side, TimeInForce

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


@paper_app.command("supervise")
def paper_supervise(
    ticks: Annotated[
        int | None, typer.Option(help="Stop after this many ticks (default: run until Ctrl+C).")
    ] = None,
    home: HomeOpt = None,
) -> None:
    """Run the paper trading supervisor (events, reconcile, equity, DAY expiry). Never sends orders."""
    from quantlab.core.filelock import LockHeld

    with context(home) as ctx, ctx.sessions() as session:
        sup = ctx.paper_supervisor(session)
        typer.echo(
            f"[PAPER] supervising {sup.status.account} every {sup.status.interval_s}s (Ctrl+C to stop)"
        )
        try:
            sup.run(max_ticks=ticks)
        except LockHeld:
            fail(f"a supervisor is already running for {sup.status.account}")
        except KeyboardInterrupt:
            typer.echo("supervisor stopped")
    st = sup.status
    typer.echo(
        f"ticks {st.ticks} · events {st.events_applied} · last reconcile clean: {st.last_reconcile_clean}"
    )
    for e in st.errors[-5:]:
        typer.secho(f"  error: {e}", fg=typer.colors.YELLOW)


@paper_app.command("supervisor-status")
def paper_supervisor_status(home: HomeOpt = None, as_json: JsonOpt = False) -> None:
    """Heartbeat and last results of the paper trading supervisor."""
    from datetime import UTC, datetime

    from quantlab.oms.supervisor import read_status

    with context(home) as ctx:
        data = read_status(ctx.supervisor_status_file(), datetime.now(UTC))
    if as_json:
        emit_json(data)
        return
    typer.echo(f"supervisor: {'running' if data['running'] else 'NOT running'}")
    if data["status"]:
        typer.echo(json.dumps(data["status"], indent=2, default=str))


@paper_app.command("gateway")
def paper_gateway(
    deployment: Annotated[str, typer.Option(help="Deployment name (lowercase letters, digits, '-').")],
    port: Annotated[int, typer.Option(help="Local TCP port (0 = any free port).")] = 0,
    home: HomeOpt = None,
) -> None:
    """Serve the LEAN gateway (OMS-007): a LEAN algorithm's orders go through the OMS and risk gate."""
    from contextlib import ExitStack

    from quantlab.core.filelock import LockHeld, exclusive
    from quantlab.oms.gateway import Gateway, GatewayError, write_run_file

    with context(home) as ctx, ctx.sessions() as session, ExitStack() as stack:
        run_file = ctx.settings.home / "run" / f"gateway-{deployment}.json"
        try:  # one gateway per deployment: its run file and sell cap must not race another one
            stack.enter_context(exclusive(run_file.with_name(run_file.name + ".lock")))
            gw = Gateway(ctx.paper_oms(session), ctx.sessions, deployment, port=port)
        except LockHeld:
            fail(f"a gateway for {deployment} is already running")
        except GatewayError as exc:
            fail(str(exc))
        write_run_file(run_file, gw.info)
        stack.callback(run_file.unlink, missing_ok=True)  # the token is only valid while this gateway runs
        typer.echo(
            f"[PAPER] LEAN gateway for {deployment} on 127.0.0.1:{gw.info.port} "
            f"(connection details in {run_file}; Ctrl+C to stop)"
        )
        try:
            gw.serve()
        except KeyboardInterrupt:
            typer.echo("gateway stopped")


@paper_app.command("deploy")
def paper_deploy(
    strategy: Annotated[str, typer.Argument(help="Registered strategy (C# or Python), at stage 'paper'.")],
    deployment: Annotated[str, typer.Option(help="Deployment name (lowercase letters, digits, '-').")],
    max_minutes: Annotated[
        float | None,
        typer.Option(help="Stop after this many minutes (default: until the algorithm exits or Ctrl+C)."),
    ] = None,
    home: HomeOpt = None,
) -> None:
    """Run a strategy on native LEAN with every order through the QuantLab OMS and risk gate (paper)."""
    from quantlab.oms.deployment import DeploymentError, deploy
    from quantlab.oms.gateway import GatewayError

    with context(home) as ctx:
        status = ctx.lifecycle().status(strategy)
        if status["stage"] not in ("paper", "live_candidate"):
            fail(
                f"{strategy} is at stage {status['stage']!r}: promote it to 'paper' first "
                "(`quantlab strategy promote`), which records the evidence and your approval"
            )
        if status["stale"]:
            fail(f"{status['stale_note']}; re-promote before deploying")
        approved = status["history"][-1]["tree_hash"]  # the files the latest promotion approved
        with ctx.sessions() as session:
            registered = ctx.registry.get(session, strategy)
            directory, entry = registered.directory, registered.entry_file
            typer.echo(f"[PAPER] deploying {strategy} as {deployment} (Ctrl+C to stop) …")
            try:
                result = deploy(
                    ctx.paper_oms(session),
                    ctx.sessions,
                    deployment=deployment,
                    strategy=strategy,
                    strategy_dir=Path(directory),
                    entry_file=entry,
                    approved_tree_hash=approved,
                    install_dir=ctx.settings.lean_native_dir,
                    home=ctx.settings.home,
                    max_minutes=max_minutes,
                    on_started=lambda work: typer.echo(f"  LEAN started; log: {work / 'engine.log'}"),
                )
            except (DeploymentError, GatewayError) as exc:
                fail(str(exc))
    typer.echo(
        f"[PAPER] {deployment} stopped ({result.stopped_by}); LEAN exit code {result.exit_code}; "
        f"files in {result.work_dir}"
    )

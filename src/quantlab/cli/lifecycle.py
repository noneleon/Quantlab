"""Lifecycle commands under ``quantlab strategy`` (STR-004) and notebook promotion (RES-006)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from quantlab.cli.common import HomeOpt, JsonOpt, context, emit_json, fail

ReasonOpt = Annotated[str, typer.Option(help="Why (recorded in the lifecycle history and audit log).")]


def _print_gate(result: Any) -> None:
    for c in result.checks:
        mark = "ok  " if c["ok"] else "FAIL"
        typer.secho(f"  [{mark}] {c['check']}: {c['detail']}", fg=None if c["ok"] else typer.colors.RED)


def register(strategy_app: typer.Typer) -> None:
    @strategy_app.command("status")
    def status(name: str, home: HomeOpt = None, as_json: JsonOpt = False) -> None:
        """Lifecycle stage, history and the next gate."""
        with context(home) as ctx:
            st = ctx.lifecycle().status(name)
        if as_json:
            emit_json(st)
            return
        typer.echo(
            f"{name}: stage {st['stage']}  (next: {st['next_stage'] or '-'})  files {st['tree_hash'][:12]}"
        )
        if st["stale"]:
            typer.secho(f"warning: {st['stale_note']}", fg=typer.colors.YELLOW)
        for e in st["history"]:
            typer.echo(
                f"  #{e['seq']} {e['from']} → {e['to']}  {e['at']:%Y-%m-%d %H:%M}  "
                f"{e['actor']}: {e['reason']}"
            )

    @strategy_app.command("promote")
    def promote(
        name: str,
        to: Annotated[str, typer.Option("--to", help="backtested | validated | paper | live_candidate")],
        reason: ReasonOpt = "",
        run: Annotated[
            list[str] | None, typer.Option("--run", help="Evidence backtest run (backtested).")
        ] = None,
        walk_forward: Annotated[str | None, typer.Option(help="Walk-forward study id (validated).")] = None,
        sweep: Annotated[str | None, typer.Option(help="Sweep id with DSR/PBO (validated).")] = None,
        dry_run: Annotated[bool, typer.Option(help="Check the gate without recording anything.")] = False,
        home: HomeOpt = None,
        as_json: JsonOpt = False,
    ) -> None:
        """Move a strategy one stage forward if the evidence passes the gate."""
        if to in ("retired", "research"):
            fail("use `strategy retire` or `strategy demote`")
        with context(home) as ctx:
            service = ctx.lifecycle()
            if dry_run:
                result, entry = (
                    service.evaluate(name, to, runs=run, walk_forward=walk_forward, sweep=sweep),
                    None,
                )
            else:
                if not reason.strip():
                    fail("--reason is required")
                result, entry = service.promote(
                    name, to, reason=reason, runs=run, walk_forward=walk_forward, sweep=sweep
                )
        if as_json:
            emit_json({"gate": result.to_dict(), "recorded": entry is not None})
        else:
            typer.echo(f"gate {name} → {to}: {'PASSED' if result.passed else 'NOT PASSED'}")
            _print_gate(result)
            if entry is not None:
                typer.echo(f"recorded lifecycle event #{entry.seq}")
            elif dry_run and result.passed:
                typer.echo("dry run: nothing recorded")
        if not result.passed:
            raise typer.Exit(1)

    def _manual(name: str, target: str, reason: str, home: Path | None) -> None:
        if not reason.strip():
            fail("--reason is required")
        with context(home) as ctx:
            result, entry = ctx.lifecycle().promote(name, target, reason=reason)
        if entry is None:
            _print_gate(result)
            raise typer.Exit(1)
        typer.echo(f"{name}: → {target} (event #{entry.seq})")

    @strategy_app.command("demote")
    def demote(name: str, reason: ReasonOpt = "", home: HomeOpt = None) -> None:
        """Back to the research stage (e.g. after a material change)."""
        _manual(name, "research", reason, home)

    @strategy_app.command("retire")
    def retire(name: str, reason: ReasonOpt = "", home: HomeOpt = None) -> None:
        """Retire a strategy (history kept; re-open with `demote`)."""
        _manual(name, "retired", reason, home)

    @strategy_app.command("from-notebook")
    def from_notebook(
        notebook: Path,
        name: str,
        template: Annotated[
            str, typer.Option(help="Template to start the strategy from.")
        ] = "momentum_rotation",
        home: HomeOpt = None,
    ) -> None:
        """Promote a research notebook into a new strategy (notebook attached with outputs stripped)."""
        from quantlab.strategies.workspace import bundled_templates_dir, list_templates

        templates = list_templates(bundled_templates_dir())
        if template not in templates:
            fail(f"unknown template {template!r}; available: {sorted(templates)}")
        with context(home) as ctx, ctx.sessions() as session:
            s = ctx.workspace.promote_notebook(
                session,
                notebook.expanduser(),
                name,
                templates[template],
                ctx.settings.layout.strategies_dir,
                now=datetime.now(UTC).isoformat(timespec="seconds"),
            )
            session.commit()
            typer.echo(f"strategy {s.name}: {s.path} (notebook in research/, stage: research)")

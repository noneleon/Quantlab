"""``quantlab secret`` commands: manage API keys in the OS keyring (SEC-001). Values are never printed."""

from __future__ import annotations

from typing import Annotated

import typer

from quantlab.cli.common import HomeOpt, context, fail
from quantlab.core.secret_store import SecretError, SecretNotFoundError, SecretRef

secret_app = typer.Typer(no_args_is_help=True, help="Store API keys in the OS keyring (never in files).")

NameArg = Annotated[str, typer.Argument(help="Secret name below quantlab/, e.g. tiingo/api-key.")]


def _ref(name: str) -> str:
    try:
        return SecretRef.parse(f"secret://quantlab/{name.strip('/')}").uri
    except SecretError as exc:
        fail(str(exc))


@secret_app.command("set")
def secret_set(name: NameArg, home: HomeOpt = None) -> None:
    """Store a secret (prompted, hidden input) in the OS keyring. Audited without the value."""
    ref = _ref(name)
    value = typer.prompt(f"Value for {ref}", hide_input=True)
    with context(home) as ctx, ctx.sessions() as session:
        try:
            ctx.secrets.set(ref, value, session=session, actor="owner")
        except SecretError as exc:
            session.rollback()
            env = SecretRef.parse(ref).env_var
            fail(f"{exc}. Without a usable keyring, set the environment variable {env} instead.")
        session.commit()
    typer.echo(f"stored {ref} in the OS keyring")


@secret_app.command("status")
def secret_status(name: NameArg, home: HomeOpt = None) -> None:
    """Show whether a secret is configured (keyring or environment); never shows the value."""
    ref = _ref(name)
    with context(home) as ctx:
        typer.echo(f"{ref}: {'configured' if ctx.secrets.exists(ref) else 'not configured'}")


@secret_app.command("delete")
def secret_delete(name: NameArg, home: HomeOpt = None) -> None:
    """Remove a secret from the OS keyring. Audited."""
    ref = _ref(name)
    with context(home) as ctx, ctx.sessions() as session:
        try:
            ctx.secrets.delete(ref, session=session, actor="owner")
        except SecretNotFoundError:
            session.rollback()
            fail(f"{ref} is not stored in the keyring")
        session.commit()
    typer.echo(f"deleted {ref}")

"""``quantlab`` command-line interface (CORE-008). Transport only: every command calls a service.

Examples::

    quantlab init
    quantlab strategy add sma ./strategies/examples/sma_crossover
    quantlab backtest run sma -p fast=5
    quantlab backtest show <run-id>
    quantlab backtest rerun <run-id>
    quantlab data create spy --asset-class equity --market usa --resolution daily \\
        --provenance real_historical --adjustment raw --source "broker export"
    quantlab data import spy ./spy.csv --symbol SPY
    quantlab serve
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from quantlab.backtest.models import BacktestRun
from quantlab.bootstrap import AppContext, initialize
from quantlab.cli.common import EXPECTED, HomeOpt, JsonOpt, context, emit_json, fail
from quantlab.cli.data import data_app
from quantlab.cli.market import md_app
from quantlab.cli.secret_commands import secret_app

app = typer.Typer(no_args_is_help=True, add_completion=False, help="QuantLab — self-hosted quant platform.")
strategy_app = typer.Typer(no_args_is_help=True, help="Register and inspect strategies.")
backtest_app = typer.Typer(no_args_is_help=True, help="Run and inspect backtests.")
engine_app = typer.Typer(no_args_is_help=True, help="Inspect the trading engine.")
api_app = typer.Typer(no_args_is_help=True, help="Local API utilities.")
app.add_typer(strategy_app, name="strategy")
app.add_typer(backtest_app, name="backtest")
app.add_typer(data_app, name="data")
app.add_typer(engine_app, name="engine")
app.add_typer(api_app, name="api")
app.add_typer(secret_app, name="secret")
app.add_typer(md_app, name="md")


def _fmt_pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.2f}%"


def _fmt_num(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def _run_summary(run: BacktestRun) -> dict[str, Any]:
    metrics = run.json("metrics") or {}
    return {
        "id": run.id,
        "status": run.status,
        "created_at": run.created_at.isoformat(),
        "data_provenance": run.data_provenance,
        "data_version": run.data_version,
        "total_return": metrics.get("total_return"),
        "sharpe": metrics.get("sharpe"),
        "max_drawdown": metrics.get("max_drawdown"),
        "rerun_of": run.rerun_of,
    }


def _print_run(run: BacktestRun) -> None:
    metrics = run.json("metrics") or {}
    typer.echo(f"run        {run.id}")
    typer.echo(f"status     {run.status}" + (f"  (engine: {run.engine_status})" if run.engine_status else ""))
    data = (run.json("manifest") or {}).get("data", {})
    dataset = (data.get("dataset") or {}).get("name")
    label = f"{dataset} @ {run.data_version[:20]}" if dataset and run.data_version else "engine sample data"
    if run.data_provenance in ("real_historical", "live_recorded"):
        typer.echo(f"data       {label} ({run.data_provenance})")
    else:
        typer.secho(
            f"data       {label} ({run.data_provenance}: not a performance claim)", fg=typer.colors.YELLOW
        )
    typer.echo(f"parameters {json.dumps(run.json('parameters'), sort_keys=True)}")
    models = (run.json("manifest") or {}).get("execution_models")
    if models is not None:
        from quantlab.core.config import ExecutionModelSettings

        defaults = ExecutionModelSettings().model_dump()
        changed = {k: v for k, v in sorted(models.items()) if defaults.get(k) != v}
        typer.echo(
            "models     " + (", ".join(f"{k}={v}" for k, v in changed.items()) or "LEAN brokerage defaults")
        )
        results_file = Path(run.run_dir) / "results.json"
        if results_file.is_file() and json.loads(results_file.read_text()).get("models_applied") is False:
            typer.secho(
                "warning    strategy did not call quantlab_lean.configure(self): "
                "LEAN default models were used",
                fg=typer.colors.YELLOW,
            )
    if run.error:
        typer.secho(f"error      {run.error}", fg=typer.colors.RED)
    if run.runtime_error:
        typer.secho(f"runtime    {run.runtime_error}", fg=typer.colors.RED)
    if metrics:
        typer.echo(
            f"metrics    return {_fmt_pct(metrics.get('total_return'))}  "
            f"CAGR {_fmt_pct(metrics.get('cagr'))}  "
            f"Sharpe {_fmt_num(metrics.get('sharpe'))}  Sortino {_fmt_num(metrics.get('sortino'))}  "
            f"MaxDD {_fmt_pct(metrics.get('max_drawdown'))}"
        )
    for check in run.json("cross_checks") or []:
        colour = {"match": typer.colors.GREEN, "mismatch": typer.colors.YELLOW}.get(check["status"])
        typer.secho(
            f"check      {check['metric']:<14} vs LEAN '{check['engine_statistic']}': {check['status']}",
            fg=colour,
        )
    typer.echo(f"run dir    {run.run_dir}")


def _parse_params(items: list[str] | None) -> dict[str, str]:
    params: dict[str, str] = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            fail(f"parameter must be key=value, got {item!r}")
        params[key] = value
    return params


# ---- top-level -------------------------------------------------------------------------------------
@app.command()
def init(home: HomeOpt = None) -> None:
    """Create the QuantLab home, config, database and local API token (idempotent; never overwrites)."""
    try:
        report = initialize(home)
    except EXPECTED as exc:
        fail(str(exc))
    typer.echo(f"QuantLab home: {report.home}")
    typer.echo(
        "config:   " + ("created quantlab.toml" if report.created_config else "existing quantlab.toml kept")
    )
    typer.echo(f"database: migrated to revision {report.db_revision}")
    if report.token_storage == "existing":  # noqa: S105 - status value, not a secret
        typer.echo("api token: already configured")
    elif report.token_storage == "keyring":  # noqa: S105 - status value, not a secret
        typer.echo("api token: created and stored in the OS keyring (show with `quantlab api token --show`)")
    else:
        assert report.new_token is not None
        typer.secho("api token: OS keyring unavailable — the token was NOT stored.", fg=typer.colors.YELLOW)
        typer.echo("Set it as an environment variable for the API server (shown once):")
        typer.echo(f"  QUANTLAB_SECRET__API__TOKEN={report.new_token.get_secret_value()}")
    typer.echo(
        "Next: pin the LEAN image in quantlab.toml ([engine.lean] image = ...), then `quantlab engine probe`."
    )


@app.command()
def research(
    home: HomeOpt = None,
    port: Annotated[int, typer.Option(help="JupyterLab port (bound to 127.0.0.1).")] = 8888,
    no_browser: Annotated[bool, typer.Option("--no-browser", help="Do not open a browser.")] = False,
) -> None:
    """Start local JupyterLab on QUANTLAB_HOME/notebooks (needs `uv sync --extra research`)."""
    from quantlab.research.notebooks import ResearchError, launch

    with context(home) as ctx:
        resolved_home = ctx.settings.home
    try:
        code = launch(resolved_home, port=port, open_browser=not no_browser)
    except ResearchError as exc:
        fail(str(exc))
    raise typer.Exit(code)


@app.command()
def serve(
    home: HomeOpt = None,
    host: Annotated[str | None, typer.Option(help="Override server.host (validated).")] = None,
    port: Annotated[int | None, typer.Option(help="Override server.port.")] = None,
) -> None:
    """Run the local API server (127.0.0.1 by default, token required)."""
    import uvicorn

    from quantlab.api.app import create_app, default_ui_dir

    overrides: dict[str, Any] = {}
    if host is not None or port is not None:
        overrides["server"] = {k: v for k, v in {"host": host, "port": port}.items() if v is not None}
    try:
        ctx = AppContext.open(home, overrides=overrides or None)
    except EXPECTED as exc:
        fail(str(exc))
    token = ctx.api_token()
    if token is None:
        ctx.close()
        fail("no API token configured; run `quantlab init` (or set QUANTLAB_SECRET__API__TOKEN)")
    ui_dir = ctx.settings.server.ui_dir or default_ui_dir()
    server = ctx.settings.server
    display_host = f"[{server.host}]" if ":" in server.host else server.host  # IPv6 literal
    base = f"http://{display_host}:{server.port}"
    if ui_dir is not None and (ui_dir / "index.html").is_file():
        # The token travels in the URL fragment, which browsers never send to the server (not logged).
        typer.echo(f"QuantLab UI: {base}/#token={token.get_secret_value()}")
    else:
        typer.echo(f"QuantLab API: {base}/api/  (web UI not built: cd web && npm ci && npm run build)")
    try:
        uvicorn.run(create_app(ctx, ui_dir), host=server.host, port=server.port, log_config=None)
    finally:
        ctx.close()


# ---- strategy -------------------------------------------------------------------------------------
@strategy_app.command("add")
def strategy_add(name: str, path: Path, home: HomeOpt = None) -> None:
    """Register a strategy directory under NAME (the files stay where they are)."""
    with context(home) as ctx, ctx.sessions() as session:
        strategy = ctx.registry.register(session, name, path)
        session.commit()
        typer.echo(f"registered {strategy.name} -> {strategy.path} (entry {strategy.entry_file})")


@strategy_app.command("list")
def strategy_list(
    home: HomeOpt = None,
    all_: Annotated[bool, typer.Option("--all", help="Include archived strategies.")] = False,
    as_json: JsonOpt = False,
) -> None:
    """List registered strategies."""
    with context(home) as ctx, ctx.sessions() as session:
        rows = [
            {"name": s.name, "path": s.path, "entry_file": s.entry_file, "archived": s.archived}
            for s in ctx.registry.list(session, include_archived=all_)
        ]
    if as_json:
        emit_json(rows)
        return
    if not rows:
        typer.echo("no strategies registered (quantlab strategy add NAME PATH)")
    for r in rows:
        typer.echo(f"{r['name']:<24} {r['path']}" + ("  [archived]" if r["archived"] else ""))


@strategy_app.command("show")
def strategy_show(name: str, home: HomeOpt = None, as_json: JsonOpt = False) -> None:
    """Show a strategy and the version its directory currently hashes to."""
    with context(home) as ctx, ctx.sessions() as session:
        strategy = ctx.registry.get(session, name)
        version = ctx.registry.current_version(session, strategy)
        session.commit()
        info: dict[str, Any] = {
            "name": strategy.name,
            "path": strategy.path,
            "entry_file": strategy.entry_file,
            "language": strategy.language,
            "description": strategy.description,
            "archived": strategy.archived,
            "current_version": {
                "id": version.id,
                "tree_hash": version.tree_hash,
                "git_commit": version.git_commit,
                "git_dirty": version.git_dirty,
                "files": sorted(version.file_hashes),
            },
        }
    if as_json:
        emit_json(info)
        return
    for key in ("name", "path", "entry_file", "language", "description", "archived"):
        typer.echo(f"{key:<12} {info[key]}")
    v = info["current_version"]
    typer.echo(
        f"version      {v['tree_hash'][:16]}…  git {v['git_commit'] or '-'}"
        f"{' (dirty)' if v['git_dirty'] else ''}"
    )


@strategy_app.command("archive")
def strategy_archive(name: str, home: HomeOpt = None) -> None:
    """Hide a strategy from listings (nothing is deleted)."""
    with context(home) as ctx, ctx.sessions() as session:
        ctx.registry.archive(session, name)
        session.commit()
        typer.echo(f"archived {name}")


# ---- backtest -------------------------------------------------------------------------------------
@backtest_app.command("run")
def backtest_run(
    strategy: str,
    param: Annotated[
        list[str] | None, typer.Option("--param", "-p", help="Parameter override key=value.")
    ] = None,
    timeout: Annotated[float | None, typer.Option(help="Seconds before the run is stopped.")] = None,
    dataset: Annotated[
        str | None, typer.Option(help="Run on this dataset (default: engine sample data).")
    ] = None,
    dataset_version: Annotated[
        str | None, typer.Option(help="Dataset version hash prefix (default: latest).")
    ] = None,
    model: Annotated[
        list[str] | None,
        typer.Option(
            "--model",
            "-m",
            help="Execution model override key=value (e.g. fee=constant), see [backtest.models].",
        ),
    ] = None,
    home: HomeOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Run a backtest of STRATEGY now (blocks until it finishes)."""
    params = _parse_params(param)
    models = _parse_params(model)
    with context(home) as ctx:
        run = ctx.backtests().run(
            strategy,
            params,
            timeout_s=timeout,
            dataset=dataset,
            dataset_version=dataset_version,
            models=models or None,
        )
        if as_json:
            emit_json(_run_summary(run))
        else:
            _print_run(run)
    if run.status != "succeeded":
        raise typer.Exit(2)


@backtest_app.command("list")
def backtest_list(
    strategy: Annotated[str | None, typer.Option(help="Only runs of this strategy.")] = None,
    limit: int = 20,
    home: HomeOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """List recent backtests (newest first)."""
    with context(home) as ctx:
        rows = [_run_summary(r) for r in ctx.read_only_backtests().list(strategy, limit)]
    if as_json:
        emit_json(rows)
        return
    if not rows:
        typer.echo("no backtests yet")
    for r in rows:
        typer.echo(
            f"{r['id']}  {r['status']:<15} return {_fmt_pct(r['total_return']):>8}  "
            f"Sharpe {_fmt_num(r['sharpe']):>7}  MaxDD {_fmt_pct(r['max_drawdown']):>8}  "
            f"[{r['data_provenance']}]"
        )


@backtest_app.command("show")
def backtest_show(run_id: str, home: HomeOpt = None, as_json: JsonOpt = False) -> None:
    """Show one backtest in detail (use --json for the full manifest)."""
    with context(home) as ctx:
        run = ctx.read_only_backtests().get(run_id)
        if as_json:
            emit_json(
                {
                    **_run_summary(run),
                    "manifest": run.json("manifest"),
                    "metrics": run.json("metrics"),
                    "cross_checks": run.json("cross_checks"),
                    "engine_statistics": run.json("engine_statistics"),
                    "error": run.error,
                    "runtime_error": run.runtime_error,
                }
            )
        else:
            _print_run(run)


@backtest_app.command("compare")
def backtest_compare(run_a: str, run_b: str, home: HomeOpt = None, as_json: JsonOpt = False) -> None:
    """Compare two runs' results (e.g. the same strategy on Tiingo vs IBKR data), with their data sources."""
    from quantlab.backtest.service import BacktestService

    with context(home) as ctx:
        service = ctx.read_only_backtests()
        report = BacktestService.compare_results(service.get(run_a), service.get(run_b))
    if as_json:
        emit_json(report)
        return
    for side in ("a", "b"):
        info = report[side]
        typer.echo(
            f"{side.upper()} {info['id']}  {info['status']}  data {info['dataset'] or 'sample'} "
            f"({info['provenance']}) {info['start']} → {info['end']}"
        )
    if not report["same_strategy_code"] or not report["same_parameters"]:
        typer.secho(
            "warning: different strategy code or parameters — not a like-for-like comparison",
            fg=typer.colors.YELLOW,
        )
    for key, row in report["metrics"].items():
        diff = "-" if row["diff"] is None else f"{row['diff']:+.4f}"
        typer.echo(f"  {key:<18} A {row['a']!s:<22} B {row['b']!s:<22} diff {diff}")
    typer.echo(
        f"  {'total orders':<18} A {report['total_orders']['a']!s:<22} B {report['total_orders']['b']!s}"
    )


@backtest_app.command("report")
def backtest_report(
    run_id: str,
    benchmark: Annotated[str | None, typer.Option(help="Catalog dataset used as benchmark.")] = None,
    benchmark_symbol: Annotated[str | None, typer.Option(help="Symbol within the benchmark dataset.")] = None,
    home: HomeOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Write a self-contained HTML report + reproducibility bundle (zip) for a run."""
    with context(home) as ctx:
        service = ctx.read_only_backtests()
        bundle = service.build_report(run_id, benchmark=benchmark, benchmark_symbol=benchmark_symbol)
        warnings = service.chart_data(run_id, benchmark=benchmark, benchmark_symbol=benchmark_symbol)[
            "warnings"
        ]
    if as_json:
        emit_json({"bundle": str(bundle), "html": str(bundle.with_suffix(".html")), "warnings": warnings})
        return
    typer.echo(f"report: {bundle.with_suffix('.html')}")
    typer.echo(f"bundle: {bundle}")
    for w in warnings:
        typer.secho(f"warning: {w}", fg=typer.colors.YELLOW)


@backtest_app.command("rerun")
def backtest_rerun(
    run_id: str,
    timeout: Annotated[float | None, typer.Option(help="Seconds before the run is stopped.")] = None,
    home: HomeOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Re-run a backtest from its stored code snapshot and report whether it reproduced."""
    with context(home) as ctx:
        run, report = ctx.backtests().rerun(run_id, timeout_s=timeout)
        if as_json:
            emit_json(
                {
                    "rerun": _run_summary(run),
                    "reproduced": report.reproduced,
                    "inputs_identical": report.inputs_identical,
                    "results_identical": report.results_identical,
                    "differences": report.differences,
                }
            )
        else:
            _print_run(run)
            colour = typer.colors.GREEN if report.reproduced else typer.colors.RED
            typer.secho(
                f"reproduced {report.reproduced} (inputs identical {report.inputs_identical}, "
                f"results identical {report.results_identical})",
                fg=colour,
            )
            for diff in report.differences:
                typer.echo(f"  - {diff}")
    if not report.reproduced:
        raise typer.Exit(3)


# ---- engine / api ---------------------------------------------------------------------------------
@engine_app.command("probe")
def engine_probe(home: HomeOpt = None, as_json: JsonOpt = False) -> None:
    """Check that the engine is available and show exactly which build would run."""
    with context(home) as ctx:
        info = ctx.engine.probe()
    data = {
        k: getattr(info, k)
        for k in (
            "name",
            "launcher",
            "image",
            "image_id",
            "repo_digests",
            "created",
            "engine_runtime_version",
        )
    }
    if as_json:
        emit_json(data)
        return
    for key, value in data.items():
        typer.echo(f"{key:<24} {value}")


@engine_app.command("pull")
def engine_pull(home: HomeOpt = None) -> None:
    """Download the pinned LEAN image (explicit; runs never download implicitly)."""
    with context(home) as ctx:
        engine = ctx.engine
        pull = getattr(engine, "pull", None)
        if pull is None:
            fail(f"engine {engine.name!r} does not support pulling")
        typer.echo(f"pulling {ctx.settings.engine.lean.image} …")
        pull()
        typer.echo("done")


@api_app.command("token")
def api_token(
    home: HomeOpt = None,
    show: Annotated[bool, typer.Option("--show", help="Print the current token.")] = False,
    rotate: Annotated[bool, typer.Option("--rotate", help="Replace the token (audited).")] = False,
) -> None:
    """Show whether an API token is configured; optionally print or rotate it."""
    with context(home) as ctx:
        if rotate:
            token, storage = ctx.rotate_api_token()
            if storage == "unavailable":
                typer.secho(
                    "OS keyring unavailable — token NOT stored. Set it in the server environment:",
                    fg=typer.colors.YELLOW,
                )
                typer.echo(f"  QUANTLAB_SECRET__API__TOKEN={token.get_secret_value()}")
            else:
                typer.echo("token rotated and stored in the OS keyring")
            return
        current = ctx.api_token()
        if current is None:
            fail("no API token configured; run `quantlab init`")
        typer.echo(current.get_secret_value() if show else "API token configured (use --show to print it)")


def main() -> None:  # console-script entry point
    app()

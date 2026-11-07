"""``quantlab data`` commands (DATA-002/003/004/006/016). Transport only: calls ``DataCatalog``."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import typer

from quantlab.cli.common import HomeOpt, JsonOpt, context, emit_json, fail
from quantlab.data.catalog import DataValidationError
from quantlab.data.providers.base import BarRequest, LicenseInfo
from quantlab.data.providers.csv_file import CsvFileProvider, load_actions_csv
from quantlab.data.schema import AssetClass, PriceAdjustment, Provenance, Resolution

data_app = typer.Typer(no_args_is_help=True, help="Manage versioned market datasets.")


@data_app.command("create")
def data_create(
    name: str,
    asset_class: Annotated[AssetClass, typer.Option(help="Asset class.")],
    market: Annotated[str, typer.Option(help="Market, e.g. usa.")],
    resolution: Annotated[Resolution, typer.Option(help="Bar resolution.")],
    provenance: Annotated[
        Provenance, typer.Option(help="Where the data comes from (required, never guessed).")
    ],
    adjustment: Annotated[PriceAdjustment, typer.Option(help="Price adjustment of the source data.")],
    source: Annotated[str, typer.Option(help="Where the data came from, e.g. 'IBKR TWS export 2026-11-06'.")],
    license_terms: Annotated[str, typer.Option(help="Licence / terms of use.")] = "licence not stated",
    redistribution_allowed: Annotated[bool, typer.Option(help="The licence allows redistribution.")] = False,
    calendar: Annotated[str | None, typer.Option(help="Calendar code (XNYS, 24/7, weekdays, none).")] = None,
    description: str = "",
    home: HomeOpt = None,
) -> None:
    """Create an empty dataset. Provenance and adjustment are fixed for its lifetime."""
    with context(home) as ctx, ctx.sessions() as session:
        dataset = ctx.data.create_dataset(
            session,
            name,
            asset_class=asset_class,
            market=market,
            resolution=resolution,
            provenance=provenance,
            price_adjustment=adjustment,
            source=source,
            license=LicenseInfo(license_terms, redistribution_allowed),
            calendar=calendar,
            description=description,
        )
        session.commit()
        typer.echo(
            f"created dataset {dataset.name} ({dataset.asset_class}/{dataset.market}/{dataset.resolution}, "
            f"calendar {dataset.calendar}, provenance {dataset.provenance})"
        )


@data_app.command("import")
def data_import(
    name: str,
    file: Path,
    symbol: Annotated[str | None, typer.Option(help="Symbol, if the file has no symbol column.")] = None,
    timezone: Annotated[str, typer.Option(help="Timezone of naive timestamps.")] = "UTC",
    bar_start: Annotated[bool, typer.Option("--bar-start", help="Timestamps are bar start times.")] = False,
    actions: Annotated[
        Path | None, typer.Option(help="Corporate actions CSV (ex_date, kind=split|dividend, value).")
    ] = None,
    on_conflict: Annotated[
        str, typer.Option(help="reject | replace values that differ from the latest version.")
    ] = "reject",
    home: HomeOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Validate a CSV/Parquet file and publish it as a new dataset version (merged with the latest)."""
    if on_conflict not in ("reject", "replace"):
        fail("--on-conflict must be 'reject' or 'replace'")
    policy: Literal["reject", "replace"] = "replace" if on_conflict == "replace" else "reject"
    with context(home) as ctx, ctx.sessions() as session:
        dataset = ctx.data.get(session, name)
        provider = CsvFileProvider(
            file,
            calendar=dataset.calendar,
            symbol=symbol,
            timezone=timezone,
            timestamp_is_bar_start=bar_start,
        )
        try:
            raw = provider.fetch_bars(BarRequest((), Resolution(dataset.resolution)))
            if actions is not None:
                raw = replace(raw, actions=load_actions_csv(actions, symbol))
            result = ctx.data.import_bars(session, name, provider, raw, on_conflict=policy)
        except DataValidationError as exc:
            session.rollback()
            for issue in exc.report.issues:
                typer.secho(
                    f"  {issue.severity:<7} {issue.check:<20} {issue.symbol or '*':<8} {issue.count:>6}  "
                    f"{issue.message} {', '.join(issue.examples[:3])}",
                    fg=typer.colors.RED if issue.severity == "error" else typer.colors.YELLOW,
                    err=True,
                )
            fail(f"{exc} (report: {exc.quarantine_dir / 'report.json'})")
        session.commit()
        version = result.version
        summary: dict[str, Any] = {
            "dataset": name,
            "created": result.created,
            "version": version.content_hash[:20] if version else None,
            "rows": version.row_count if version else 0,
            "warnings": [f"{i.check}: {i.count} ({i.symbol or '*'})" for i in result.report.warnings],
            "notes": result.notes,
            "completeness": result.report.completeness,
        }
    if as_json:
        emit_json(summary)
        return
    typer.echo(
        ("new version " if result.created else "unchanged: ")
        + f"{summary['version']} ({summary['rows']} rows)"
    )
    for warning in summary["warnings"]:
        typer.secho(f"  warning {warning}", fg=typer.colors.YELLOW)
    for note in summary["notes"]:
        typer.echo(f"  note    {note}")


@data_app.command("fetch")
def data_fetch(
    name: str,
    symbol: Annotated[list[str], typer.Option("--symbol", "-s", help="Ticker(s) to fetch.")],
    provider: Annotated[
        str | None,
        typer.Option(help="Historical provider (default: market_data.historical in quantlab.toml)."),
    ] = None,
    start: Annotated[str | None, typer.Option(help="First date YYYY-MM-DD (default: full history).")] = None,
    since_last: Annotated[
        bool, typer.Option("--since-last", help="Start at the last stored date (incremental update).")
    ] = False,
    end: Annotated[str | None, typer.Option(help="Last date YYYY-MM-DD (default: today).")] = None,
    on_conflict: Annotated[
        str, typer.Option(help="reject | replace values that differ from the latest version.")
    ] = "reject",
    home: HomeOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Download bars (+ corporate actions) into a dataset as a new validated version; cached locally."""
    if start is not None and since_last:
        fail("use either --start or --since-last")
    if on_conflict not in ("reject", "replace"):
        fail("--on-conflict must be 'reject' or 'replace'")
    policy: Literal["reject", "replace"] = "replace" if on_conflict == "replace" else "reject"
    from quantlab.data.factory import historical_provider

    def day(value: str | None) -> datetime | None:
        return None if value is None else datetime.fromisoformat(value).replace(tzinfo=UTC)

    with context(home) as ctx, ctx.sessions() as session:
        dataset = ctx.data.get(session, name)
        first = day(start)
        if since_last:
            latest = ctx.data.latest(session, dataset)
            if latest is None:
                fail("--since-last needs an existing version; run a first fetch with --start")
            first = latest.end_ts.replace(hour=0, minute=0, second=0, microsecond=0)  # refetch the last day
        source = historical_provider(ctx.settings, ctx.secrets, dataset.calendar, provider)
        try:
            result = ctx.data.fetch(
                session,
                name,
                source,
                BarRequest(tuple(symbol), Resolution(dataset.resolution), first, day(end)),
                on_conflict=policy,
            )
        except DataValidationError as exc:
            session.rollback()
            fail(f"{exc} (report: {exc.quarantine_dir / 'report.json'})")
        finally:
            close = getattr(source, "close", None)
            if close is not None:
                close()
        session.commit()
        version = result.version
        summary: dict[str, Any] = {
            "dataset": name,
            "created": result.created,
            "version": version.content_hash[:20] if version else None,
            "rows": version.row_count if version else 0,
            "notes": result.notes,
            "warnings": [f"{i.check}: {i.count} ({i.symbol or '*'})" for i in result.report.warnings],
        }
    if as_json:
        emit_json(summary)
        return
    typer.echo(
        ("new version " if result.created else "unchanged: ")
        + f"{summary['version']} ({summary['rows']} rows)"
    )
    for w in summary["warnings"]:
        typer.secho(f"  warning {w}", fg=typer.colors.YELLOW)
    for note in summary["notes"]:
        typer.echo(f"  note    {note}")


@data_app.command("compare")
def data_compare(
    dataset_a: str,
    dataset_b: str,
    symbol: Annotated[list[str] | None, typer.Option("--symbol", "-s", help="Only these symbols.")] = None,
    tolerance: Annotated[
        float, typer.Option(help="Relative close difference considered a mismatch.")
    ] = 0.001,
    home: HomeOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Compare two datasets (e.g. Tiingo vs IBKR) on common bars: coverage, price and volume differences."""
    from quantlab.data.compare import compare_bars

    with context(home) as ctx, ctx.sessions() as session:
        da, db = ctx.data.get(session, dataset_a), ctx.data.get(session, dataset_b)
        if da.resolution != db.resolution:
            fail(f"resolutions differ ({da.resolution} vs {db.resolution})")
        fa, va = ctx.data.read(session, dataset_a, symbols=symbol)
        fb, vb = ctx.data.read(session, dataset_b, symbols=symbol)
        rows = [c.to_dict() for c in compare_bars(fa, fb, tolerance=tolerance)]
    report = {
        "a": {"dataset": dataset_a, "version": va.content_hash[:20], "provenance": da.provenance},
        "b": {"dataset": dataset_b, "version": vb.content_hash[:20], "provenance": db.provenance},
        "symbols": rows,
    }
    if as_json:
        emit_json(report)
        return
    typer.echo(
        f"A = {dataset_a} @ {va.content_hash[:12]} ({da.provenance})   B = {dataset_b} @ "
        f"{vb.content_hash[:12]} ({db.provenance})"
    )
    for r in rows:
        diff = "-" if r["close_rel_diff_max"] is None else f"{r['close_rel_diff_max']:.4%}"
        typer.echo(
            f"{r['symbol']:<8} A {r['rows_a']:>7}  B {r['rows_b']:>7}  common {r['overlap']:>7}  "
            f"max close diff {diff}"
        )
        for note in r["notes"]:
            typer.secho(f"         {note}", fg=typer.colors.YELLOW)


@data_app.command("list")
def data_list(home: HomeOpt = None, as_json: JsonOpt = False) -> None:
    """List datasets with their latest version."""
    rows = []
    with context(home) as ctx, ctx.sessions() as session:
        for d in ctx.data.list_datasets(session):
            latest = ctx.data.latest(session, d)
            rows.append(
                {
                    "name": d.name,
                    "resolution": d.resolution,
                    "provenance": d.provenance,
                    "adjustment": d.price_adjustment,
                    "latest": latest.content_hash[:20] if latest else None,
                    "rows": latest.row_count if latest else 0,
                }
            )
    if as_json:
        emit_json(rows)
        return
    if not rows:
        typer.echo("no datasets (quantlab data create …)")
    for r in rows:
        typer.echo(
            f"{r['name']:<28} {r['resolution']:<7} {r['provenance']:<16} {r['adjustment']:<24} "
            f"{r['latest'] or '-':<21} {r['rows']} rows"
        )


@data_app.command("show")
def data_show(name: str, home: HomeOpt = None, as_json: JsonOpt = False) -> None:
    """Show a dataset, its versions and the latest validation report."""
    with context(home) as ctx, ctx.sessions() as session:
        d = ctx.data.get(session, name)
        versions = ctx.data.versions(session, d)
        info: dict[str, Any] = {
            **d.identity(),
            "source": d.source,
            "license": d.license_terms,
            "redistribution_allowed": d.redistribution_allowed,
            "versions": [
                {
                    "version": v.content_hash[:20],
                    "created_at": v.created_at.isoformat(),
                    "rows": v.row_count,
                    "start": v.start_ts.isoformat(),
                    "end": v.end_ts.isoformat(),
                    "symbols": sorted(v.json("symbols")),
                    "conflict_policy": v.conflict_policy,
                    "source": v.source,
                }
                for v in versions
            ],
            "latest_validation": versions[-1].json("validation") if versions else None,
        }
    if as_json:
        emit_json(info)
        return
    for key in (
        "name",
        "asset_class",
        "market",
        "resolution",
        "calendar",
        "provenance",
        "price_adjustment",
        "source",
        "license",
    ):
        typer.echo(f"{key:<17} {info[key]}")
    for v in info["versions"]:
        typer.echo(
            f"version {v['version']}  {v['rows']:>8} rows  {v['start'][:10]} → {v['end'][:10]}  "
            f"{','.join(v['symbols'])}"
        )
    validation = info["latest_validation"]
    if validation:
        typer.echo(f"completeness      {json.dumps(validation['completeness'])}")
        for issue in validation["issues"]:
            typer.secho(
                f"  {issue['severity']} {issue['check']}: {issue['count']} ({issue['symbol'] or '*'})",
                fg=typer.colors.YELLOW,
            )


@data_app.command("head")
def data_head(
    name: str,
    symbol: Annotated[str | None, typer.Option(help="Only this symbol.")] = None,
    n: Annotated[int, typer.Option("-n", help="Rows to print.")] = 10,
    version: Annotated[str | None, typer.Option(help="Version hash prefix (default latest).")] = None,
    adjust: Annotated[str, typer.Option(help="raw | split | split_dividend (back-adjusted).")] = "raw",
    home: HomeOpt = None,
) -> None:
    """Print the first rows of a dataset version."""
    if adjust not in ("raw", "split", "split_dividend"):
        fail("--adjust must be raw, split or split_dividend")
    mode: Literal["raw", "split", "split_dividend"] = adjust  # type: ignore[assignment]
    with context(home) as ctx, ctx.sessions() as session:
        frame, resolved = ctx.data.read(
            session, name, version=version, symbols=[symbol] if symbol else None, adjust=mode
        )
        typer.echo(f"# {name} version {resolved.content_hash[:20]} ({mode} prices)")
        typer.echo(frame.head(n).to_string(index=False))


@data_app.command("actions")
def data_actions(
    name: str,
    version: Annotated[str | None, typer.Option(help="Version hash prefix (default latest).")] = None,
    home: HomeOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """List the corporate actions recorded in a dataset version."""
    with context(home) as ctx, ctx.sessions() as session:
        actions = ctx.data.actions(session, name, version)
    rows = [
        {"symbol": r.symbol, "ex_date": r.ex_date.isoformat(), "kind": r.kind, "value": float(r.value)}
        for r in actions.itertuples(index=False)
    ]
    if as_json:
        emit_json(rows)
        return
    if not rows:
        typer.echo("no corporate actions recorded")
    for r in rows:
        typer.echo(f"{r['symbol']:<8} {r['ex_date']}  {r['kind']:<9} {r['value']}")


@data_app.command("lean-export")
def data_lean_export(
    name: str,
    version: Annotated[str | None, typer.Option(help="Version hash prefix (default latest).")] = None,
    home: HomeOpt = None,
) -> None:
    """Create (once) and verify the LEAN-format export of a version; prints its location and hash."""
    with context(home) as ctx, ctx.sessions() as session:
        path, export_hash, _, resolved = ctx.data.lean_export(session, name, version)
    typer.echo(f"version {resolved.content_hash[:20]}  export {export_hash[:20]}  {path}")


@data_app.command("export")
def data_export(
    name: str,
    destination: Path,
    fmt: Annotated[str, typer.Option("--format", help="csv | parquet")] = "csv",
    version: Annotated[str | None, typer.Option(help="Version hash prefix (default latest).")] = None,
    acknowledge_license: Annotated[
        bool, typer.Option(help="Export licence-restricted data for own use.")
    ] = False,
    home: HomeOpt = None,
) -> None:
    """Export a dataset version (no lock-in: plain CSV or Parquet plus manifest.json)."""
    if fmt not in ("csv", "parquet"):
        fail("--format must be csv or parquet")
    out_format: Literal["csv", "parquet"] = "csv" if fmt == "csv" else "parquet"
    with context(home) as ctx, ctx.sessions() as session:
        files = ctx.data.export(
            session,
            name,
            destination,
            fmt=out_format,
            version=version,
            acknowledge_license=acknowledge_license,
        )
        session.commit()
    typer.echo(f"exported {len(files)} file(s) to {destination}")

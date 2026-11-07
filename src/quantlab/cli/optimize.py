"""``quantlab optimize`` — parameter sweeps and walk-forward analysis (OPT-001/003/005/006).

Every trial is a normal backtest; results always show how many trials were tried and the overfitting
statistics.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

import typer

from quantlab.cli.common import HomeOpt, JsonOpt, context, emit_json, fail

optimize_app = typer.Typer(
    no_args_is_help=True, help="Parameter optimization (sweep, walk-forward) with overfitting checks."
)

SpaceOpt = Annotated[
    list[str], typer.Option("--space", "-s", help="Search dimension: name=a,b,c or name=start:stop:step.")
]
ObjectiveOpt = Annotated[
    str, typer.Option(help="Objective to maximise, e.g. 'sharpe' or 'cagr - 0.5*max_drawdown'.")
]
ConstraintOpt = Annotated[
    list[str] | None, typer.Option("--constraint", "-c", help="e.g. 'max_drawdown <= 0.25'.")
]
ParamOpt = Annotated[list[str] | None, typer.Option("--param", "-p", help="Fixed parameter key=value.")]
ModelOpt = Annotated[
    list[str] | None, typer.Option("--model", "-m", help="Execution model override key=value.")
]
DatasetOpt = Annotated[str | None, typer.Option(help="Dataset to run on (default: engine sample data).")]
RandomOpt = Annotated[
    int | None, typer.Option("--random", help="Random search: number of points (default: full grid).")
]
SeedOpt = Annotated[int, typer.Option(help="Seed for random search.")]
ParallelOpt = Annotated[int, typer.Option(min=1, max=16, help="Trials run concurrently (LEAN containers).")]


def _pairs(items: list[str] | None, what: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            fail(f"{what} must be key=value, got {item!r}")
        out[key] = value
    return out


def _spec(
    strategy: str,
    space: list[str],
    objective: str,
    constraint: list[str] | None,
    param: list[str] | None,
    model: list[str] | None,
    dataset: str | None,
    random: int | None,
    seed: int,
    parallel: int,
) -> Any:
    from quantlab.optimize.objective import Constraint, ExpressionError, Objective
    from quantlab.optimize.service import StudySpec
    from quantlab.optimize.space import Space, SpaceError

    try:
        return StudySpec(
            strategy=strategy,
            space=Space.parse(space),
            objective=Objective.parse(objective),
            constraints=tuple(Constraint.parse(c) for c in constraint or []),
            method="random" if random else "grid",
            samples=random,
            seed=seed,
            fixed=_pairs(param, "parameter"),
            dataset=dataset,
            models=_pairs(model, "model") or None,
            max_parallel=parallel,
        )
    except (SpaceError, ExpressionError) as exc:
        fail(str(exc))


def _fmt(v: Any, pct: bool = False) -> str:
    if v is None:
        return "-"
    return f"{v * 100:.2f}%" if pct else f"{v:.4f}" if isinstance(v, float) else str(v)


def _print_summary(opt: Any) -> None:
    s = opt.json("summary") or {}
    typer.echo(f"optimization {opt.id}  {opt.kind}  {opt.strategy_name}  status {opt.status}")
    if opt.error:
        typer.secho(f"error: {opt.error}", fg=typer.colors.RED)
    for w in s.get("warnings", []):
        typer.secho(f"warning: {w}", fg=typer.colors.YELLOW)
    if opt.kind == "sweep":
        typer.echo(f"objective {s.get('objective')}  trials {s.get('trials')}  {s.get('status_counts')}")
        best = s.get("best")
        if best:
            typer.echo(
                f"best     {best['parameters']}  objective {_fmt(best['objective'])}  run {best['run_id']}"
            )
        of = s.get("overfitting", {})
        if "deflated_sharpe" in of:
            d = of["deflated_sharpe"]
            typer.echo(
                f"deflated Sharpe: P(true Sharpe > best-of-{d['trials']} noise) = {d['probability']:.3f} "
                f"(Sharpe/period {d['sharpe']:.4f} vs expected max {d['expected_max_sharpe']:.4f})"
            )
        if "pbo" in of:
            typer.echo(f"PBO (CSCV, {of['pbo']['splits']} splits): {of['pbo']['pbo']:.3f}")
        sens = s.get("sensitivity")
        if sens and sens.get("neighbourhood_ratio") is not None:
            typer.echo(f"neighbourhood ratio (plateau≈1, spike≪1): {sens['neighbourhood_ratio']:.3f}")
    else:
        for w in s.get("windows", []):
            typer.echo(
                f"window {w['window']}  IS {w['is'][0]}..{w['is'][1]}  OOS {w['oos'][0]}..{w['oos'][1]}  "
                f"{w.get('parameters', '-')}  IS obj {_fmt(w.get('is_objective'))}  "
                f"OOS obj {_fmt(w.get('oos_objective'))}"
            )
        m = s.get("oos_metrics")
        if m:
            typer.echo(
                f"OOS (stitched): return {_fmt(m['total_return'], True)}  CAGR {_fmt(m['cagr'], True)}  "
                f"Sharpe {_fmt(m['sharpe'])}  MaxDD {_fmt(m['max_drawdown'], True)}"
            )
        typer.echo(
            f"walk-forward efficiency {_fmt(s.get('walk_forward_efficiency'))}  "
            f"parameter changes {s.get('parameter_changes')}"
        )


@optimize_app.command("sweep")
def sweep(
    strategy: str,
    space: SpaceOpt,
    objective: ObjectiveOpt = "sharpe",
    constraint: ConstraintOpt = None,
    param: ParamOpt = None,
    model: ModelOpt = None,
    dataset: DatasetOpt = None,
    random: RandomOpt = None,
    seed: SeedOpt = 0,
    parallel: ParallelOpt = 1,
    home: HomeOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Grid or random search over parameters; every trial is a recorded backtest."""
    spec = _spec(strategy, space, objective, constraint, param, model, dataset, random, seed, parallel)
    with context(home) as ctx:
        opt = ctx.optimizer().sweep(spec)
    if as_json:
        emit_json({"id": opt.id, "status": opt.status, "summary": opt.json("summary")})
    else:
        _print_summary(opt)


@optimize_app.command("walk-forward")
def walk_forward(
    strategy: str,
    space: SpaceOpt,
    start: Annotated[str, typer.Option(help="First day, YYYY-MM-DD.")],
    end: Annotated[str, typer.Option(help="Last day, YYYY-MM-DD.")],
    train_days: Annotated[int, typer.Option(help="In-sample window length (calendar days).")],
    test_days: Annotated[int, typer.Option(help="Out-of-sample window length (calendar days).")],
    anchored: Annotated[
        bool, typer.Option(help="Keep the in-sample start fixed (expanding window).")
    ] = False,
    start_param: Annotated[
        str, typer.Option(help="Strategy parameter receiving the window start.")
    ] = "start",
    end_param: Annotated[str, typer.Option(help="Strategy parameter receiving the window end.")] = "end",
    objective: ObjectiveOpt = "sharpe",
    constraint: ConstraintOpt = None,
    param: ParamOpt = None,
    model: ModelOpt = None,
    dataset: DatasetOpt = None,
    random: RandomOpt = None,
    seed: SeedOpt = 0,
    parallel: ParallelOpt = 1,
    home: HomeOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Optimise on each in-sample window, run the winner on the following out-of-sample window."""
    from quantlab.optimize.service import WalkForwardSpec

    spec = _spec(strategy, space, objective, constraint, param, model, dataset, random, seed, parallel)
    try:
        wf = WalkForwardSpec(
            date.fromisoformat(start),
            date.fromisoformat(end),
            train_days,
            test_days,
            anchored,
            start_param,
            end_param,
        )
    except ValueError as exc:
        fail(f"invalid date: {exc}")
    with context(home) as ctx:
        opt = ctx.optimizer().walk_forward(spec, wf)
    if as_json:
        emit_json({"id": opt.id, "status": opt.status, "summary": opt.json("summary")})
    else:
        _print_summary(opt)


@optimize_app.command("list")
def list_optimizations(home: HomeOpt = None, as_json: JsonOpt = False) -> None:
    """Recent optimizations."""
    with context(home) as ctx:
        rows = ctx.read_only_optimizer().recent()
    if as_json:
        emit_json(
            [{"id": o.id, "kind": o.kind, "strategy": o.strategy_name, "status": o.status} for o in rows]
        )
        return
    if not rows:
        typer.echo("no optimizations")
    for o in rows:
        typer.echo(f"{o.id}  {o.kind:<12} {o.strategy_name:<20} {o.status}  {o.created_at:%Y-%m-%d %H:%M}")


@optimize_app.command("show")
def show(optimization_id: str, home: HomeOpt = None, as_json: JsonOpt = False) -> None:
    """Summary of an optimization (best trial, overfitting statistics, walk-forward windows)."""
    with context(home) as ctx:
        service = ctx.read_only_optimizer()
        opt = service.get(optimization_id)
        trials = service.trials(optimization_id)
    if as_json:
        emit_json(
            {
                "id": opt.id,
                "kind": opt.kind,
                "status": opt.status,
                "spec": opt.json("spec"),
                "summary": opt.json("summary"),
                "trials": [
                    {
                        "seq": t.seq,
                        "window": t.window,
                        "segment": t.segment,
                        "parameters": t.json("parameters"),
                        "run_id": t.run_id,
                        "status": t.status,
                        "objective": t.objective,
                    }
                    for t in trials
                ],
            }
        )
    else:
        _print_summary(opt)

"""Local REST API (CORE-009) with token authentication (SEC-003).

Security model (docs/SECURITY_POLICY.md):
* Binds to 127.0.0.1 by default (enforced by config validation).
* Every endpoint except ``/api/health`` requires ``Authorization: Bearer <token>``. The token lives
  in the OS keyring (``secret://quantlab/api/token``, created by ``quantlab init``). If no token is
  configured the API **fails closed** (503) instead of running unauthenticated.
* Host-header allow-list (DNS-rebinding protection for a localhost service). No CORS.
* Read-only: no endpoint starts runs or changes state yet. Nothing here can trade.
* Serves the built web UI (``web/dist``) same-origin (DEC-0018) with a strict Content-Security-Policy; the UI
  receives the token through a URL fragment printed by ``quantlab serve`` and sends it as a bearer header.
"""

from __future__ import annotations

import hmac
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request

import quantlab
from quantlab.backtest.models import BacktestRun
from quantlab.backtest.service import BacktestError
from quantlab.bootstrap import AppContext
from quantlab.data.catalog import DataError
from quantlab.oms import queries as oms_queries
from quantlab.optimize.service import OptimizationError
from quantlab.risk.limits import RiskConfigError, load_limits
from quantlab.risk.models import RiskDecision
from quantlab.strategies.lifecycle import LifecycleLog
from quantlab.strategies.registry import StrategyError, load_manifest
from quantlab.strategies.snapshot import SnapshotError
from quantlab.strategies.workspace import ConflictError, bundled_templates_dir, list_templates

LOCAL_HOSTS = ["127.0.0.1", "localhost", "[::1]", "::1"]


class StrategyOut(BaseModel):
    id: str
    name: str
    path: str
    entry_file: str
    language: str
    description: str
    archived: bool
    stage: str = "research"  # lifecycle stage (STR-004)


UI_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
)
# FastAPI's interactive docs load assets from a CDN; they keep a relaxed policy (developer convenience only).
_DOCS_PATHS = ("/api/docs", "/api/openapi.json")
UI_NOT_BUILT = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>QuantLab</title></head>
<body style="font-family: system-ui; max-width: 640px; margin: 48px auto;">
<h1>QuantLab API is running</h1>
<p>The web UI has not been built. Build it once (needs Node.js 22):</p>
<pre>cd web
npm ci
npm run build</pre>
<p>then restart <code>quantlab serve</code>. The API itself is available under <code>/api/</code>.</p>
</body></html>"""


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if not request.url.path.startswith(_DOCS_PATHS):
            response.headers.setdefault("Content-Security-Policy", UI_CSP)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response


def default_ui_dir() -> Path | None:
    """``web/dist`` of a source checkout (editable install), if it has been built."""
    candidate = Path(quantlab.__file__).resolve().parents[2] / "web" / "dist"
    return candidate if (candidate / "index.html").is_file() else None


class DatasetOut(BaseModel):
    name: str
    asset_class: str
    market: str
    resolution: str
    calendar: str
    provenance: str
    price_adjustment: str
    source: str
    license_terms: str
    redistribution_allowed: bool
    versions: int
    latest_version: str | None
    latest_rows: int | None
    latest_start: str | None
    latest_end: str | None
    latest_validation_ok: bool | None


class DatasetDetailOut(DatasetOut):
    version_history: list[dict[str, Any]]
    corporate_actions: list[dict[str, Any]]  # of the latest version


class BarsOut(BaseModel):
    dataset: str
    version: str
    provenance: str
    price_adjustment: str
    adjustment: str
    symbol: str
    truncated: bool
    bars: list[dict[str, Any]]


class BacktestSummaryOut(BaseModel):
    id: str
    strategy_id: str
    status: str
    created_at: str
    finished_at: str | None
    data_provenance: str
    rerun_of: str | None
    total_return: float | None
    sharpe: float | None
    max_drawdown: float | None


class BacktestDetailOut(BacktestSummaryOut):
    parameters: dict[str, str]
    engine_status: str | None
    runtime_error: str | None
    error: str | None
    inputs_hash: str
    result_hash: str | None
    engine_statistics: dict[str, str] | None
    metrics: dict[str, Any] | None
    cross_checks: list[dict[str, Any]] | None
    manifest: dict[str, Any]


def _summary(run: BacktestRun) -> dict[str, Any]:
    metrics = run.json("metrics") or {}
    return {
        "id": run.id,
        "strategy_id": run.strategy_id,
        "status": run.status,
        "created_at": run.created_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "data_provenance": run.data_provenance,
        "rerun_of": run.rerun_of,
        "total_return": metrics.get("total_return"),
        "sharpe": metrics.get("sharpe"),
        "max_drawdown": metrics.get("max_drawdown"),
    }


class CreateStrategyIn(BaseModel):
    name: str
    template: str


class SaveFileIn(BaseModel):
    content: str
    expected_sha256: str | None  # sha256 the editor last read; null for a new file


def _strategy_out(s: Any, stage: str = "research") -> StrategyOut:
    return StrategyOut(
        stage=stage,
        id=s.id,
        name=s.name,
        path=s.path,
        entry_file=s.entry_file,
        language=s.language,
        description=s.description,
        archived=s.archived,
    )


def create_app(ctx: AppContext, ui_dir: Path | None = None) -> FastAPI:
    app = FastAPI(
        title="QuantLab",
        version=quantlab.__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    allowed_hosts = list(LOCAL_HOSTS)
    if ctx.settings.server.remote_access:
        allowed_hosts.append(ctx.settings.server.host)
    app.add_middleware(SecurityHeaders)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = ctx.api_token()
        if expected is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "API token not configured; run `quantlab init`"
            )
        scheme, _, presented = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            presented.encode(), expected.get_secret_value().encode()
        ):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "invalid or missing token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    auth = [Depends(require_token)]

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": quantlab.__version__}

    @app.get("/api/strategies", dependencies=auth)
    def list_strategies(include_archived: bool = False) -> list[StrategyOut]:
        with ctx.sessions() as session:
            log = LifecycleLog()
            return [
                _strategy_out(s, log.current(session, s)[0])
                for s in ctx.registry.list(session, include_archived=include_archived)
            ]

    @app.get("/api/strategies/{name}/lifecycle", dependencies=auth)
    def strategy_lifecycle(name: str) -> dict[str, Any]:
        """STR-004 stage, history and gate thresholds (read-only; transitions are CLI-only)."""
        try:
            return ctx.lifecycle().status(name)
        except StrategyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    # ---- strategy files (UI-003, DEC-0024): the only write endpoints; token-protected like the rest ----
    def _strategy(session: Any, name: str) -> Any:
        try:
            return ctx.registry.get(session, name)
        except StrategyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.get("/api/strategy-templates", dependencies=auth)
    def strategy_templates() -> list[dict[str, Any]]:
        out = []
        for name, path in list_templates(bundled_templates_dir()).items():
            manifest = load_manifest(path)
            out.append({"name": name, "description": manifest.description, "parameters": manifest.parameters})
        return out

    @app.post("/api/strategies", dependencies=auth, status_code=status.HTTP_201_CREATED)
    def create_strategy(body: CreateStrategyIn) -> StrategyOut:
        templates = list_templates(bundled_templates_dir())
        if body.template not in templates:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown template {body.template!r}")
        with ctx.sessions() as session:
            try:
                s = ctx.workspace.create_from_template(
                    session, body.name, templates[body.template], ctx.settings.layout.strategies_dir
                )
            except StrategyError as exc:
                session.rollback()
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
            session.commit()
            return _strategy_out(s)

    @app.get("/api/strategies/{name}/files", dependencies=auth)
    def strategy_files(name: str) -> list[dict[str, Any]]:
        with ctx.sessions() as session:
            s = _strategy(session, name)
            try:
                return [asdict(f) for f in ctx.workspace.list_files(s)]
            except (StrategyError, SnapshotError) as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    @app.get("/api/strategies/{name}/files/{path:path}", dependencies=auth)
    def strategy_file(name: str, path: str) -> dict[str, Any]:
        with ctx.sessions() as session:
            s = _strategy(session, name)
            try:
                return asdict(ctx.workspace.read(s, path))
            except StrategyError as exc:
                raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.put("/api/strategies/{name}/files/{path:path}", dependencies=auth)
    def save_strategy_file(name: str, path: str, body: SaveFileIn) -> dict[str, Any]:
        with ctx.sessions() as session:
            s = _strategy(session, name)
            try:
                result = ctx.workspace.write(session, s, path, body.content, body.expected_sha256)
            except ConflictError as exc:
                session.rollback()
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
            except StrategyError as exc:
                session.rollback()
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
            session.commit()
            return asdict(result)

    def _dataset_out(d: Any, versions: list[Any]) -> dict[str, Any]:
        latest = versions[-1] if versions else None
        return dict(
            name=d.name,
            asset_class=d.asset_class,
            market=d.market,
            resolution=d.resolution,
            calendar=d.calendar,
            provenance=d.provenance,
            price_adjustment=d.price_adjustment,
            source=d.source,
            license_terms=d.license_terms,
            redistribution_allowed=d.redistribution_allowed,
            versions=len(versions),
            latest_version=latest.content_hash[:20] if latest else None,
            latest_rows=latest.row_count if latest else None,
            latest_start=latest.start_ts.isoformat() if latest else None,
            latest_end=latest.end_ts.isoformat() if latest else None,
            latest_validation_ok=latest.json("validation")["ok"] if latest else None,
        )

    @app.get("/api/datasets", dependencies=auth)
    def list_datasets() -> list[DatasetOut]:
        with ctx.sessions() as session:
            return [
                DatasetOut(**_dataset_out(d, ctx.data.versions(session, d)))
                for d in ctx.data.list_datasets(session)
            ]

    @app.get("/api/datasets/{name}", dependencies=auth)
    def get_dataset(name: str) -> DatasetDetailOut:
        with ctx.sessions() as session:
            try:
                d = ctx.data.get(session, name)
            except DataError as exc:
                raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
            versions = ctx.data.versions(session, d)
            history = [
                {
                    "version": v.content_hash,
                    "created_at": v.created_at.isoformat(),
                    "rows": v.row_count,
                    "start": v.start_ts.isoformat(),
                    "end": v.end_ts.isoformat(),
                    "symbols": sorted(v.json("symbols")),
                    "conflict_policy": v.conflict_policy,
                    "source": v.source,
                    "notes": v.json("notes"),
                    "validation": v.json("validation"),
                }
                for v in versions
            ]
            actions = ctx.data.actions(session, name) if versions else None
            action_rows = (
                []
                if actions is None
                else [
                    {
                        "symbol": r.symbol,
                        "ex_date": r.ex_date.isoformat(),
                        "kind": r.kind,
                        "value": float(r.value),
                    }
                    for r in actions.itertuples(index=False)
                ]
            )
            return DatasetDetailOut(
                **_dataset_out(d, versions), version_history=history, corporate_actions=action_rows
            )

    @app.get("/api/datasets/{name}/bars", dependencies=auth)
    def get_bars(
        name: str,
        symbol: str,
        version: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100_000)] = 5_000,
        adjust: Literal["raw", "split", "split_dividend"] = "raw",
    ) -> BarsOut:
        """Most recent ``limit`` bars of one symbol (read-only); ``adjust`` applies corporate actions."""
        with ctx.sessions() as session:
            try:
                d = ctx.data.get(session, name)
                frame, resolved = ctx.data.read(
                    session, name, version=version, symbols=[symbol], adjust=adjust
                )
            except (DataError, ValueError) as exc:
                raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        truncated = len(frame) > limit
        frame = frame.tail(limit)
        bars = [
            {
                "ts": ts.isoformat(),
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": None if v != v else v,  # NaN -> null
            }
            for ts, o, h, lo, c, v in zip(
                frame["ts"],
                frame["open"],
                frame["high"],
                frame["low"],
                frame["close"],
                frame["volume"],
                strict=True,
            )
        ]
        return BarsOut(
            dataset=d.name,
            version=resolved.content_hash,
            provenance=d.provenance,
            price_adjustment=d.price_adjustment,
            adjustment=adjust,
            symbol=symbol.upper(),
            truncated=truncated,
            bars=bars,
        )

    @app.get("/api/backtests", dependencies=auth)
    def list_backtests(
        strategy: str | None = None, limit: Annotated[int, Query(ge=1, le=1000)] = 100
    ) -> list[BacktestSummaryOut]:
        try:
            runs = ctx.read_only_backtests().list(strategy, limit)
        except StrategyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        return [BacktestSummaryOut(**_summary(r)) for r in runs]

    @app.get("/api/backtests/{run_id}", dependencies=auth)
    def get_backtest(run_id: str) -> BacktestDetailOut:
        try:
            run = ctx.read_only_backtests().get(run_id)
        except BacktestError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        return BacktestDetailOut(
            **_summary(run),
            parameters=run.json("parameters"),
            engine_status=run.engine_status,
            runtime_error=run.runtime_error,
            error=run.error,
            inputs_hash=run.inputs_hash,
            result_hash=run.result_hash,
            engine_statistics=run.json("engine_statistics"),
            metrics=run.json("metrics"),
            cross_checks=run.json("cross_checks"),
            manifest=run.json("manifest"),
        )

    @app.get("/api/backtests/{run_id}/charts", dependencies=auth)
    def backtest_charts(
        run_id: str, benchmark: str | None = None, benchmark_symbol: str | None = None
    ) -> dict[str, Any]:
        """AN-002 chart datasets (+ BT-008 benchmark) and the run's orders."""
        try:
            return ctx.read_only_backtests().chart_data(
                run_id, benchmark=benchmark, benchmark_symbol=benchmark_symbol
            )
        except (BacktestError, DataError) as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.get("/api/backtests/{run_id}/report", dependencies=auth)
    def backtest_report(
        run_id: str, benchmark: str | None = None, benchmark_symbol: str | None = None
    ) -> Response:
        """The self-contained HTML report, as a download (rendered on request; nothing is written)."""
        try:
            text, _ = ctx.read_only_backtests().report_html(
                run_id, benchmark=benchmark, benchmark_symbol=benchmark_symbol
            )
        except (BacktestError, DataError) as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        return Response(
            text,
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="backtest-{run_id}.html"'},
        )

    # ---- optimization (read-only; studies are started from the CLI) --------------------------------------
    @app.get("/api/optimizations", dependencies=auth)
    def list_optimizations(limit: Annotated[int, Query(ge=1, le=1000)] = 100) -> list[dict[str, Any]]:
        return [
            {
                "id": o.id,
                "kind": o.kind,
                "strategy": o.strategy_name,
                "status": o.status,
                "created_at": o.created_at,
                "finished_at": o.finished_at,
                "objective": (o.json("spec") or {}).get("objective"),
            }
            for o in ctx.read_only_optimizer().recent(limit)
        ]

    @app.get("/api/optimizations/{optimization_id}", dependencies=auth)
    def get_optimization(optimization_id: str) -> dict[str, Any]:
        service = ctx.read_only_optimizer()
        try:
            opt = service.get(optimization_id)
        except OptimizationError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        return {
            "id": opt.id,
            "kind": opt.kind,
            "strategy": opt.strategy_name,
            "status": opt.status,
            "created_at": opt.created_at,
            "finished_at": opt.finished_at,
            "error": opt.error,
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
                    "metrics": t.json("metrics"),
                }
                for t in service.trials(optimization_id)
            ],
        }

    @app.get("/api/compare", dependencies=auth)
    def compare_backtests(runs: str) -> dict[str, Any]:
        """AN-004: ?runs=<id>,<id>[,...] (2-10 runs)."""
        try:
            return ctx.read_only_backtests().compare_many([r for r in runs.split(",") if r])
        except BacktestError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.get("/api/backtests/{run_id}/robustness", dependencies=auth)
    def backtest_robustness(
        run_id: str,
        samples: Annotated[int, Query(ge=10, le=5000)] = 1000,
        seed: int = 0,
    ) -> dict[str, Any]:
        try:
            return ctx.read_only_backtests().robustness(run_id, samples=samples, seed=seed)
        except BacktestError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    # ---- paper trading + risk (read-only; orders and the kill switch are CLI-only, CLAUDE.md §4) -----------
    @app.get("/api/paper/orders", dependencies=auth)
    def paper_orders(limit: Annotated[int, Query(ge=1, le=1000)] = 200) -> list[dict[str, Any]]:
        with ctx.sessions() as session:
            return [
                {
                    "id": o.id,
                    "client_order_key": o.client_order_key,
                    "symbol": o.symbol,
                    "side": o.side,
                    "quantity": o.quantity,
                    "order_type": o.order_type,
                    "limit_price": o.limit_price,
                    "stop_price": o.stop_price,
                    "time_in_force": o.time_in_force,
                    "mode": o.mode,
                    "source": o.source,
                    "state": state.value,
                    "created_at": o.created_at,
                }
                for o, state in oms_queries.orders_with_state(session, ctx.settings.paper.account, limit)
            ]

    @app.get("/api/paper/orders/{order_id}", dependencies=auth)
    def paper_order(order_id: str) -> dict[str, Any]:
        with ctx.sessions() as session:
            events = oms_queries.order_events(session, order_id)
            if not events:
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown order {order_id}")
            decisions = session.execute(
                select(RiskDecision).where(RiskDecision.order_id == order_id)
            ).scalars()
            return {
                "order_id": order_id,
                "events": [
                    {"seq": e.seq, "state": e.state, "detail": e.json(), "at": e.created_at} for e in events
                ],
                "risk_decisions": [
                    {
                        "approved": d.approved,
                        "checks": d.json("checks"),
                        "limits_sha256": d.limits_sha256,
                        "at": d.created_at,
                    }
                    for d in decisions
                ],
                "fills": [
                    {
                        "exec_id": f.exec_id,
                        "quantity": f.quantity,
                        "price": f.price,
                        "commission": f.commission,
                        "at": f.executed_at,
                        "price_state": f.price_state,
                    }
                    for f in oms_queries.fills(session, ctx.settings.paper.account)
                    if f.order_id == order_id
                ],
            }

    @app.get("/api/paper/portfolio", dependencies=auth)
    def paper_portfolio() -> dict[str, Any]:
        """Confirmed book from fills (unmarked: marking needs live quotes, see `quantlab paper positions`)."""
        with ctx.sessions() as session:
            account = ctx.settings.paper.account
            book = oms_queries.confirmed_book(session, account, ctx.settings.paper.starting_cash)
            states = oms_queries.fill_price_states(session, account)
            return {
                "account": account,
                "mode": "paper",
                "fill_price_states": states,
                "warning": oms_queries.simulation_warning(states),
                **book.snapshot(),
            }

    @app.get("/api/risk", dependencies=auth)
    def risk_status(limit: Annotated[int, Query(ge=1, le=500)] = 50) -> dict[str, Any]:
        with ctx.sessions() as session:
            last = ctx.kill_switch().state(session)
            try:
                loaded = load_limits(ctx.settings.risk_limits_file)
                limits: dict[str, Any] = {
                    "valid": True,
                    "sha256": loaded.sha256,
                    "values": loaded.limits.model_dump(),
                }
            except RiskConfigError as exc:
                limits = {"valid": False, "error": str(exc)}
            decisions = session.execute(
                select(RiskDecision).order_by(RiskDecision.created_at.desc()).limit(limit)
            ).scalars()
            return {
                "kill_switch": {
                    "engaged": bool(last and last.engaged),
                    "reason": last.reason if last else None,
                    "at": last.created_at if last else None,
                },
                "limits": limits,
                "decisions": [
                    {
                        "order_id": d.order_id,
                        "approved": d.approved,
                        "failed": [c for c in d.json("checks") if not c["ok"]],
                        "at": d.created_at,
                    }
                    for d in decisions
                ],
            }

    # ---- web UI (same origin) ---------------------------------------------------------------------
    if ui_dir is not None and (ui_dir / "index.html").is_file():
        index = ui_dir / "index.html"
        if (ui_dir / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=ui_dir / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> Response:
            if path.startswith("api/") or path == "api":
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
            candidate = (ui_dir / path).resolve()
            if path and candidate.is_file() and candidate.is_relative_to(ui_dir.resolve()):
                return FileResponse(candidate)
            return FileResponse(index, headers={"Cache-Control": "no-cache"})  # client-side routes
    else:

        @app.get("/", include_in_schema=False)
        def ui_not_built() -> HTMLResponse:
            return HTMLResponse(UI_NOT_BUILT)

    return app

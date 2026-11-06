"""Local REST API (CORE-009) with token authentication (SEC-003).

Security model (docs/SECURITY_POLICY.md):
* Binds to 127.0.0.1 by default (enforced by config validation).
* Every endpoint except ``/api/health`` requires ``Authorization: Bearer <token>``. The token lives
  in the OS keyring (``secret://quantlab/api/token``, created by ``quantlab init``). If no token is
  configured the API **fails closed** (503) instead of running unauthenticated.
* Host-header allow-list (DNS-rebinding protection for a localhost service). No CORS.
* Read-only for M1: no endpoint starts runs or changes state yet. Nothing here can trade.
"""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel

import quantlab
from quantlab.backtest.models import BacktestRun
from quantlab.backtest.service import BacktestError
from quantlab.bootstrap import AppContext
from quantlab.strategies.registry import StrategyError

LOCAL_HOSTS = ["127.0.0.1", "localhost", "[::1]", "::1"]


class StrategyOut(BaseModel):
    id: str
    name: str
    path: str
    entry_file: str
    language: str
    description: str
    archived: bool


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


def create_app(ctx: AppContext) -> FastAPI:
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
            return [
                StrategyOut(
                    id=s.id,
                    name=s.name,
                    path=s.path,
                    entry_file=s.entry_file,
                    language=s.language,
                    description=s.description,
                    archived=s.archived,
                )
                for s in ctx.registry.list(session, include_archived=include_archived)
            ]

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

    return app

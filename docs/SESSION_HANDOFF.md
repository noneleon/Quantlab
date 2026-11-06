# Session Handoff

**Last session:** 2026-11-06 · branch `claude/quantlab-self-hosted-platform-q9f3l1` · CHANGE-0007 · PR noneleon/Quantlab#1

## What was done (cumulative this session)
- CHANGE-0002..0005: core infra + CI fixes. CHANGE-0006: LEAN engine adapter (Docker).
- CHANGE-0007 (step 1d): strategy registry + versioning (tree hash, git info, `strategy.toml` parameters), backtest
  service with per-run code snapshot, reproducibility manifest, immutable run history, `rerun` + `compare`,
  equity-curve metrics + cross-check vs LEAN statistics, migration 0002, layered import contracts, example strategy.

## Tests (actually executed)
`uv run pytest --cov` → 180 passed, 1 skipped (real Docker), 94 % coverage (Linux, Py 3.11.15); ruff, mypy --strict,
lint-imports clean. CI for CHANGE-0007: see latest run on the PR.

## Critical honesty note
LEAN has still **never run** under QuantLab. Every backtest test uses the fake docker + synthetic fixture.
BT-001..003 are TESTED in that sense only.

## Key design facts to remember
- `BacktestService(settings, session_factory(engine), create_engine_adapter(settings)).run("name", {"fast": 5})`.
- Run dir: `runs/<id>/{algorithm/, engine/{lean-config.json,docker-command.json,engine.log,results/,storage/},
  manifest.json, metrics.json}`.
- Terminal runs immutable; strategies/versions/runs never deleted (DB triggers + ORM guards).
- New module ⇒ add it to the import-linter layers contract; new tables ⇒ new Alembic revision + add the model module to
  `MODEL_MODULES` in `migrations/env.py` (the schema-drift test will fail otherwise).
- Metrics conventions: daily UTC resampling, 252 periods, rf 0 (recorded in manifest).

## Waiting for owner
- First real LEAN run on the Windows PC (TESTING_STRATEGY "Real LEAN run").

## Next recommended task
Phase 1 step 1e: CORE-008 CLI (`quantlab init`, `strategy add/list`, `backtest run/show/rerun/list`), CORE-009 + SEC-003
local API (FastAPI, 127.0.0.1, token auth) for `GET /api/backtests[/{id}]`. Then M1 acceptance test on a Docker machine.

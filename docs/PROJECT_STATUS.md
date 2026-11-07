# Project Status

**Current phase:** Phase 2 — Market data & research (2a data foundation, 2b LEAN export/backtests on datasets, 2c research API + Jupyter done; next 2d UI shell).
Phase 1 code complete; **M1 acceptance (real LEAN on Docker) still pending.**
**Last change:** CHANGE-0011 (2026-11-07)
**Overall health:** Core, LEAN engine adapter (never run against real LEAN), strategies, backtests, analytics, CLI, read-only API and the data catalog are implemented and tested (CI green). No trading, portfolio or UI yet.

## Phase progress
| Phase | Name | Status |
|---|---|---|
| 0 | Repository & architecture audit | DONE — approved by owner 2026-11-06 |
| 1 | Core infrastructure + M1 | IN PROGRESS — 1a (config, logging, paths, skeleton, CI) + 1b (DB, migrations, audit, ids/clock, secrets) + 1c (LEAN engine adapter) + 1d (strategies, backtests, metrics) + 1e (CLI, read-only API) done; M1 acceptance pending |
| 2 | Market data & research | IN PROGRESS — 2a (providers, CSV import, validation, versioned catalog, export) + 2b (LEAN export, backtests on datasets) + 2c (research API, statistics, JupyterLab) done |
| 3–9 | — | Not started |

## What exists
- Project memory: `CLAUDE.md`, `docs/*`, `.claude/commands`, `.claude/agents`.
- `src/quantlab/core/`: `paths.py` (CORE-005), `config.py` (CORE-002), `logging.py` (CORE-003), `db.py` + `migrations/`
  (CORE-004), `audit.py` (CORE-010), `ids.py`/`clock.py` (CORE-006 partial), `secret_store.py` (SEC-001).
- `src/quantlab/engine/`: EngineAdapter protocol (ENG-001), LEAN config builder (ENG-004), result parser (ENG-005),
  Docker launcher + probe (ENG-002/006) — **not yet run against real LEAN** (KI-0002).
- `src/quantlab/strategies/` (STR-001..003), `src/quantlab/backtest/` (BT-001..003), `src/quantlab/analytics/` (AN-001
  partial), `src/quantlab/migrations/` (0001 audit, 0002 strategies/backtests). Example: `strategies/examples/sma_crossover`.
- `quantlab` CLI (`src/quantlab/cli/`), local read-only API (`src/quantlab/api/`), composition root `src/quantlab/bootstrap.py`.
- `src/quantlab/data/` (DATA-001..007, 016): `quantlab data create/import/list/show/head/export`, `GET /api/datasets`; LEAN export (DATA-009) and `quantlab backtest run --dataset`.
- `src/quantlab/research/` (RES-001/002) + `analytics/statistics.py` (RES-004): `quantlab research`, notebook API.
- Packaging: `pyproject.toml` + `uv.lock`, Python 3.11 (CORE-001). CI: `.github/workflows/ci.yml` (MEM-004, green on ubuntu + windows).
- `tools/env_check.py` environment probe.
- Tests: 278 passing + 1 Docker-gated skip (`uv run pytest`), 92 % coverage; 3 import-linter contracts in CI.
- PR noneleon/Quantlab#1 open from this branch (opened by owner).

## Decisions
All DEC-0002..0012 ACCEPTED (DEC-0007 = Option A gateway brokerage; can be revisited before Phase 5).

## Environment notes
Dev container: Linux, Python 3.11.15 via uv, Docker client without daemon, no .NET — LEAN cannot run here.
The owner's Windows machine has not been probed yet (`python tools/env_check.py`).

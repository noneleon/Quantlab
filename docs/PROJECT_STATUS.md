# Project Status

**Current phase:** Phase 1 — Core infrastructure (1a–1d done; next 1e CLI + API, then M1 acceptance on a Docker machine)
**Last change:** CHANGE-0007 (2026-11-06)
**Overall health:** Foundation code with passing tests. No engine, data, trading or UI code yet.

## Phase progress
| Phase | Name | Status |
|---|---|---|
| 0 | Repository & architecture audit | DONE — approved by owner 2026-11-06 |
| 1 | Core infrastructure + M1 | IN PROGRESS — 1a (config, logging, paths, skeleton, CI) + 1b (DB, migrations, audit, ids/clock, secrets) + 1c (LEAN engine adapter) + 1d (strategies, backtests, metrics) done |
| 2–9 | — | Not started |

## What exists
- Project memory: `CLAUDE.md`, `docs/*`, `.claude/commands`, `.claude/agents`.
- `src/quantlab/core/`: `paths.py` (CORE-005), `config.py` (CORE-002), `logging.py` (CORE-003), `db.py` + `migrations/`
  (CORE-004), `audit.py` (CORE-010), `ids.py`/`clock.py` (CORE-006 partial), `secret_store.py` (SEC-001).
- `src/quantlab/engine/`: EngineAdapter protocol (ENG-001), LEAN config builder (ENG-004), result parser (ENG-005),
  Docker launcher + probe (ENG-002/006) — **not yet run against real LEAN** (KI-0002).
- `src/quantlab/strategies/` (STR-001..003), `src/quantlab/backtest/` (BT-001..003), `src/quantlab/analytics/` (AN-001
  partial), `src/quantlab/migrations/` (0001 audit, 0002 strategies/backtests). Example: `strategies/examples/sma_crossover`.
- Packaging: `pyproject.toml` + `uv.lock`, Python 3.11 (CORE-001). CI: `.github/workflows/ci.yml` (MEM-004, green on ubuntu + windows).
- `tools/env_check.py` environment probe.
- Tests: 180 passing + 1 Docker-gated skip (`uv run pytest`), 94 % coverage; 3 import-linter contracts in CI.
- PR noneleon/Quantlab#1 open from this branch (opened by owner).

## Decisions
All DEC-0002..0012 ACCEPTED (DEC-0007 = Option A gateway brokerage; can be revisited before Phase 5).

## Environment notes
Dev container: Linux, Python 3.11.15 via uv, Docker client without daemon, no .NET — LEAN cannot run here.
The owner's Windows machine has not been probed yet (`python tools/env_check.py`).

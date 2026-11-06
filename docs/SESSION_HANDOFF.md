# Session Handoff

**Last session:** 2026-11-06 · branch `claude/quantlab-self-hosted-platform-q9f3l1` · CHANGE-0008 · PR noneleon/Quantlab#1

## What was done (cumulative this session)
- CHANGE-0002..0005 core infra; 0006 LEAN Docker adapter; 0007 strategies/backtests/metrics;
- CHANGE-0008 (step 1e): `quantlab` CLI, `AppContext` composition root, read-only local API (token in OS keyring,
  fail-closed, Host allow-list), secret write read-back verification (bug found by smoke test).

## Tests (actually executed)
`uv run pytest --cov` → 214 passed, 1 skipped (real Docker), 92 % (Linux, Py 3.11.15); ruff, mypy --strict, lint-imports
clean; manual uvicorn smoke test over HTTP passed. CI runs #21 (push) and #22 (PR) green on ubuntu + windows.

## Critical honesty note
Phase 1 code is complete, but **Milestone M1 is not accepted**: LEAN has never run under QuantLab. The M1 flow is proven
only against the fake engine with synthetic results.

## M1 acceptance procedure (owner, Windows PC with Docker Desktop)
```
uv sync
uv run quantlab init
# edit %LOCALAPPDATA%\QuantLab\quantlab.toml: [engine.lean] image = "quantconnect/lean@sha256:<digest>"
uv run quantlab engine pull
uv run quantlab engine probe
uv run quantlab strategy add sma strategies/examples/sma_crossover
uv run quantlab backtest run sma
uv run quantlab backtest rerun <run-id>      # expect: reproduced True
```
Report any failure output; KI-0014 lists the most likely LEAN-specific issues.

## Key design facts to remember
- All entry points go through `quantlab.bootstrap.AppContext.open()` (auto-migrates DB). Don't build services elsewhere.
- Tests: autouse in-memory keyring + isolated QUANTLAB_HOME; `tests/fixtures/m1.py` configures the fake engine.
- CLI `--json` writes to stdout only; logs go to stderr.
- Layers: cli > api > bootstrap > migrations > backtest > (strategies | analytics | engine) > core.

## Next recommended task
1. M1 acceptance on real LEAN (owner) → fix whatever it reveals, record recorded LEAN fixture, mark ENG/BT VERIFIED.
2. Then Phase 2 (data & research): DATA-001..006 (provider protocol, CSV importer, Parquet+DuckDB catalog, versioning,
   validation, provenance) — the first real-data path into backtests.

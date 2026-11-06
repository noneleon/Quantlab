# Session Handoff

**Last session:** 2026-11-06 · branch `claude/quantlab-self-hosted-platform-q9f3l1` · CHANGE-0010 · PR noneleon/Quantlab#1

## What was done (cumulative this session)
- Phase 1 complete in code (CHANGE-0002..0008). M1 acceptance on real LEAN still pending.
- CHANGE-0009 (2a): data foundation — providers, CSV import, calendars, validation/quarantine, versioned Parquet catalog.
- CHANGE-0010 (2b): deterministic LEAN-format export (golden-tested against LEAN's own sample bytes), backtests on pinned
  dataset versions (`--dataset`), provenance carried into runs/manifests/CLI, sample prices removed inside the container
  for dataset runs (DEC-0016).

## Tests (actually executed)
`uv run pytest` → 263 passed, 1 skipped (real Docker); ruff, mypy --strict, lint-imports clean. CI for CHANGE-0010: see
latest run on the PR.

## Critical honesty note
No real LEAN run has happened yet — neither M1 nor a dataset run. KI-0002/0014/0028 list what is unverified.

## Owner verification (Windows PC with Docker) — now covers datasets too
```
uv run quantlab data create spy-daily --asset-class equity --market usa --resolution daily \
    --provenance real_historical --adjustment raw --source "<your source>"
uv run quantlab data import spy-daily SPY.csv --symbol SPY
uv run quantlab strategy add sma-daily strategies/examples/sma_daily
uv run quantlab backtest run sma-daily --dataset spy-daily -p start=2020-01-02 -p end=2024-12-31
uv run quantlab backtest rerun <run-id>
```

## Key design facts to remember
- `BacktestService.run(name, params, dataset=..., dataset_version=...)`; `DataBinding` describes the data of a run.
- LEAN export: `data/lean/<dataset>/<hash[:20]>/`, created once, verified on every use; equities only.
- Example strategies: `sma_crossover` (sample minute data), `sma_daily` (dataset, RAW normalization, own benchmark).

## Next recommended task
Phase 2c: RES-001/002 research environment — `quantlab.research` API (load dataset versions as pandas, shared metrics),
`quantlab research` launching local JupyterLab against the catalog (optional dependency group), RES-004 basic statistics.
Then 2d UI shell, 2e network data providers, DATA-008 corporate actions.

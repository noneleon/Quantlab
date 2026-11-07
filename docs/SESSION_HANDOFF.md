# Session Handoff

**Last session:** 2026-11-07 · branch `claude/quantlab-self-hosted-platform-q9f3l1` · CHANGE-0012 · PR noneleon/Quantlab#1

## What was done (cumulative this session)
- Phase 1 complete in code (CHANGE-0002..0008). M1 acceptance on real LEAN still pending.
- CHANGE-0009 (2a): data foundation — providers, CSV import, calendars, validation/quarantine, versioned Parquet catalog.
- CHANGE-0010 (2b): deterministic LEAN-format export (golden-tested against LEAN's own sample bytes), backtests on pinned
  dataset versions (`--dataset`), provenance carried into runs/manifests/CLI, sample prices removed inside the container
  for dataset runs (DEC-0016).
- CHANGE-0011 (2c): `quantlab.research.Research` notebook API (catalog-only, provenance in attrs, NonRealDataWarning),
  `analytics.statistics` (distribution/VaR/ES/correlation/rolling vol), `quantlab research` JupyterLab launcher
  (optional extra), starter notebook verified by real headless execution.
- CHANGE-0012 (2d): `web/` React UI (dashboard, market data browser with chart/validation, backtest list/detail, strategy
  list, honest "planned" pages for all other modules), served same-origin by `quantlab serve` with strict CSP; token via
  `#token=` fragment (DEC-0018). Verified in real Chromium with Playwright.

## Tests (actually executed)
Python: `uv run pytest` → 282 passed, 1 skipped (real Docker); ruff, mypy --strict, lint-imports clean.
Web: `npm run typecheck`, `npm test` (16 passed), `npm run build` ok. Manual Playwright smoke (see CHANGE-0012).
CI #39 (push) / #40 (PR) green: ubuntu, windows and the new `web` job.

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
- Push procedure: fresh clone → `uv sync --locked` → ruff check/format, mypy, lint-imports, pytest → `git push`, all chained
  with `&&` (CHANGE-0004 and CHANGE-0011 incidents).
- `BacktestService.run(name, params, dataset=..., dataset_version=...)`; `DataBinding` describes the data of a run.
- LEAN export: `data/lean/<dataset>/<hash[:20]>/`, created once, verified on every use; equities only.
- Example strategies: `sma_crossover` (sample minute data), `sma_daily` (dataset, RAW normalization, own benchmark).

## Next recommended task
Phase 2e: DATA-011 network data providers behind the DataProvider protocol (start with one free, licence-clear source;
record ToS/licence; rate limiting; never silently mark data as real without the user's declaration), then DATA-008
corporate actions (split/dividend factor files) so LEAN exports can carry real adjustments. Also open: first real LEAN run
(owner), RES-003 QuantBook, UI equity chart (needs equity series in the API, AN-002).

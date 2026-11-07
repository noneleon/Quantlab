# Session Handoff

**Last session:** 2026-11-07 · branch `claude/quantlab-self-hosted-platform-q9f3l1` · CHANGE-0015 · PR noneleon/Quantlab#1

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
- CHANGE-0013 (2e part 1): corporate actions (splits/dividends) stored in dataset versions, LEAN factor files and
  read-time adjustment from one golden-tested factor computation; calendar history fix (pre-2006 data was rejected).
- CHANGE-0014 (2e part 2): Tiingo EOD provider (`quantlab data fetch`), `quantlab secret` CLI; verified against the real
  API; Tiingo AAPL history reproduces LEAN's AAPL factor file.
- CHANGE-0015 (migration slice A, DEC-0021): `MarketDataProvider` + DataState/QuoteGuard, Tiingo 1-minute bars, mock
  SIMULATED streaming (`quantlab md`), `[market_data]` provider switch, `data compare` / `backtest compare`,
  minute-coverage validation.

## Tests (actually executed)
Python: `uv run pytest` → 335 passed, 1 skipped (real Docker); ruff, mypy --strict, lint-imports clean.
Web: unchanged (16 passed). Real-data checks: see CHANGE-0014/0015. CI for CHANGE-0015: see latest run on the PR.

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

## Environment
Cloud sessions for this repo should use the personal environment **Quantlab** (allows api/www/apimedia.tiingo.com; the
Tiingo key is a proxy-injected network secret — code sends a placeholder, e.g. `QUANTLAB_SECRET__TIINGO__API_KEY=proxy-injected`).
Real Tiingo data must never be committed (licence: personal use, no redistribution).

## Key design facts to remember
- Push procedure: fresh clone → `uv sync --locked` → ruff check/format, mypy, lint-imports, pytest → `git push`, all chained
  with `&&` (CHANGE-0004 and CHANGE-0011 incidents).
- `BacktestService.run(name, params, dataset=..., dataset_version=...)`; `DataBinding` describes the data of a run.
- LEAN export: `data/lean/<dataset>/<hash[:20]>/`, created once, verified on every use; equities only.
- Example strategies: `sma_crossover` (sample minute data), `sma_daily` (dataset, RAW normalization, own benchmark).

## Next recommended task
Migration slice B (DATA-019): `IBKRProvider` implementing `MarketDataProvider` via the TWS API (library choice to verify:
`ib_async`), historical bars → catalog, Level I streaming with `reqMarketDataType` mapped to LIVE/FROZEN/DELAYED (+
detection of IB's "delayed data displayed" fallback → DELAYED, refused when live is required), reconnection with
backoff, historical pacing limits. Test against a fake IB client; real verification needs the owner's IB Gateway (paper).
Then slice C in Phase 5 (BrokerAdapter, OMS, risk gate, paper trading) — orders never bypass the risk gate (DEC-0007).

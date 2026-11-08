# Session Handoff

**Last session:** 2026-11-08 · branch `claude/quantlab-self-hosted-platform-q9f3l1` · CHANGE-0046 · PR noneleon/Quantlab#1

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
- CHANGE-0016 (slice B): `IBKRProvider` (historical + Level I, read-only connection, client id 18) via `ib_async`;
  fake-client tests only — **not yet run against a real IB Gateway** (KI-0043/0044).
- CHANGE-0017 (Phase 3a, DEC-0022): chart datasets (`analytics/charts.py`), benchmark comparison, self-contained HTML
  report + bundle (`quantlab backtest report`), `/api/backtests/{id}/charts|report`, run-detail charts in the UI.
- CHANGE-0018 (Phase 3b, DEC-0023): `[backtest.models]` + `-m key=value`, opt-in `quantlab_lean.configure(self)`
  (mounted read-only, marker-confirmed), 3 strategy templates. Stub/fake-engine tested only (KI-0050/0051).
- CHANGE-0019 (Phase 3c part 1, DEC-0024): no QuantConnect subscription ever; in-browser editor (CodeMirror, shadow
  root, strict CSP) + strategy library with create-from-template; `backtest golden record|list|check`; vitest 4.1.12.
- CHANGE-0020: Windows CI fix (tests wrote CRLF) + editor preserves CRLF/LF.
- CHANGE-0021 (Phase 4a, DEC-0025): `quantlab optimize sweep|walk-forward|list|show`, `backtest robustness`;
  objectives/constraints, DSR, PBO (CSCV), sensitivity, stationary bootstrap, trade Monte Carlo; migration 0004.
- CHANGE-0022: fix race on strategy_versions under parallel trials (CI #59).
- CHANGE-0023 (Phase 4b): Optuna TPE (`--tpe N`, `--extra-objective` → Pareto), optimization pages, run comparison
  (`/compare`), robustness panel.
- CHANGE-0024 (Phase 4c, DEC-0026): lifecycle stages with evidence gates (`strategy status|promote|demote|retire`),
  append-only history (migration 0005), `strategy from-notebook` (outputs stripped).
- CHANGE-0025 (Phase 5a, DEC-0027): OMS + central risk gate + kill switch + paper broker + confirmed book +
  reconciliation (migration 0006); `quantlab risk …`, `quantlab paper …` (paper only, CLI-only writes).
- CHANGE-0026/0027: second and third trading-safety reviews fixed (market-mark equity, persisted anomalies, TTY-only
  disengage, OS-level paper-broker lock, event ack by sequence, snapshot reconcile, fills never hidden as replays,
  untracked broker orders block new orders). A fourth review found no BLOCKER/MAJOR; open minors are KI-0068..0071.
- CHANGE-0028 (5b-1): drawdown limit, cash-account buying power, rejection cooldown (new required limit keys —
  **existing limits files must add them**, see `quantlab risk template`); kill-switch engage cancels working orders.
- CHANGE-0029 (5b-2, DEC-0028): web console — Portfolio, Orders (+detail), Risk; engage-only kill switch in the UI.
- CHANGE-0030 (5b-3, DEC-0029): IBKR paper broker adapter (`[paper] broker = "ibkr"`, `ibkr_account = "DU…"`,
  `ibkr_starting_cash`); paper logins only; **fake-client tested only — needs a real IB Gateway paper check (KI-0075)**.
  CHANGE-0031 fixed its safety-review findings (rejections reported as Cancelled; manual TWS orders refreshed).
- CHANGE-0032 (5b-4, DEC-0030): `quantlab paper supervise` — events, reconcile (engages the kill switch on position
  differences), equity samples / start-of-day at the open, DAY expiry; heartbeat in CLI/API/console. Migration 0007.
  CHANGE-0033 fixed its safety-review findings (missed-close DAY expiry, checked quotes for equity, health status).
- CHANGE-0034 (5c-1, DEC-0031): LEAN → OMS gateway service (`quantlab paper gateway --deployment`); the C# plugin
  (5c-2) is next. A .NET 10 SDK lives in the session scratchpad only (`dotnet-install.sh`, host allowed in the env).
- CHANGE-0036 (5c-2): C# `lean/QuantLabBrokerage` plugin + xUnit tests against the real Python gateway
  (`dotnet test lean/QuantLabBrokerage.Tests`, needs `uv sync`; 14 tests); CI job `lean-plugin`. CHANGE-0037 fixed its
  review (no updates via the brokerage model, auto-reconnect, unknown vs not-placed). Next: 5c-3 native launcher.
- CHANGE-0038 (5c-3a, DEC-0032): **first real LEAN runs** — native launcher (`quantlab engine native-install`,
  `[engine.lean] launcher = "native"`), C# strategies, LEAN's published statistics reproduced exactly; metrics fixed
  (full-resolution total return / CAGR / drawdown).
- CHANGE-0039 (5c-3b): **a real LEAN engine (live mode) placed a paper order through the gateway, OMS and risk gate**
  and received the fill (synthetic prices).
- CHANGE-0041 (5c-4, DEC-0033): `quantlab paper deploy <strategy> --deployment <name>` — C# strategy promoted to
  `paper`, LEAN live mode on QuantLab's quotes (QuantLabDataQueue), orders via gateway/OMS/risk gate; real LEAN verified.
- CHANGE-0042/0043: deployment review fixes; Paper Trading console page (status, data states, logs); IBKR quotes from
  kept subscriptions with a lock around the shared client.
- CHANGE-0044: quote safety-review fixes — frozen IBKR tickers / IB 1100 flag quotes `stale`; the paper broker never
  fills on flagged or old real-time quotes; the risk gate rejects flagged quotes; no reconnect under the quote lock
  (back-off instead); the order's quote is fetched before the OMS write transaction; log token redaction.
- CHANGE-0045: Python strategies on the native LEAN launcher (no Docker): pythonnet uses QuantLab's Python 3.11;
  golden Python run reproduces LEAN's published statistics and order hash; custom execution models work natively.
- CHANGE-0046: `quantlab paper deploy` accepts Python strategies (real LEAN: QuantLab price → order through gateway
  and risk gate → fill, synthetic quotes).

## Tests (actually executed)
Python: `uv run pytest` → 638 passed, 6 skipped (CHANGE-0046); C# plugin 17 passed; real-LEAN integration 5 passed; ruff, mypy --strict, lint-imports (5 contracts) clean.
Web: `npm test` → 29 passed; typecheck and build clean; console checked in real Chromium (CHANGE-0029). Trading tests: synthetic SIMULATED quotes on the local paper broker only.

## Critical honesty note
Real LEAN has run natively (C#, LEAN sample data) and matches LEAN's published statistics. Not yet: a real run of
the Docker launcher, Python algorithms, or a dataset run on real LEAN. KI-0002/0014/0028 list what is unverified.

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
Phase 5 remaining: a LEAN paper deployment on a real-time LIVE feed (owner's IB Gateway, KI-0077/0080/0081), deployment
pause/resume, strategy isolation (KI-0082). Phase 6 (live gates, LIVE-003) only with explicit owner approval.
Owner actions: `quantlab risk init` + review limits; first real LEAN run (KI-0002); IBKR paper check — market data (KI-0043/0044) and the paper broker adapter (KI-0075).

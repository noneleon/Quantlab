# Implementation Status — Canonical Feature Registry

**This is the single source of truth for feature status.** Search here (and the repo) before implementing anything.
Other docs reference these IDs and must not keep their own status copies.

Status vocabulary:
`PLANNED` · `IN_PROGRESS` · `IMPLEMENTED` (behaviour exists & integrated) · `TESTED` (automated tests pass) ·
`VERIFIED` (validated against real data/engine/broker or by the owner) · `DEPRECATED` · `BLOCKED`.

Approach codes: **L** = provided by LEAN (configure/integrate only) · **LI** = LEAN + QuantLab integration layer ·
**C** = custom QuantLab development · **X** = depends on external licensed data/service (adapter only).

Phase = roadmap phase in [ROADMAP.md](ROADMAP.md). Location/tests are filled when work starts.

Last updated: 2026-11-07 (CHANGE-0025)

## MEM — Project memory & dev tooling
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| MEM-001 | Project memory docs (CLAUDE.md + docs/*) | docs | C | 0 | `CLAUDE.md`, `docs/` | — | n/a (docs) | IMPLEMENTED | Must be maintained every session |
| MEM-002 | Claude Code slash commands & agents | .claude | C | 0 | `.claude/commands/`, `.claude/agents/` | MEM-001 | manual | IMPLEMENTED | Format = Claude Code markdown+frontmatter; re-verify on CLI upgrades |
| MEM-003 | Environment probe tool | tools | C | 0 | `tools/env_check.py` | — | `tests/tools/test_env_check.py` | TESTED | Run on the owner's Windows machine not yet done |
| MEM-004 | CI pipeline (lint, format, type-check, tests; Ubuntu + Windows) | repo | C | 1 | `.github/workflows/ci.yml` | CORE-001 | the workflow itself | VERIFIED | Green on ubuntu + windows; import-linter contracts (core ⟂ engine, engine→core only) added in CHANGE-0006 |

## CORE — Core infrastructure
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| CORE-001 | Python package skeleton, `uv` lockfile, ruff/mypy config | core | C | 1 | `pyproject.toml`, `uv.lock`, `.python-version`, `src/quantlab/` | — | full suite runs via it | TESTED | Python pinned `>=3.11,<3.12` |
| CORE-002 | Layered configuration (defaults/toml/env/overrides) | core | C | 1 | `src/quantlab/core/config.py` | CORE-001, CORE-005 | `tests/unit/core/test_config.py` | TESTED | Rejects `trading.mode=live` until LIVE-003; unknown keys rejected |
| CORE-003 | Structured logging with secret redaction | core | C | 1 | `src/quantlab/core/logging.py` | CORE-001 | `tests/unit/core/test_logging.py` | TESTED | Redaction is pattern-based (key names + text patterns); unusual secret formats in free text may slip through |
| CORE-004 | Metadata DB (SQLAlchemy 2 + Alembic, SQLite WAL) | core | C | 1 | `src/quantlab/core/db.py`, `src/quantlab/migrations/` (moved from core, DEC-0013) | CORE-002 | `tests/unit/core/test_db_audit.py` | TESTED | Linux + Windows CI green; PostgreSQL path written but untested (no server in CI) |
| CORE-005 | QUANTLAB_HOME layout & path resolution (Win/Linux) | core | C | 1 | `src/quantlab/core/paths.py` | — | `tests/unit/core/test_paths.py` | TESTED | Windows paths tested by simulation on Linux; real Windows run pending CI |
| CORE-006 | In-process event bus + ULID ids + Clock abstraction | core | C | 1 | `src/quantlab/core/ids.py`, `src/quantlab/core/clock.py` | CORE-001 | `tests/unit/core/test_ids_clock.py` | IN_PROGRESS | ids + clock TESTED; event bus not started (needed by OMS, Phase 5) |
| CORE-007 | Job runner (background runs, cancellation, progress) | core | C | 1 | `core/jobs.py`, `POST /api/jobs/backtest|sweep|walk-forward`, `GET /api/jobs`, `POST /api/jobs/{id}/cancel`, web Jobs page (CHANGE-0051, DEC-0036) | CORE-004 | `test_app.py` job tests, `jobs.test.tsx`; real LEAN run from the browser | TESTED | States queued/running/succeeded/failed/cancelled, cancel stops the engine (backtest) or skips unstarted trials (optimization); no progress percentage; the job list lives in server memory (results are stored) |
| CORE-008 | CLI (`quantlab`) | cli | C | 1 | `src/quantlab/cli/main.py`, `src/quantlab/bootstrap.py` | CORE-*, BT-* | `tests/unit/cli/test_cli.py`, `tests/unit/test_bootstrap.py` | TESTED | Commands: init, serve, strategy add/list/show/archive, backtest run/list/show/rerun, engine probe/pull, api token; `serve`/`engine pull` only smoke-tested manually; CLI coverage 77 % |
| CORE-009 | REST + WebSocket API with local token auth | api | C | 1 | `src/quantlab/api/app.py` | CORE-*, SEC-003 | `tests/unit/api/test_app.py` + manual uvicorn smoke test | IN_PROGRESS | Read-only REST done + TESTED (health, strategies, datasets list/detail/bars, backtests list/detail) + same-origin UI serving with CSP; no write endpoints, no WebSocket yet |
| CORE-010 | Audit log (append-only, hash-chained) | core | C | 1 | `src/quantlab/core/audit.py`, migration `0001` | CORE-004 | `tests/unit/core/test_db_audit.py` | TESTED | Append lock is per-process; multi-process writers would need DB-level serialisation |

## ENG — Trading engine integration
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| ENG-001 | EngineAdapter protocol | engine | C | 1 | `src/quantlab/engine/base.py` | CORE-001 | `tests/unit/engine/test_lean_docker.py::test_adapter_satisfies_protocol` | TESTED | Methods: probe/start_backtest/wait/cancel/collect; live intentionally excluded |
| ENG-002 | LEAN Docker launcher (pinned image digest) | engine | LI | 1 | `src/quantlab/engine/lean/docker.py` | ENG-001 | `tests/unit/engine/test_lean_docker.py` (fake docker), `tests/integration/test_lean_docker_e2e.py` (skipped) | IMPLEMENTED | **Never run against a real Docker daemon/LEAN image** (KI-0002); start/wait/timeout/cancel tested with a fake docker executable |
| ENG-003 | LEAN native .NET 10 launcher | engine | LI | 3 | `engine/lean/native.py`, `lean/QuantLabLauncher`, `quantlab engine native-install` (DEC-0032) | ENG-001 | `test_lean_native.py` (unit + real-LEAN golden) | VERIFIED | Reproduces LEAN's published statistics on its sample algorithm, in C# and Python (CHANGE-0045) |
| ENG-004 | LEAN config.json builder | engine | LI | 1 | `src/quantlab/engine/lean/config.py` | ENG-001 | `tests/unit/engine/test_lean_config.py` | TESTED | Keys from LEAN Launcher/config.json + Lean CLI layout; not yet accepted by a real LEAN run |
| ENG-005 | LEAN result/log parser (orders, stats, charts) | engine | LI | 1 | `src/quantlab/engine/lean/results.py` | ENG-002 | `tests/unit/engine/test_lean_results.py` | TESTED | Tested on a hand-built synthetic fixture only; logs captured to `engine.log` but not parsed |
| ENG-006 | Engine version probe recorded in manifests | engine | LI | 1 | `LeanDockerAdapter.probe()` | ENG-002 | `test_full_run_with_fake_docker`, `test_probe_errors` | IMPLEMENTED | Manifest recording is BT-002 (step 1d); probe untested against real Docker |
| ENG-007 | Python algorithms | engine | L | 1 | Docker image; native: `native.python_runtime`, LEAN's Python modules copied by `lean/QuantLabLauncher` (CHANGE-0045) | ENG-002/003 | `test_lean_native.py` (unit + real-LEAN golden `buy_and_hold_py`) | VERIFIED | Native backtests reproduce LEAN's published statistics and order hash; strategies see QuantLab's Python 3.11 packages; paper deployments run Python too (CHANGE-0046, real LEAN verified on synthetic quotes) |
| ENG-008 | C# algorithms (compile in container) | engine | L | 3 | `native.compile_csharp` (per-run compile against LEAN 2.5.18229), example `strategies/examples/buy_and_hold_cs` | ENG-003 | real-LEAN golden test | VERIFIED | Native launcher only (not in the Docker image) |
| ENG-009 | `quantlab_lean` helper library (platform-configured models, telemetry) | engine | C | 3 | `src/quantlab/engine/lean/library/quantlab_lean/`, `engine/lean/docker.py` (`prepare_library`, `models_confirmed`), `python-additional-paths` | ENG-004 | `tests/unit/engine/test_quantlab_lean.py` (recording stub of AlgorithmImports), fake-engine runs | TESTED | Opt-in `quantlab_lean.configure(self)`; read-only mount; marker confirms models were applied. LEAN API names checked in LEAN source cc98c42; never run inside real LEAN yet (KI-0051) |
| ENG-010 | Algorithm Framework (universe/alpha/portfolio/execution/risk models) | engine | L | 3 | — | ENG-007 | — | PLANNED | Exposed as-is |
| ENG-011 | Indicators (LEAN built-in) + custom indicators | engine | L | 3 | — | ENG-007 | — | PLANNED | |
| ENG-012 | Scheduled events, multi-resolution, consolidators | engine | L | 3 | — | ENG-007 | — | PLANNED | |
| ENG-013 | Custom data sources (PythonData / BaseData) | engine | LI | 3 | — | DATA-010 | — | PLANNED | |

## DATA — Market data infrastructure
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| DATA-001 | DataProvider adapter protocol + license metadata | data | C | 2 | `src/quantlab/data/providers/base.py` | CORE-* | `tests/unit/data/test_csv_provider.py` | TESTED | Only the file provider implements it so far |
| DATA-002 | CSV/Parquet file importer (user-owned data) | data | C | 2 | `src/quantlab/data/providers/csv_file.py`, `quantlab data import` | DATA-001, DATA-007 | `test_csv_provider.py`, `tests/unit/cli/test_cli_data.py` | TESTED | Column auto-detection by common names; ambiguous DST timestamps rejected |
| DATA-003 | Parquet storage + DuckDB catalog | data | C | 2 | `data/storage.py`, `data/catalog.py`, migration 0003 | CORE-005 | `tests/unit/data/test_catalog.py` | TESTED | Catalog of record in SQLite; DuckDB for reads |
| DATA-004 | Dataset versioning (content hash, immutable versions) | data | C | 2 | `data/storage.py`, `data/catalog.py` | DATA-003 | `test_catalog.py` | TESTED | Copy-on-write full rewrite per import (KI-0024) |
| DATA-005 | Validation: duplicates, gaps vs calendar, OHLC sanity, outliers, staleness | data | C | 2 | `src/quantlab/data/validation.py` | DATA-003, DATA-007 | `tests/unit/data/test_validation.py` | TESTED | Gap detection daily-only (intraday gaps not checked, KI-0025) |
| DATA-006 | Provenance labels (real/live/synthetic/sample) + completeness report | data | C | 2 | `data/schema.py`, `data/catalog.py`, `backtest/service.py` (DataBinding) | DATA-004 | `test_catalog.py`, `test_cli_data.py`, `tests/unit/backtest/test_service.py` | TESTED | Provenance required at dataset creation and propagated into backtest rows/manifests/metrics.json and CLI output |
| DATA-007 | Trading calendars & sessions | data | C | 2 | `src/quantlab/data/calendars.py` | — | `tests/unit/data/test_calendars.py` | TESTED | exchange_calendars from 1970 (CHANGE-0013 fix) + `24/7`/`weekdays`/`none`; `session_dates` shared by validation, actions and LEAN export; forex `weekdays` approximate; not reconciled with LEAN MHDB (KI-0026) |
| DATA-008 | Corporate actions: split/dividend factor files, map files | data | LI | 2 | `src/quantlab/data/corporate_actions.py`, catalog/storage/lean_export integration, `quantlab data import --actions`, `data actions` | DATA-003 | `tests/unit/data/test_corporate_actions.py` (golden vs LEAN's AAPL factor file), `test_actions_integration.py` | VERIFIED | Factor computation verified: real Tiingo AAPL history 1998–2021 → same 41 rows as LEAN's factor file (max rel. diff 2.6e-6). Splits + cash dividends only; LEAN reading the files still unverified (KI-0028) |
| DATA-009 | LEAN-format exporter (zipped CSV per version) | data | LI | 2 | `src/quantlab/data/lean_export.py`, `DataCatalog.lean_export`, `quantlab data lean-export` | DATA-004 | `tests/unit/data/test_lean_export.py` (golden round-trip vs LEAN sample bytes) | TESTED | Equities only (daily/hour/minute/second trade bars); neutral map/factor files; **not yet read by a real LEAN run** (KI-0028) |
| DATA-010 | User-defined custom datasets | data | C | 2 | — | DATA-001 | — | PLANNED | |
| DATA-011 | Free/low-cost provider adapters (e.g. Stooq/Tiingo/Alpha Vantage/Binance public) | data | X | 2 | `src/quantlab/data/providers/tiingo.py`, `DataCatalog.fetch`, `quantlab data fetch [--since-last]` | DATA-001, DATA-008, SEC-001 | `tests/unit/data/test_tiingo.py` (mock transport, synthetic Tiingo-format payloads), `test_cli_data.py` | VERIFIED | **Tiingo only**: EOD daily (raw + dividends/splits) and IEX 1-minute regular-session bars (IEX-only volume; 14-day windows, capped responses split, never accepted). Verified against the real API 2026-11-07 |
| DATA-012 | IBKR historical data adapter | data | X | 5 | — | BRK-002 | — | PLANNED | Needs IBKR market data subscriptions; pacing limits |
| DATA-013 | Real-time streaming + recording | data | X | 5 | `src/quantlab/data/market_data.py` (RealtimeProvider, Quote), `providers/mock_stream.py`, `quantlab md` | DATA-018 | `tests/unit/market/test_market_data.py`, `test_cli_data.py` | IN_PROGRESS | Interface + SIMULATED mock replay done; IBKR Level I streaming = DATA-019; recording not started |
| DATA-014 | Point-in-time universes, delisted symbols | data | X | 7 | — | DATA-008 | — | PLANNED | Survivorship-free data usually paid (Norgate/CRSP/QC library) |
| DATA-015 | Options/futures chains & continuous futures | data | LI/X | 7 | — | DATA-009 | — | PLANNED | Licensed data required |
| DATA-016 | Data export (Parquet/CSV) for zero lock-in | data | C | 2 | `DataCatalog.export`, `quantlab data export` | DATA-003 | `test_catalog.py`, `test_cli_data.py` | TESTED | Refuses licence-restricted data without `--acknowledge-license`; audited |
| DATA-017 | Delayed/stale market data detection | data | C | 5 | `market_data.normalize_quote`, `QuoteGuard` | DATA-018 | `tests/unit/market/test_market_data.py` | TESTED | Stale (by exchange time; IBKR kept tickers also by update age and during IB 1100, CHANGE-0044), clock skew, crossed, no-price flags; `NO_TRADE_FLAGS` refused by the risk gate and paper fills; guard refuses non-LIVE when live is required. Real-feed behaviour verified only with IBKR (DATA-019) |
| DATA-018 | Provider-agnostic MarketDataProvider (historical + real-time), DataState LIVE/DELAYED/FROZEN/SIMULATED, quote normalisation, QuoteGuard | data | C | 2 | `src/quantlab/data/market_data.py` | DATA-001 | `tests/unit/market/test_market_data.py` | TESTED | Real-time side exercised with the mock provider only |
| DATA-019 | IBKR market data provider (TWS/IB Gateway): historical bars, Level I streaming, market-data-type state, reconnection, pacing | data | X | 2f | `src/quantlab/data/providers/{ibkr,ib_async_client}.py`, `data.factory.ibkr_provider` | DATA-018, DATA-022 | `tests/unit/market/test_ibkr.py` (fake IB client; ib_async object translation) | TESTED | Not verified against a real IB Gateway (none reachable here); IB facts from ib_async source, IB docs unreadable here (KI-0044). Read-only connection, own client id |
| DATA-020 | Cross-provider comparison (`data compare`, `backtest compare`) | data/backtest | C | 2f | `src/quantlab/data/compare.py`, `BacktestService.compare_results` | DATA-018 | `test_market_data.py`, `test_cli_data.py`, `tests/unit/cli/test_cli.py` | VERIFIED | Real check: Tiingo vs LEAN sample (QuantConnect) SPY daily 2013–2021, 1888/1888 bars, max close diff 0.114 % |
| DATA-021 | Mock real-time provider (SIMULATED replay of catalog bars) | data | C | 2f | `src/quantlab/data/providers/mock_stream.py` | DATA-018 | `test_market_data.py` | TESTED | Synthetic bid/ask from close ± spread |
| DATA-022 | Provider selection by configuration (`[market_data]`) | data/core | C | 2f | `core/config.py` (MarketDataSettings), `data/factory.py` | DATA-018 | `test_market_data.py` | TESTED | `require_live` + mock rejected by config validation |

## RES — Research environment
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| RES-001 | Local JupyterLab launcher wired to QuantLab catalog | research | C | 2 | `src/quantlab/research/notebooks.py`, `quantlab research` | DATA-003 | `tests/unit/research/test_research.py` | TESTED | Optional extra (`uv sync --extra research`), not installed in CI; starter notebook executed for real with nbconvert (manual, CHANGE-0011); server UI launch not automated |
| RES-002 | `quantlab.research` API (history, indicators via shared code) | research | C | 2 | `src/quantlab/research/api.py` | DATA-003, AN-001 | `test_research.py` + real notebook execution | TESTED | history/closes/datasets/backtests + analytics delegation; provenance in `frame.attrs`, warning for non-real data. **No indicators** (planned via LEAN QuantBook, RES-003) |
| RES-003 | LEAN QuantBook support (research image) | research | L | 2 | — | DATA-009 | — | PLANNED | |
| RES-004 | Statistics: returns dist., correlation, factor analysis | research | C | 2 | `src/quantlab/analytics/statistics.py` | RES-002 | `tests/unit/analytics/test_statistics.py` | IN_PROGRESS | Done + TESTED: returns, distribution (moments, quantiles, historical VaR/ES), correlation, rolling volatility. Missing: factor analysis |
| RES-005 | Research report export (HTML/PDF) | research | C | 2 | — | RES-004 | — | PLANNED | |
| RES-006 | Promote notebook prototype → strategy | research/strategies | C | 4 | `StrategyWorkspace.promote_notebook`, `quantlab strategy from-notebook NOTEBOOK NAME --template T` | STR-001, STR-005 | `test_workspace.py`, CLI test | TESTED | Strategy from a template + notebook attached under `research/` with outputs stripped (no vendor data) + provenance (sha256) in `research/PROMOTED.md`; logic must still be ported to `main.py` |

## STR — Strategy management
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| STR-001 | Strategy registry (register/get/list/archive; import = register by path) | strategies | C | 1 | `src/quantlab/strategies/registry.py` | CORE-004 | `tests/unit/strategies/test_registry.py` | TESTED | Clone not implemented yet; archive audited, never deletes |
| STR-002 | Strategy versioning (tree hash + git commit/dirty) | strategies | C | 1 | `src/quantlab/strategies/snapshot.py`, `registry.py` | STR-001 | `tests/unit/strategies/test_snapshot.py`, `test_registry.py` | TESTED | Version diff (compare two versions' files) not implemented; byte-exact hash (CRLF differences matter, KI-0020) |
| STR-003 | Strategy parameters schema (`strategy.toml`) | strategies | LI | 1 | `registry.py: load_manifest, resolve_parameters` | STR-001 | `test_registry.py` | TESTED | Delivery to LEAN `get_parameter` not yet verified with a real run (KI-0002) |
| STR-004 | Lifecycle state (Research→…→Monitoring) with gates | strategies/lifecycle | C | 4 | `src/quantlab/strategies/lifecycle.py` (append-only history, migration 0005), `src/quantlab/lifecycle/service.py` (gates), `quantlab strategy status|promote|demote|retire`, `GET /api/strategies/{name}/lifecycle`, stage column in the library | STR-001, OPT-003, OPT-005 | `tests/unit/lifecycle/`, CLI/API tests; browser check | TESTED | research→backtested→validated→paper→live_candidate (+retired). Evidence must be from the current files and real data; thresholds in `[lifecycle]`, recorded per event. `live_candidate` gate always refused until Phase 5 paper records exist |
| STR-005 | Strategy templates (momentum, mean-rev, pairs, …) as examples | strategies | C | 3 | `strategies/templates/{momentum_rotation,rsi_mean_reversion,pairs_zscore}` | ENG-009 | `tests/unit/strategies/test_templates.py`, fake-engine CLI runs | IMPLEMENTED | Syntax, manifests, pairs math tested; fake engine confirms they apply the models. Never run on real LEAN (KI-0050) |

## BT — Backtesting
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| BT-001 | Backtest submission service | backtest | LI | 1 | `src/quantlab/backtest/service.py` | ENG-001, STR-002, AN-001 | `tests/unit/backtest/test_service.py` (fake docker) | TESTED | Synchronous; engine path tested only with fake docker; sample data only |
| BT-002 | Reproducibility manifest (code/data/engine/config/seeds) | backtest | C | 1 | `service.py` (`manifest.json`, `inputs_hash`, `DataBinding`) | BT-001, ENG-006, DATA-009 | `test_service.py` | TESTED | Data = sample or pinned dataset version + LEAN export hash; rerun refuses changed exports |
| BT-003 | Immutable backtest history & re-run diff | backtest | C | 1 | `backtest/models.py`, migration 0002, `BacktestService.rerun/compare` | BT-002 | `test_service.py` | TESTED | Reproducibility proven only against the deterministic fake engine; real LEAN determinism unverified |
| BT-004 | Event-driven simulation, multi-asset/strategy/timeframe | backtest | L | 1 | — | ENG-007 | — | PLANNED | |
| BT-005 | Fee, slippage, fill, brokerage, margin, buying-power models | backtest | L | 3 | `core/config.py` (`[backtest.models]`), `quantlab_lean`, `backtest run -m key=value` | ENG-009 | `test_quantlab_lean.py`, `test_service.py`, `test_cli.py` | TESTED | Brokerage (default / Interactive Brokers) + account type (margin/cash), fee (brokerage/zero/constant), slippage (brokerage/none/constant/volume share), fill (brokerage/immediate). Margin/buying power = the brokerage model's. Recorded in manifest + inputs hash. Stub-tested only (KI-0051) |
| BT-006 | Partial fills, cancellation, market impact | backtest | L/C | 3 | — | BT-005 | — | PLANNED | Impact models custom |
| BT-007 | Settlement (cash accounts, T+1) | backtest | L | 3 | `[backtest.models]` settlement/account_type, `quantlab_lean.settlement_model` | BT-005 | `test_quantlab_lean.py` | TESTED | LEAN source (cc98c42): US equity cash accounts settle T+1 at 06:00 by default (`Equity.DefaultSettlementDays = 1`); QuantLab can force immediate or delayed (days, hour). Stub-tested only |
| BT-008 | Benchmark comparison | backtest/analytics | C | 3 | `analytics/charts.benchmark_comparison`, `BacktestService.chart_data(benchmark=)` | AN-002 | `tests/unit/analytics/test_charts.py`, `tests/unit/backtest/test_service.py` | TESTED | Benchmark = catalog dataset symbol, split+dividend adjusted (total return); beta, CAPM alpha, correlation, tracking error, IR, excess return; provenance warning for non-real benchmarks. Synthetic data only so far |
| BT-009 | Multi-strategy portfolio backtest (combined books) | backtest | C | 7 | — | BT-003 | — | PLANNED | LEAN runs one algorithm per process |

## AN — Performance analytics
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| AN-001 | Metrics library (return, CAGR, vol, Sharpe, Sortino, Calmar, MDD, DD duration, win rate, PF, expectancy, turnover, exposure, alpha/beta, costs) | analytics | C | 1 | `src/quantlab/analytics/metrics.py`, `analytics/charts.py` (trade stats, alpha/beta) | — | `tests/unit/analytics/test_metrics.py`, `test_charts.py` | IN_PROGRESS | Done + TESTED: total return, CAGR, vol, Sharpe, Sortino, Calmar, MDD, DD duration, best/worst day, cross-check vs LEAN (matches real LEAN on total return / CAGR / drawdown since DEC-0032; Sharpe differs by definition, KI-0079); win rate, profit factor, expectancy (gross, from filled orders); alpha/beta vs benchmark (BT-008). Missing: turnover, exposure, costs (need LEAN order events/holdings, KI-0048) |
| AN-002 | Chart datasets (equity, drawdown, monthly/annual, trade dist., allocation, exposure, rolling Sharpe, benchmark) | analytics | C | 3 | `src/quantlab/analytics/charts.py`, `BacktestService.chart_data`, `GET /api/backtests/{id}/charts` | AN-001 | `tests/unit/analytics/test_charts.py`, service + API tests | TESTED | Equity, drawdown, monthly/annual, rolling Sharpe, FIFO trades + stats, benchmark overlay. Missing: allocation, exposure (need holdings, KI-0048). Fake-engine data only |
| AN-003 | Downloadable report (HTML/PDF + config bundle) | analytics | C | 3 | `src/quantlab/analytics/report.py`, `BacktestService.build_report/report_html`, `quantlab backtest report`, `GET /api/backtests/{id}/report` | AN-002 | `tests/unit/analytics/test_report.py`, service/CLI/API tests | TESTED | Self-contained HTML (inline SVG, no scripts, offline) + zip bundle (charts, metrics, manifest, results, strategy snapshot; never market data). PDF via the browser's print (print CSS); no native PDF |
| AN-004 | Strategy/run comparison | analytics | C | 4 | `analytics/comparison.py`, `BacktestService.compare_many`, `GET /api/compare`, `web/src/pages/Compare.tsx` (select runs in Backtesting) | AN-001 | `test_charts.py`, API test; browser check | VERIFIED | 2–10 runs: rebased equity, metrics, return correlation on common dates, provenance warnings |

## OPT — Optimization & robustness
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| OPT-001 | Grid / random / parameter sweep | optimize | C | 4 | `src/quantlab/optimize/{space,service}.py`, `quantlab optimize sweep` | BT-001 | `tests/unit/optimize/` (fake engine, synthetic), CLI/API tests | TESTED | Every trial is a BacktestService run; dataset version pinned by the first trial; optional parallel trials. LEAN's Optimizer launcher not used (DEC-0025) |
| OPT-002 | Bayesian & multi-objective (Optuna) | optimize | C | 4 | `OptimizationService._run_tpe` (Optuna TPE, seeded; extra `optimize`), `--tpe N`, `--extra-objective` | OPT-001 | `tests/unit/optimize/test_service.py` (seeded, distinct, Pareto non-dominance), CLI test | TESTED | Proposals from the same discrete space; duplicates told from cache, never re-run; failed/rejected trials told as FAIL; Pareto front for ≥2 objectives |
| OPT-003 | Walk-forward / rolling-window with IS/OOS labels | optimize | C | 4 | `OptimizationService.walk_forward`, `WalkForwardSpec`, `quantlab optimize walk-forward` | OPT-001 | `tests/unit/optimize/test_service.py`, CLI test | TESTED | Rolling or anchored calendar-day windows; trials labelled IS/OOS; stitched OOS equity + metrics; walk-forward efficiency; needs `start`/`end` strategy parameters |
| OPT-004 | Monte Carlo & bootstrap | optimize/analytics | C | 4 | `analytics/robustness.py` (`stationary_bootstrap`, `trade_monte_carlo`), `BacktestService.robustness`, `quantlab backtest robustness`, `GET /api/backtests/{id}/robustness` | AN-001 | `tests/unit/analytics/test_robustness.py`, CLI/API tests | TESTED | Seeded, recorded; descriptive resampling, not forecasts |
| OPT-005 | Sensitivity/robustness heatmaps, overfitting metrics (e.g. deflated Sharpe, PBO) | optimize/analytics | C | 4 | `analytics/robustness.py` (`deflated_sharpe`, `probability_of_backtest_overfitting`, `sensitivity`); in every sweep summary | OPT-001 | `test_robustness.py` (closed form vs Monte Carlo), `tests/unit/optimize/` | TESTED | DSR per Bailey & López de Prado 2014; PBO via CSCV (Bailey et al. 2017); marginal + neighbourhood ratio. Heatmap view: UI-005 |
| OPT-006 | Custom objective functions | optimize | C | 4 | `optimize/objective.py` (allow-listed arithmetic over metrics, constraints) | OPT-001 | `tests/unit/optimize/test_objective_space.py` | TESTED | No eval; metrics + trade stats; constraints reject trials |

## PORT — Portfolio management
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| PORT-001 | Accounts & books (theoretical vs broker-confirmed) | portfolio | C | 5 | `src/quantlab/portfolio/book.py`, `oms/queries.confirmed_book` | OMS-003 | `tests/unit/trading/` | TESTED | Confirmed book from fills only (DEC-0008); one paper account. Theoretical (strategy) book arrives with OMS-007 |
| PORT-002 | Positions, cash, realized/unrealized P&L from fills | portfolio | C | 5 | `portfolio/book.py` (average cost), `quantlab paper positions`, `GET /api/paper/portfolio` | PORT-001 | `test_paper_broker_book.py` | TESTED | Marks from configured real-time quotes; API snapshot is unmarked |
| PORT-003 | Exposure: asset, sector, risk contribution | portfolio | C | 5 | — | PORT-002 | — | PLANNED | Sector data source needed |
| PORT-004 | Position sizing models (fixed qty/$, %equity, vol, ATR, risk budget, capped Kelly) | portfolio | C | 5 | — | PORT-002 | — | PLANNED | |
| PORT-005 | Multi-strategy capital allocation & rebalancing | portfolio | C | 7 | — | PORT-004 | — | PLANNED | |

## OMS — Order management
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| OMS-001 | Order model (market/limit/stop/stop-limit/trailing/bracket/OCO, TIF) | oms | C | 5 | `oms/models.py`, `brokers/base.py` | CORE-004 | `tests/unit/trading/` | IN_PROGRESS | Market, limit, stop, stop-limit; DAY/GTC. Trailing, bracket, OCO not yet |
| OMS-002 | Formal order state machine | oms | C | 5 | `oms/states.py` (+ NOT_PLACED for unknowns resolved by reconciliation) | OMS-001 | `test_oms_risk.py` | TESTED | Illegal transitions refused; unexpected broker events recorded as anomalies |
| OMS-003 | Event-sourced order/fill store | oms | C | 5 | `orders`, `order_events`, `fills` (migration 0006, append-only triggers) | OMS-002 | `test_oms_risk.py` | TESTED | Current state = latest event |
| OMS-004 | Idempotency keys & duplicate prevention | oms | C | 5 | `orders.client_order_key` unique; `(broker, exec_id)` unique for fills | OMS-003 | `test_oms_risk.py`, CLI test | TESTED | A repeated key returns the existing order and sends nothing |
| OMS-005 | Timeouts, expiry auto-cancel, SUBMIT_UNKNOWN resolution | oms | C | 5 | `OMS.submit` (SubmitUnknown / unexpected adapter errors → SUBMIT_UNKNOWN), `OMS.reconcile`, paper DAY expiry | OMS-002 | `test_oms_risk.py` (incl. crash after PENDING_SUBMIT) | TESTED | Never resubmits; resolution only from broker facts. No timer-based expiry daemon yet |
| OMS-006 | Modify/cancel, reserved buying power | oms | C | 5 | `OMS.cancel`, `OMS.cancel_all`, gate "buying power" | OMS-002, RISK-001 | `test_oms_risk.py` | IN_PROGRESS | Cancel, cancel-all and reserved buying power done; modify not yet |
| OMS-007 | LEAN ↔ OMS gateway brokerage plugin | oms | LI | 5 | `oms/gateway.py`, `quantlab paper gateway`, `lean/QuantLabBrokerage` (DEC-0031) | DEC-0007 | `test_gateway.py`, `lean/QuantLabBrokerage.Tests` (vs the real gateway) | VERIFIED | Real LEAN (live mode, native) placed a paper order through gateway → OMS → risk gate → paper broker and received the fill (CHANGE-0039); synthetic prices; no live data feed for deployments yet |

## BRK — Brokerage
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| BRK-001 | BrokerAdapter protocol + contract test suite | brokers | C | 5 | `brokers/base.py` (SequencedEvent, BrokerSnapshot) | OMS-001 | `test_paper_broker_book.py`, `test_ibkr_broker.py` | IN_PROGRESS | Protocol implemented by the local and IBKR paper adapters; a shared contract suite run against both is not yet extracted |
| BRK-002 | Simulated paper broker (local) | brokers | C | 5 | `brokers/paper.py`, `[paper]` config, `quantlab paper …` | BRK-001 | `test_paper_broker_book.py`, `test_oms_risk.py`, CLI test | TESTED | Fills from quotes (no partial fills/impact, KI-0061); persisted state under an OS lock; events acked by sequence; snapshot for reconcile; failure injection (CHANGE-0027) |
| BRK-003 | IBKR adapter (TWS/IB Gateway): account, positions, funds, orders, executions, commissions | brokers | X | 5 | `brokers/ibkr.py`, `brokers/ib_async_trading.py`, `[paper] broker = "ibkr"` (DEC-0029) | BRK-001 | `test_ibkr_broker.py` (fake client) | IMPLEMENTED | Paper accounts only; not yet run against a real gateway (KI-0075) |
| BRK-004 | Connection monitoring & automatic reconnection | brokers | C | 5 | `IBKRPaperBroker._ensure` (reconnect on every call, re-verifies paper accounts) | BRK-003 | `test_ibkr_broker.py` | IMPLEMENTED | Reconnect-on-use; continuous monitoring comes with the supervisor (LIVE-001) |
| BRK-005 | Broker reconciliation (positions, open orders, cash) | brokers/oms | C | 5 | `OMS.reconcile`, `quantlab paper reconcile` | BRK-003, PORT-002 | `test_oms_risk.py` | TESTED | Paper broker only; reports differences, never rewrites positions |
| BRK-006 | Additional broker/exchange adapters | brokers | X | 7 | — | BRK-001 | — | PLANNED | |

## RISK — Risk management
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| RISK-001 | Central pre-trade risk gate (non-bypassable) | risk | C | 5 | `risk/gate.py`; OMS is the only caller of `BrokerAdapter.submit`; import contract forbids other modules importing oms/brokers/risk | OMS-002 | `test_oms_risk.py` | TESTED | Fails closed (no/invalid limits, no quote, unknown equity) |
| RISK-002 | Rule set: daily loss, drawdown, position/order size, exposure, leverage, #positions, sector, frequency, volatility, liquidity, spread, session, cooldown | risk | C | 5 | `risk/limits.py` (required keys, no defaults), `quantlab risk init|show` | RISK-001 | `test_oms_risk.py` | IN_PROGRESS | Done: order qty/notional, position notional, gross exposure, open orders, rate, daily loss + drawdown (reduce-only), rejection cooldown, cash-account buying power, quote state/age, spread, shorting, symbol allow-list, session (CHANGE-0028). Not yet: sector, volatility, liquidity |
| RISK-003 | Risk decision log (reason, timestamp) | risk | C | 5 | `risk_decisions` (append-only, every check + limits sha256), `quantlab risk decisions`, `GET /api/risk` | RISK-001 | `test_oms_risk.py`, API test | TESTED | |
| RISK-004 | Kill switch (block new orders; configurable cancel/flatten) | risk | C | 5 | `KillSwitch` (append-only events, audit), `quantlab risk kill-switch engage|disengage` | RISK-001 | `test_oms_risk.py`, CLI test | TESTED | Blocks all new orders; engage cancels working paper orders (`OMS.cancel_all`, reports leftovers); disengage needs the typed phrase. No automatic flatten |
| RISK-005 | Stop-loss / trailing-stop policy rules | risk | C | 5 | — | RISK-002 | — | PLANNED | |
| RISK-006 | Settlement policy (T+1, cash account free-riding guard) | risk | C | 5 | `risk/settlement.py`, gate "buying power" uses settled cash, `settlement_days` limit (CHANGE-0050) | PORT-002 | `test_settlement.py` | TESTED | Cash accounts buy only with settled cash: sale proceeds settle on the next trading day (US/Eastern trade date, configured calendar; weekdays without one). Conservative: also blocks buys a broker would allow as good faith if held |
| RISK-007 | Advanced risk analytics (VaR/ES, stress tests) | risk | C | 7 | — | PORT-003 | — | PLANNED | |

## LIVE — Paper/live deployment
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| LIVE-001 | Deployment supervisor (start/pause/stop/monitor) | live | LI | 5 | `oms/supervisor.py`, `quantlab paper supervise|supervisor-status`, `GET /api/paper/supervisor` (DEC-0030) | ENG-002, OMS-* | `test_supervisor.py`, CLI/API/web tests | IN_PROGRESS | Account supervision done; `quantlab paper deploy` runs a LEAN strategy on paper with QuantLab prices (CHANGE-0041, real LEAN verified on synthetic quotes); deployments listed in the console (status, data states, log; CHANGE-0043); pause/resume per deployment (CHANGE-0047, DEC-0034); strategy process without network on Linux/WSL2 (CHANGE-0048, DEC-0035); both real-LEAN verified |
| LIVE-002 | Paper mode default | live | C | 5 | `trading.mode` rejects live; OMS refuses non-paper brokers; every CLI line `[PAPER]` | LIVE-001 | `test_oms_risk.py` | TESTED | |
| LIVE-003 | Live activation safety gates (separate creds, limits set, broker verified, typed confirmation, status banner) | live | C | 6 | — | RISK-*, BRK-005 | — | PLANNED | Disabled until owner approves |
| LIVE-004 | Crash/restart recovery with reconciliation-before-trading | live | C | 6 | — | BRK-005 | — | PLANNED | |

## ML — Machine learning
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| ML-001 | Feature/label generation on versioned datasets | ml | C | 7 | — | DATA-004 | — | PLANNED | |
| ML-002 | Time-series CV (purged/embargoed, walk-forward) | ml | C | 7 | — | ML-001 | — | PLANNED | |
| ML-003 | Training/tuning (sklearn, LightGBM, XGBoost, PyTorch, TF optional) | ml | C | 7 | — | ML-002 | — | PLANNED | |
| ML-004 | Model registry & versioning | ml | C | 7 | — | CORE-004 | — | PLANNED | |
| ML-005 | Model deployment into LEAN strategies | ml | LI | 7 | — | ML-004 | — | PLANNED | Packages must exist in LEAN image |
| ML-006 | Prediction monitoring & drift detection | ml | C | 7 | — | ML-005 | — | PLANNED | |

## AI — AI-native development
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| AI-001 | Provider abstraction (Anthropic, OpenAI, Ollama) | ai | C/X | 8 | — | CORE-002 | — | PLANNED | API keys user-provided |
| AI-002 | Permissioned tool gateway + audit | ai | C | 8 | — | CORE-010 | — | PLANNED | No trading/credential tools by design |
| AI-003 | Project context retrieval (docs, code, runs, logs) | ai | C | 8 | — | AI-002 | — | PLANNED | |
| AI-004 | Assistants: strategy, code, debug, backtest analysis, research, docs, tests | ai | C | 8 | — | AI-003 | — | PLANNED | |
| AI-005 | Proposed-change workflow (diff + tests + human approval) | ai | C | 8 | — | AI-004 | — | PLANNED | |

## UI — Workstation
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| UI-001 | App shell, workspaces, resizable panels, search | web | C | 2 | `web/` (React 19 + TS + Vite), served by `quantlab serve` | CORE-009 | `web/src/*.test.ts(x)` (16 vitest) + manual Playwright smoke | IN_PROGRESS | Shell, navigation for all 18 modules, token flow, paper-mode indicator done; no resizable panels, workspaces or search yet |
| UI-002 | Backtests list + run detail (charts, stats, orders) | web | C | 3 | `web/src/pages/{Backtests,BacktestCharts}.tsx`, `components/TimeSeriesChart.tsx` | BT-003, AN-002 | vitest; browser check 2026-11-07 (fake-engine run, no CSP errors) | TESTED | Equity (+benchmark), drawdown, monthly grid, trade stats, orders, benchmark form, report download |
| UI-003 | Algorithm editor (CodeMirror) + strategy library | web/strategies/api | C | 3 | `src/quantlab/strategies/workspace.py`, `/api/strategies/{name}/files[/{path}]` (GET/PUT), `POST /api/strategies`, `/api/strategy-templates`, `web/src/pages/{Editor,Strategies}.tsx`, `components/CodeEditor.tsx` | STR-001, STR-005 | `tests/unit/strategies/test_workspace.py`, API tests, vitest; browser check 2026-11-07 (create from template, edit, Ctrl+S, conflict refused, no CSP violations) | VERIFIED | Path-safe, conflict-checked, audited writes; strategy.toml validated; CodeMirror in a shadow root keeps CSP strict (DEC-0024). No delete/rename yet |
| BT-010 | Golden-result regression suite (record / list / check, tolerance, engine-change handling) | backtest | C | 3 | `src/quantlab/backtest/golden.py`, `quantlab backtest golden record|list|check`, `BacktestService.rerun(require_same_engine=)` | BT-003 | `tests/unit/backtest/test_golden.py`, CLI test (fake engine) | TESTED | No golden cases from real LEAN exist yet: record them from the owner's first real runs (KI-0053) |
| UI-004 | Market data browser & quality reports | web | C | 2 | `web/src/pages/Datasets.tsx`, `GET /api/datasets/{name}[/bars]` | DATA-005 | `tests/unit/api/test_app.py`, vitest, Playwright smoke | TESTED | Read-only (imports via CLI); close-price line chart only |
| UI-005 | Optimization views | web | C | 4 | `web/src/pages/Optimizations.tsx` (list, sweep detail with overfitting checks, heatmap, sensitivity, Pareto, top trials; walk-forward windows + OOS curve), robustness panel in run detail | OPT-* | vitest (heatmap); browser check 2026-11-07 (no console/CSP errors) | VERIFIED | Studies are started from the CLI (read-only UI) |
| UI-006 | Portfolio, orders, risk, paper/live consoles, kill switch | web | C | 5 | `web/src/pages/Trading.tsx`, `POST /api/risk/kill-switch/engage` (DEC-0028) | PORT/OMS/RISK | `trading.test.tsx`, API tests, Playwright check (CHANGE-0029) | TESTED | Paper read-only + engage-only kill switch; positions unmarked in the UI; no live console (LIVE-003) |
| UI-007 | ML, AI assistant, logs/monitoring, settings, project mgmt views | web | C | 7–8 | — | — | — | PLANNED | |
| UI-008 | "Unavailable feature" indicator convention (no dead buttons) | web | C | 2 | `web/src/modules.ts`, `pages/ModulePage.tsx` | UI-001 | `modules.test.ts`, `components.test.tsx` | TESTED | Every module listed; planned ones show phase + feature IDs and contain no controls |

## OPS — Monitoring & reliability
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| OPS-001 | Health/status endpoints (engine, broker, data feeds, strategies) | ops | C | 5 | `oms/health.py`, `quantlab health` (exit 1 on fail), `GET /api/health/checks`, Dashboard panel (CHANGE-0049) | CORE-009 | `test_health_alerts.py`, CLI/API/web tests | TESTED | Database, risk limits, kill switch, supervisor, deployments, engine install; read-only, never contacts a broker (broker/feed health comes through the supervisor's reconcile) |
| OPS-002 | Alerts & notifications (desktop/email/webhook) | ops | C | 5 | `oms/alerts.py`; from `paper supervise` every tick and `quantlab health --alert` (Task Scheduler / cron); `[alerts] webhook_secret` (CHANGE-0049) | OPS-001 | `test_health_alerts.py` (incl. local webhook receiver) | TESTED | Changes, recoveries, hourly reminders to logs/alerts.jsonl + optional https webhook; email / desktop notifications not implemented |
| OPS-003 | Process supervision & auto-restart | ops | C | 6 | — | LIVE-001 | — | PLANNED | |
| OPS-004 | Backup & restore | ops | C | 9 | — | CORE-005 | — | PLANNED | |
| OPS-005 | Resource & performance monitoring | ops | C | 9 | — | OPS-001 | — | PLANNED | |

## SEC — Security
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| SEC-001 | Secret store via keyring (Windows Credential Manager) + env fallback | core | C | 1 | `src/quantlab/core/secret_store.py` | CORE-002, CORE-010 | `tests/unit/core/test_secrets.py` | TESTED | Writes verified by read-back (CHANGE-0008); real Windows Credential Manager not exercised (tests use in-memory keyring by design) |
| SEC-002 | Separate paper/live credential namespaces | core | C | 5 | — | SEC-001 | — | PLANNED | |
| SEC-003 | Local API protection (127.0.0.1 bind + token) | api | C | 1 | `api/app.py`, `bootstrap.py`, config validation | CORE-009, SEC-001 | `tests/unit/api/test_app.py`, `tests/unit/core/test_config.py` | TESTED | Bearer token in keyring/env; fail-closed; Host allow-list; verified over real HTTP (smoke) |
| SEC-004 | Remote access auth + TLS guidance | api | C | 9 | — | SEC-003 | — | PLANNED | |
| SEC-005 | AI permission boundaries (import-linter + gateway allowlist) | ai | C | 8 | — | AI-002 | — | PLANNED | |

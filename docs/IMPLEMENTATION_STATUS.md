# Implementation Status — Canonical Feature Registry

**This is the single source of truth for feature status.** Search here (and the repo) before implementing anything.
Other docs reference these IDs and must not keep their own status copies.

Status vocabulary:
`PLANNED` · `IN_PROGRESS` · `IMPLEMENTED` (behaviour exists & integrated) · `TESTED` (automated tests pass) ·
`VERIFIED` (validated against real data/engine/broker or by the owner) · `DEPRECATED` · `BLOCKED`.

Approach codes: **L** = provided by LEAN (configure/integrate only) · **LI** = LEAN + QuantLab integration layer ·
**C** = custom QuantLab development · **X** = depends on external licensed data/service (adapter only).

Phase = roadmap phase in [ROADMAP.md](ROADMAP.md). Location/tests are filled when work starts.

Last updated: 2026-11-07 (CHANGE-0012)

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
| CORE-007 | Job runner (background runs, cancellation, progress) | core | C | 1 | — | CORE-004 | — | PLANNED | |
| CORE-008 | CLI (`quantlab`) | cli | C | 1 | `src/quantlab/cli/main.py`, `src/quantlab/bootstrap.py` | CORE-*, BT-* | `tests/unit/cli/test_cli.py`, `tests/unit/test_bootstrap.py` | TESTED | Commands: init, serve, strategy add/list/show/archive, backtest run/list/show/rerun, engine probe/pull, api token; `serve`/`engine pull` only smoke-tested manually; CLI coverage 77 % |
| CORE-009 | REST + WebSocket API with local token auth | api | C | 1 | `src/quantlab/api/app.py` | CORE-*, SEC-003 | `tests/unit/api/test_app.py` + manual uvicorn smoke test | IN_PROGRESS | Read-only REST done + TESTED (health, strategies, datasets list/detail/bars, backtests list/detail) + same-origin UI serving with CSP; no write endpoints, no WebSocket yet |
| CORE-010 | Audit log (append-only, hash-chained) | core | C | 1 | `src/quantlab/core/audit.py`, migration `0001` | CORE-004 | `tests/unit/core/test_db_audit.py` | TESTED | Append lock is per-process; multi-process writers would need DB-level serialisation |

## ENG — Trading engine integration
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| ENG-001 | EngineAdapter protocol | engine | C | 1 | `src/quantlab/engine/base.py` | CORE-001 | `tests/unit/engine/test_lean_docker.py::test_adapter_satisfies_protocol` | TESTED | Methods: probe/start_backtest/wait/cancel/collect; live intentionally excluded |
| ENG-002 | LEAN Docker launcher (pinned image digest) | engine | LI | 1 | `src/quantlab/engine/lean/docker.py` | ENG-001 | `tests/unit/engine/test_lean_docker.py` (fake docker), `tests/integration/test_lean_docker_e2e.py` (skipped) | IMPLEMENTED | **Never run against a real Docker daemon/LEAN image** (KI-0002); start/wait/timeout/cancel tested with a fake docker executable |
| ENG-003 | LEAN native .NET 10 launcher | engine | LI | 3 | — | ENG-001 | — | PLANNED | Requires local LEAN build |
| ENG-004 | LEAN config.json builder | engine | LI | 1 | `src/quantlab/engine/lean/config.py` | ENG-001 | `tests/unit/engine/test_lean_config.py` | TESTED | Keys from LEAN Launcher/config.json + Lean CLI layout; not yet accepted by a real LEAN run |
| ENG-005 | LEAN result/log parser (orders, stats, charts) | engine | LI | 1 | `src/quantlab/engine/lean/results.py` | ENG-002 | `tests/unit/engine/test_lean_results.py` | TESTED | Tested on a hand-built synthetic fixture only; logs captured to `engine.log` but not parsed |
| ENG-006 | Engine version probe recorded in manifests | engine | LI | 1 | `LeanDockerAdapter.probe()` | ENG-002 | `test_full_run_with_fake_docker`, `test_probe_errors` | IMPLEMENTED | Manifest recording is BT-002 (step 1d); probe untested against real Docker |
| ENG-007 | Python algorithms | engine | L | 1 | — | ENG-002 | — | PLANNED | Python 3.11 only |
| ENG-008 | C# algorithms (compile in container) | engine | L | 3 | — | ENG-002 | — | PLANNED | |
| ENG-009 | `quantlab_lean` helper library (platform-configured models, telemetry) | engine | C | 3 | — | ENG-004 | — | PLANNED | |
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
| DATA-007 | Trading calendars & sessions | data | C | 2 | `src/quantlab/data/calendars.py` | — | `tests/unit/data/test_calendars.py` | TESTED | exchange_calendars + `24/7`/`weekdays`/`none`; forex `weekdays` is approximate; not reconciled with LEAN MHDB (KI-0026) |
| DATA-008 | Corporate actions: split/dividend factor files, map files | data | LI | 2 | — | DATA-003 | — | PLANNED | Accurate factors require a data source that provides them |
| DATA-009 | LEAN-format exporter (zipped CSV per version) | data | LI | 2 | `src/quantlab/data/lean_export.py`, `DataCatalog.lean_export`, `quantlab data lean-export` | DATA-004 | `tests/unit/data/test_lean_export.py` (golden round-trip vs LEAN sample bytes) | TESTED | Equities only (daily/hour/minute/second trade bars); neutral map/factor files; **not yet read by a real LEAN run** (KI-0028) |
| DATA-010 | User-defined custom datasets | data | C | 2 | — | DATA-001 | — | PLANNED | |
| DATA-011 | Free/low-cost provider adapters (e.g. Stooq/Tiingo/Alpha Vantage/Binance public) | data | X | 2 | — | DATA-001 | — | PLANNED | ToS & rate limits per provider |
| DATA-012 | IBKR historical data adapter | data | X | 5 | — | BRK-002 | — | PLANNED | Needs IBKR market data subscriptions; pacing limits |
| DATA-013 | Real-time streaming + recording | data | X | 5 | — | DATA-012 | — | PLANNED | |
| DATA-014 | Point-in-time universes, delisted symbols | data | X | 7 | — | DATA-008 | — | PLANNED | Survivorship-free data usually paid (Norgate/CRSP/QC library) |
| DATA-015 | Options/futures chains & continuous futures | data | LI/X | 7 | — | DATA-009 | — | PLANNED | Licensed data required |
| DATA-016 | Data export (Parquet/CSV) for zero lock-in | data | C | 2 | `DataCatalog.export`, `quantlab data export` | DATA-003 | `test_catalog.py`, `test_cli_data.py` | TESTED | Refuses licence-restricted data without `--acknowledge-license`; audited |
| DATA-017 | Delayed/stale market data detection | data | C | 5 | — | DATA-013 | — | PLANNED | |

## RES — Research environment
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| RES-001 | Local JupyterLab launcher wired to QuantLab catalog | research | C | 2 | `src/quantlab/research/notebooks.py`, `quantlab research` | DATA-003 | `tests/unit/research/test_research.py` | TESTED | Optional extra (`uv sync --extra research`), not installed in CI; starter notebook executed for real with nbconvert (manual, CHANGE-0011); server UI launch not automated |
| RES-002 | `quantlab.research` API (history, indicators via shared code) | research | C | 2 | `src/quantlab/research/api.py` | DATA-003, AN-001 | `test_research.py` + real notebook execution | TESTED | history/closes/datasets/backtests + analytics delegation; provenance in `frame.attrs`, warning for non-real data. **No indicators** (planned via LEAN QuantBook, RES-003) |
| RES-003 | LEAN QuantBook support (research image) | research | L | 2 | — | DATA-009 | — | PLANNED | |
| RES-004 | Statistics: returns dist., correlation, factor analysis | research | C | 2 | `src/quantlab/analytics/statistics.py` | RES-002 | `tests/unit/analytics/test_statistics.py` | IN_PROGRESS | Done + TESTED: returns, distribution (moments, quantiles, historical VaR/ES), correlation, rolling volatility. Missing: factor analysis |
| RES-005 | Research report export (HTML/PDF) | research | C | 2 | — | RES-004 | — | PLANNED | |
| RES-006 | Promote notebook prototype → strategy | research | C | 4 | — | STR-001 | — | PLANNED | |

## STR — Strategy management
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| STR-001 | Strategy registry (register/get/list/archive; import = register by path) | strategies | C | 1 | `src/quantlab/strategies/registry.py` | CORE-004 | `tests/unit/strategies/test_registry.py` | TESTED | Clone not implemented yet; archive audited, never deletes |
| STR-002 | Strategy versioning (tree hash + git commit/dirty) | strategies | C | 1 | `src/quantlab/strategies/snapshot.py`, `registry.py` | STR-001 | `tests/unit/strategies/test_snapshot.py`, `test_registry.py` | TESTED | Version diff (compare two versions' files) not implemented; byte-exact hash (CRLF differences matter, KI-0020) |
| STR-003 | Strategy parameters schema (`strategy.toml`) | strategies | LI | 1 | `registry.py: load_manifest, resolve_parameters` | STR-001 | `test_registry.py` | TESTED | Delivery to LEAN `get_parameter` not yet verified with a real run (KI-0002) |
| STR-004 | Lifecycle state (Research→…→Monitoring) with gates | strategies | C | 4 | — | STR-001 | — | PLANNED | |
| STR-005 | Strategy templates (momentum, mean-rev, pairs, …) as examples | strategies | C | 3 | — | ENG-007 | — | PLANNED | Examples only, not engine code |

## BT — Backtesting
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| BT-001 | Backtest submission service | backtest | LI | 1 | `src/quantlab/backtest/service.py` | ENG-001, STR-002, AN-001 | `tests/unit/backtest/test_service.py` (fake docker) | TESTED | Synchronous; engine path tested only with fake docker; sample data only |
| BT-002 | Reproducibility manifest (code/data/engine/config/seeds) | backtest | C | 1 | `service.py` (`manifest.json`, `inputs_hash`, `DataBinding`) | BT-001, ENG-006, DATA-009 | `test_service.py` | TESTED | Data = sample or pinned dataset version + LEAN export hash; rerun refuses changed exports |
| BT-003 | Immutable backtest history & re-run diff | backtest | C | 1 | `backtest/models.py`, migration 0002, `BacktestService.rerun/compare` | BT-002 | `test_service.py` | TESTED | Reproducibility proven only against the deterministic fake engine; real LEAN determinism unverified |
| BT-004 | Event-driven simulation, multi-asset/strategy/timeframe | backtest | L | 1 | — | ENG-007 | — | PLANNED | |
| BT-005 | Fee, slippage, fill, brokerage, margin, buying-power models | backtest | L | 3 | — | ENG-009 | — | PLANNED | Configured via helper library |
| BT-006 | Partial fills, cancellation, market impact | backtest | L/C | 3 | — | BT-005 | — | PLANNED | Impact models custom |
| BT-007 | Settlement (cash accounts, T+1) | backtest | L | 3 | — | BT-005 | — | PLANNED | Verify LEAN settlement model coverage |
| BT-008 | Benchmark comparison | backtest | L | 3 | — | BT-004 | — | PLANNED | |
| BT-009 | Multi-strategy portfolio backtest (combined books) | backtest | C | 7 | — | BT-003 | — | PLANNED | LEAN runs one algorithm per process |

## AN — Performance analytics
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| AN-001 | Metrics library (return, CAGR, vol, Sharpe, Sortino, Calmar, MDD, DD duration, win rate, PF, expectancy, turnover, exposure, alpha/beta, costs) | analytics | C | 1 | `src/quantlab/analytics/metrics.py` | — | `tests/unit/analytics/test_metrics.py` | IN_PROGRESS | Done + TESTED: total return, CAGR, vol, Sharpe, Sortino, Calmar, MDD, DD duration, best/worst day, cross-check vs LEAN. Missing: trade metrics (needs fill data from order events), turnover, exposure, alpha/beta (needs benchmark), costs |
| AN-002 | Chart datasets (equity, drawdown, monthly/annual, trade dist., allocation, exposure, rolling Sharpe, benchmark) | analytics | C | 3 | — | AN-001 | — | PLANNED | |
| AN-003 | Downloadable report (HTML/PDF + config bundle) | analytics | C | 3 | — | AN-002 | — | PLANNED | |
| AN-004 | Strategy/run comparison | analytics | C | 4 | — | AN-001 | — | PLANNED | |

## OPT — Optimization & robustness
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| OPT-001 | Grid / random / parameter sweep | optimize | C | 4 | — | BT-001 | — | PLANNED | LEAN Optimizer launcher is an alternative; decide in Phase 4 |
| OPT-002 | Bayesian & multi-objective (Optuna) | optimize | C | 4 | — | OPT-001 | — | PLANNED | |
| OPT-003 | Walk-forward / rolling-window with IS/OOS labels | optimize | C | 4 | — | OPT-001 | — | PLANNED | |
| OPT-004 | Monte Carlo & bootstrap | optimize | C | 4 | — | AN-001 | — | PLANNED | |
| OPT-005 | Sensitivity/robustness heatmaps, overfitting metrics (e.g. deflated Sharpe, PBO) | optimize | C | 4 | — | OPT-001 | — | PLANNED | |
| OPT-006 | Custom objective functions | optimize | C | 4 | — | OPT-001 | — | PLANNED | |

## PORT — Portfolio management
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| PORT-001 | Accounts & books (theoretical vs broker-confirmed) | portfolio | C | 5 | — | OMS-003 | — | PLANNED | |
| PORT-002 | Positions, cash, realized/unrealized P&L from fills | portfolio | C | 5 | — | PORT-001 | — | PLANNED | |
| PORT-003 | Exposure: asset, sector, risk contribution | portfolio | C | 5 | — | PORT-002 | — | PLANNED | Sector data source needed |
| PORT-004 | Position sizing models (fixed qty/$, %equity, vol, ATR, risk budget, capped Kelly) | portfolio | C | 5 | — | PORT-002 | — | PLANNED | |
| PORT-005 | Multi-strategy capital allocation & rebalancing | portfolio | C | 7 | — | PORT-004 | — | PLANNED | |

## OMS — Order management
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| OMS-001 | Order model (market/limit/stop/stop-limit/trailing/bracket/OCO, TIF) | oms | C | 5 | — | CORE-004 | — | PLANNED | Bracket/OCO depend on broker |
| OMS-002 | Formal order state machine | oms | C | 5 | — | OMS-001 | — | PLANNED | |
| OMS-003 | Event-sourced order/fill store | oms | C | 5 | — | OMS-002 | — | PLANNED | |
| OMS-004 | Idempotency keys & duplicate prevention | oms | C | 5 | — | OMS-003 | — | PLANNED | |
| OMS-005 | Timeouts, expiry auto-cancel, SUBMIT_UNKNOWN resolution | oms | C | 5 | — | OMS-002 | — | PLANNED | |
| OMS-006 | Modify/cancel, reserved buying power | oms | C | 5 | — | OMS-002, RISK-001 | — | PLANNED | |
| OMS-007 | LEAN ↔ OMS gateway brokerage plugin | oms | LI | 5 | — | DEC-0007 approval | — | BLOCKED | Awaiting decision DEC-0007 |

## BRK — Brokerage
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| BRK-001 | BrokerAdapter protocol + contract test suite | brokers | C | 5 | — | OMS-001 | — | PLANNED | |
| BRK-002 | Simulated paper broker (local) | brokers | C | 5 | — | BRK-001 | — | PLANNED | |
| BRK-003 | IBKR adapter (TWS/IB Gateway): account, positions, funds, orders, executions, commissions | brokers | X | 5 | — | BRK-001 | — | PLANNED | Requires IBKR account; TWS/Gateway running; daily restarts |
| BRK-004 | Connection monitoring & automatic reconnection | brokers | C | 5 | — | BRK-003 | — | PLANNED | |
| BRK-005 | Broker reconciliation (positions, open orders, cash) | brokers | C | 5 | — | BRK-003, PORT-002 | — | PLANNED | |
| BRK-006 | Additional broker/exchange adapters | brokers | X | 7 | — | BRK-001 | — | PLANNED | |

## RISK — Risk management
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| RISK-001 | Central pre-trade risk gate (non-bypassable) | risk | C | 5 | — | OMS-002 | — | PLANNED | |
| RISK-002 | Rule set: daily loss, drawdown, position/order size, exposure, leverage, #positions, sector, frequency, volatility, liquidity, spread, session, cooldown | risk | C | 5 | — | RISK-001 | — | PLANNED | |
| RISK-003 | Risk decision log (reason, timestamp) | risk | C | 5 | — | RISK-001 | — | PLANNED | |
| RISK-004 | Kill switch (block new orders; configurable cancel/flatten) | risk | C | 5 | — | RISK-001 | — | PLANNED | |
| RISK-005 | Stop-loss / trailing-stop policy rules | risk | C | 5 | — | RISK-002 | — | PLANNED | |
| RISK-006 | Settlement policy (T+1, cash account free-riding guard) | risk | C | 5 | — | PORT-002 | — | PLANNED | |
| RISK-007 | Advanced risk analytics (VaR/ES, stress tests) | risk | C | 7 | — | PORT-003 | — | PLANNED | |

## LIVE — Paper/live deployment
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| LIVE-001 | Deployment supervisor (start/pause/stop/monitor) | live | LI | 5 | — | ENG-002, OMS-* | — | PLANNED | |
| LIVE-002 | Paper mode default | live | C | 5 | — | LIVE-001 | — | PLANNED | |
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
| UI-002 | Backtests list + run detail (charts, stats, orders) | web | C | 3 | `web/src/pages/Backtests.tsx` | BT-003, AN-002 | vitest + Playwright smoke | IN_PROGRESS | List/detail with metrics, cross-checks, engine stats, manifest done; equity chart and orders need AN-002/API additions |
| UI-003 | Algorithm editor (Monaco) + strategy library | web | C | 3 | — | STR-001 | — | PLANNED | |
| UI-004 | Market data browser & quality reports | web | C | 2 | `web/src/pages/Datasets.tsx`, `GET /api/datasets/{name}[/bars]` | DATA-005 | `tests/unit/api/test_app.py`, vitest, Playwright smoke | TESTED | Read-only (imports via CLI); close-price line chart only |
| UI-005 | Optimization views | web | C | 4 | — | OPT-* | — | PLANNED | |
| UI-006 | Portfolio, orders, risk, paper/live consoles, kill switch | web | C | 5 | — | PORT/OMS/RISK | — | PLANNED | |
| UI-007 | ML, AI assistant, logs/monitoring, settings, project mgmt views | web | C | 7–8 | — | — | — | PLANNED | |
| UI-008 | "Unavailable feature" indicator convention (no dead buttons) | web | C | 2 | `web/src/modules.ts`, `pages/ModulePage.tsx` | UI-001 | `modules.test.ts`, `components.test.tsx` | TESTED | Every module listed; planned ones show phase + feature IDs and contain no controls |

## OPS — Monitoring & reliability
| ID | Feature | Module | Appr. | Phase | Location | Depends | Tests | Status | Limitations |
|---|---|---|---|---|---|---|---|---|---|
| OPS-001 | Health/status endpoints (engine, broker, data feeds, strategies) | ops | C | 5 | — | CORE-009 | — | PLANNED | |
| OPS-002 | Alerts & notifications (desktop/email/webhook) | ops | C | 5 | — | OPS-001 | — | PLANNED | |
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

# QuantLab Architecture Specification

Status: **PROPOSED (Phase 0)** — awaiting owner approval. Decisions referenced as `DEC-NNNN` live in
[DECISION_LOG.md](DECISION_LOG.md). Feature IDs (`BT-003`, …) live in
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

---

## 1. Goals and constraints

| Goal | Consequence for architecture |
|---|---|
| Local-first, user-owned | All state on the user's disk under `QUANTLAB_HOME`; no mandatory cloud; every external service behind an optional adapter. |
| Windows 10/11 first, Linux/Docker portable | Python + .NET + Docker are all cross-platform; paths via `pathlib`; no bash-only scripts in the runtime path; WSL2 Docker supported but not required for research. |
| LEAN is the execution core | QuantLab is a **platform around LEAN**, not a re-implementation. Event-driven simulation, fills, fees, margin, corporate actions, indicators, algorithm framework all come from LEAN. |
| Financial safety | Paper by default; a single risk gate outside strategy code; broker-confirmed fills are the only source of truth for positions. |
| Reproducibility | Every backtest is an immutable record with a manifest (code hash, data version, engine image digest, config). |
| Long-lived maintainability | Modular monolith (one Python backend process + LEAN worker processes), typed interfaces, no premature microservices. |

### 1.1 Verified LEAN facts (2026-11-06)
- License Apache-2.0 (LEAN, Lean CLI, `Lean.Brokerages.InteractiveBrokers`). Commercial and private use permitted; keep NOTICE/attribution when redistributing builds.
- LEAN targets **`net10.0`**; Python support through `QuantConnect.pythonnet` with **Python 3.11** (foundation image uses Miniconda py311).
- Official Docker images (`quantconnect/lean`, `quantconnect/research`) bundle the .NET runtime, Python 3.11 and a large scientific Python stack.
- Lean CLI exists (PyPI `lean` 1.0.233) but several of its workflows (cloud sync, data purchases, some brokerage modules) assume a QuantConnect account. **QuantLab does not depend on it** (DEC-0003).
- Data shipped with LEAN is a small **sample** set (a few symbols). The QuantConnect Data Library is separately licensed and is **not** available locally without purchase.

## 2. System context

```
                         ┌────────────────────────── User's machine (QUANTLAB_HOME) ─────────────────────────┐
  Browser (React UI) ───►│  QuantLab Server (FastAPI, Python 3.11)                                          │
  CLI (`quantlab ...`) ─►│   ├─ API / WebSocket gateway  ── auth, local-only bind by default                  │
  Jupyter (research) ───►│   ├─ Application services: strategies, data, backtests, optimize, portfolio,      │
                         │   │   OMS, risk, live supervisor, ML registry, AI tool gateway, ops               │
                         │   ├─ Job runner (local process pool / queue)                                      │
                         │   └─ Storage: SQLite (metadata, WAL)  +  Parquet/DuckDB (market data, results)   │
                         │                                                                                    │
                         │  Engine workers (EngineAdapter)                                                    │
                         │   └─ LEAN (Docker image OR native .NET 10 build) ── reads LEAN-format data export  │
                         │                                                                                    │
                         │  Broker adapters ── IBKR (TWS / IB Gateway) ── paper first, live gated            │
                         └────────────────────────────────────────────────────────────────────────────────────┘
       Optional adapters (off by default): data vendors (Polygon, Tiingo, Alpha Vantage, Binance…), AI providers
       (Anthropic, OpenAI, Ollama-local), notification channels, remote access via reverse proxy + auth.
```

## 3. Core architectural decisions (summary)

1. **Modular monolith** in Python 3.11 (DEC-0002). Modules communicate through typed service interfaces and an in-process event bus; process boundaries only where required (LEAN workers, broker connections, Jupyter).
2. **EngineAdapter abstraction** (DEC-0004). QuantLab never calls LEAN internals directly from services; it calls `EngineAdapter.run_backtest(spec) -> RunHandle`. LEAN is the first and only planned implementation. Two LEAN launch modes: `docker` (default, reproducible, image digest pinned) and `native` (local .NET 10 build for Windows users without Docker).
3. **QuantLab owns the data catalog; LEAN consumes an export** (DEC-0005). Canonical data lives in Parquet partitions with a DuckDB catalog and content-hashed dataset versions. A deterministic exporter writes LEAN's zipped-CSV format into a per-version folder that is mounted read-only into the engine. Research and backtesting read the **same** dataset versions.
4. **Strategies are user files in Git** (DEC-0006). Each strategy is a directory (`main.py` or `Main.cs`, `config.json`, optional tests) under `strategies/` (its own Git repo or the user's chosen repo). Strategy version = Git commit SHA + content hash of the directory tree.
5. **Single pre-trade risk gate for live/paper** (DEC-0007, PROPOSED — needs approval). See §6.
6. **Broker-confirmed state only** (DEC-0008). Positions/cash/avg price change only on broker execution reports; theoretical (strategy) positions are tracked separately and reconciled.
7. **SQLite default, PostgreSQL optional** (DEC-0009) via SQLAlchemy 2 + Alembic migrations. Append-only tables for backtests, orders, fills, risk decisions, audit log.
8. **Paper default, live behind explicit safety gates** (DEC-0010).
9. **AI through a permissioned tool gateway** (DEC-0011); AI never holds trading or credential capabilities.

## 4. Repository layout (target)

```
Quantlab/
├── CLAUDE.md                     agent operating rules
├── docs/                         project memory (canonical)
├── .claude/{commands,agents}/    Claude Code workflow helpers
├── pyproject.toml / uv.lock      backend packaging + lockfile (Phase 1)
├── src/quantlab/
│   ├── core/          config (pydantic-settings), logging (structlog JSON), ids, clock, event bus, db session, errors
│   ├── migrations/    Alembic env + versions (top layer: imports every module's models, DEC-0013)
│   ├── engine/        EngineAdapter protocol; lean/ (config builder, docker+native launchers, result parser, version probe)
│   ├── data/          providers/ (adapter protocol + impls), catalog (DuckDB), storage (Parquet), validation,
│   │                  calendars, corporate_actions, versioning, lean_export/
│   ├── strategies/    registry, versioning, templates, lint/validation
│   ├── backtest/      service, run records, reproducibility manifest, comparison
│   ├── analytics/     metrics (independent of LEAN stats, cross-checked), tearsheets, report export
│   ├── optimize/      search (grid/random/Optuna), walk-forward, Monte Carlo, bootstrap, sensitivity
│   ├── portfolio/     accounts, books (theoretical vs confirmed), allocation, sizing models
│   ├── oms/           order model, state machine, idempotency, event store
│   ├── risk/          rule engine, limits config, decisions log, kill switch
│   ├── brokers/       BrokerAdapter protocol; paper/ (simulated), ibkr/ ; reconciliation
│   ├── live/          live/paper deployment supervisor, recovery, heartbeat
│   ├── ml/            feature store, labelling, CV splitters (purged/embargoed), model registry
│   ├── ai/            provider abstraction (anthropic/openai/ollama), context retrieval, tool gateway, audit
│   ├── ops/           health, metrics, alerts, backup/restore
│   ├── api/           FastAPI routers, WebSocket streams, auth
│   └── cli/           `quantlab` command (Typer)
├── web/                          React + TypeScript + Vite UI (Phase 2+)
├── strategies/                   example + user strategies (LEAN-compatible)
├── docker/                       compose files, pinned image digests
├── tools/                        developer utilities (env_check.py, …)
└── tests/{unit,integration,contract,e2e,regression}/
```

Runtime home (outside Git, default `%LOCALAPPDATA%\QuantLab` on Windows, `~/.local/share/quantlab` on Linux, override `QUANTLAB_HOME`):
```
QUANTLAB_HOME/
├── db/quantlab.sqlite
├── data/parquet/…        canonical market data partitions
├── data/lean/<data_version>/   LEAN-format exports (immutable)
├── runs/<run_id>/        backtest/optimization artifacts (results json, logs, manifest, report)
├── models/               ML model artifacts
├── logs/
└── backups/
```

## 5. Module design

### 5.1 Engine (ENG-*)
```python
# As implemented in src/quantlab/engine/base.py (CHANGE-0006)
class EngineAdapter(Protocol):
    name: str
    def probe(self) -> EngineInfo: ...                    # engine build: image digest/id, runtime version
    def start_backtest(self, spec: BacktestSpec) -> RunHandle: ...   # prepares run dir, starts, returns
    def wait(self, handle: RunHandle, timeout_s: float | None = None) -> RunOutcome: ...
    def cancel(self, handle: RunHandle) -> None: ...
    def collect(self, handle: RunHandle) -> EngineResult: ...        # parse engine-native results
```
Optimization trials are ordinary backtests submitted by the optimizer through BacktestService (no separate
engine call). Live/paper execution is deliberately **not** an engine method: it arrives in Phase 5 through the
OMS + risk gate (DEC-0007).

Docker run layout (ENG-002): strategy dir → `/LeanCLI` (ro), dataset export → `/Lean/Data` (ro; omitted = image's
bundled sample data), `runs/<id>/results` → `/Results`, `runs/<id>/storage` → `/Storage`, generated config →
`/Lean/Launcher/bin/Debug/config.json` (ro), `--network none` by default, image pinned by digest.
LEAN adapter responsibilities: generate LEAN `config.json` (algorithm location/language, data folder, parameters, brokerage/fee/slippage models are set in algorithm code or via QuantLab helper library), launch worker, stream logs, collect `*-summary.json`/`*.json` result files, record engine version.
QuantLab ships a small **`quantlab_lean` helper library** (Python + C#) that strategies may import to apply platform-configured fee/slippage/brokerage models and to emit structured telemetry — so models are configured once and are not re-implemented.

### 5.2 Market data (DATA-*)
As implemented (CHANGE-0009, `src/quantlab/data/`): `DataProvider` protocol + `CsvFileProvider`; `validate_bars`
(errors quarantine the batch, warnings are recorded); `DataCatalog` publishes immutable, content-addressed versions
(copy-on-write merge with the latest; conflicting revisions rejected unless `on_conflict="replace"`, which is recorded);
DuckDB reads Parquet; exports refuse licence-restricted data without explicit acknowledgement. See DEC-0015.
Original design notes:
- `DataProvider` protocol: `capabilities()`, `fetch_history(request) -> RawBatch`, `stream(subscription)`; each provider declares license terms + redistribution flag.
- Pipeline: fetch → normalize (UTC timestamps + exchange tz metadata, canonical symbol/`SecurityIdentifier` mapping) → validate (duplicates, gaps vs trading calendar, OHLC sanity, outliers, stale prices) → store raw + adjusted factors separately → **dataset version** (content hash of partitions + manifest) → optional LEAN export.
- Every dataset carries `provenance` ∈ {`real_historical`, `live_recorded`, `synthetic`, `sample`} and `completeness` report. Reports refuse to label `synthetic`/`incomplete` data as real.
- Point-in-time: raw prices + factor files (split/dividend) stored, adjustments applied at read time (LEAN map/factor-file convention), so no look-ahead from back-adjusted series. Delisted symbols kept; universe membership stored as time-ranged records.
- Calendars: `exchange_calendars` for sessions/holidays (cross-checked with LEAN market-hours database for backtests).

### 5.3 Backtesting (BT-*)
`BacktestService.submit(strategy_ref, config) -> run_id`. Steps: resolve strategy version → resolve/lock dataset version → build manifest → EngineAdapter run → parse → compute QuantLab analytics → persist immutable `backtest_run` row + artifacts. Re-running a manifest creates a **new** run and a reproducibility diff against the original.

As implemented (CHANGE-0007, `src/quantlab/backtest/service.py`): `BacktestService.run(name, parameters)` (synchronous until
CORE-007) copies the strategy's files into `runs/<id>/algorithm/` and hashes **the copy** (DEC-0014); the engine runs the
copy. `inputs_hash` = sha256 of {strategy tree hash, entry file, language, parameters, data provenance/version, engine
identity (image + image id), QuantLab version}; `result_hash` = sha256 of {status, statistics, orders, equity}.
`rerun(id)` re-executes the stored snapshot with the original parameters, refuses if the engine identity changed, and
returns a `ReproducibilityReport` (inputs identical? results identical? differences).

### 5.4 Optimization (OPT-*)
Optimizer orchestrates many backtest trials via the same BacktestService (no second simulator). Grid/random built-in, Bayesian via Optuna, walk-forward windows defined explicitly with IS/OOS labels persisted per trial. Monte Carlo/bootstrap operate on trade lists and return series from stored runs.

### 5.5 Portfolio, OMS, risk, brokers (PORT-*, OMS-*, RISK-*, BRK-*)

Order state machine (OMS-002):
```
NEW ─risk_check─► RISK_REJECTED (terminal)
 │
 └─► PENDING_SUBMIT ─► SUBMITTED ─► ACCEPTED ─► PARTIALLY_FILLED ─► FILLED (terminal)
          │                 │            │              │
          │                 └► REJECTED  ├► PENDING_CANCEL ─► CANCELLED (terminal)
          └► SUBMIT_UNKNOWN (timeout/disconnect: must be resolved by reconciliation, never auto-resubmitted)
                                         └► EXPIRED (terminal)
```
- Every order: internal `order_id` (ULID) + `client_order_key` (idempotency; deterministic from strategy run, signal id) → duplicate keys rejected at the DB unique constraint.
- Event-sourced `order_events` table; current state is a projection.

### 5.6 ML (ML-*), AI (AI-*), UI (UI-*), Ops (OPS-*), Security (SEC-*)
See FEATURE_MATRIX for scope. Key structural points: ML models deploy into strategies as versioned artifacts loaded read-only by the strategy; AI tool gateway exposes a whitelisted, audited tool set with a `capability` per tool and no trading/credential capabilities at all.

## 6. Live/paper order path and the risk gate (DEC-0007 — decision required)

The requirement "strategies must never bypass centralized risk controls" conflicts with LEAN's normal live mode, where the algorithm process talks to the brokerage directly. Options:

| Option | How | Pros | Cons |
|---|---|---|---|
| **A. QuantLab Gateway brokerage (recommended)** | Custom LEAN `IBrokerage` plugin ("QuantLabBrokerage") forwards every LEAN order to the QuantLab OMS over a local socket; OMS runs risk gate → routes to `BrokerAdapter` (IBKR via `ib_async`/TWS API); fills flow back to LEAN. | One OMS, one risk gate, one audit log for all engines and manual orders; kill switch is outside the strategy process; broker-agnostic. | Must implement & test the LEAN brokerage plugin and fill/event round-trip; extra latency (ms, fine for non-HFT). |
| B. LEAN native IB brokerage + C# risk decorator | Wrap LEAN's IB brokerage with a pre-trade check. | Less new code. | Risk logic lives inside the strategy's process; per-engine duplication; harder kill switch; positions reconciliation split. |
| C. LEAN native brokerage, post-hoc monitoring only | — | Trivial. | Does **not** meet the requirement. Rejected. |

Until approved, Phase 5 work starts with the simulated paper broker behind the OMS, which is needed in both A and B.

## 7. Module dependency map

```
core  ◄────────────────────────────── every module
data ──► core
engine ──► core, data(lean_export)
strategies ──► core
backtest ──► engine, data, strategies, analytics
analytics ──► core (pure functions on returns/trades)
optimize ──► backtest, analytics
portfolio ──► core, oms(events)
risk ──► core, portfolio(read), data(read: prices/spreads)
oms ──► core, risk, brokers
brokers ──► core (adapters have no dependency on oms; they emit events)
live ──► engine, oms, brokers, risk, portfolio
ml ──► data, core ; strategies consume ml artifacts
ai ──► core + read-only services (strategies, backtest, analytics, docs) — NEVER oms/brokers/risk-write
ops ──► core, read access to all status
api/cli ──► bootstrap (AppContext: single composition root) ──► services (thin); cli may import api (serve)
web ──► api only
```
Rule: no dependency cycles; `brokers` never imports `oms`; `ai` cannot import write interfaces of `oms`, `risk`, `brokers`, `live`, or the secrets store (enforced by an import-linter contract in CI, Phase 1).

## 8. Technology stack

| Layer | Choice | Rationale |
|---|---|---|
| Engine | QuantConnect LEAN (Docker image pinned by digest; native .NET 10 build optional) | Mature event-driven engine, Apache-2.0, Python + C#, IB brokerage. |
| Backend | Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, Typer CLI, structlog | Same Python as LEAN; strong typing; async API + WebSockets. |
| Packaging | `uv` with `uv.lock` | Fast, reproducible lockfiles, works on Windows. |
| Metadata DB | SQLite (WAL) default; PostgreSQL optional | Zero-install local; upgrade path for server deployments. |
| Market data | Parquet (pyarrow) + DuckDB; Polars/pandas | Columnar, fast analytical queries, open formats (no lock-in). |
| Calendars | `exchange_calendars` + LEAN market-hours DB | Session/holiday correctness. |
| Research | JupyterLab (local), optional `quantconnect/research` image | Shares the QuantLab data catalog & analytics package. |
| Optimization | Built-in grid/random, Optuna (Bayesian/TPE, multi-objective) | Proven, local. |
| ML | scikit-learn, LightGBM, XGBoost, PyTorch (optional extras) | Standard; TF optional. |
| Broker | IBKR TWS/IB Gateway via `ib_async` (QuantLab side) and/or LEAN IB brokerage | IBKR first per charter. |
| UI | React + TypeScript + Vite, TanStack Query, Monaco editor, TradingView Lightweight Charts (Apache-2.0) + ECharts, dockview/golden-layout style panels | Professional workstation feel, all self-hostable. |
| AI | Provider interface; Anthropic SDK, OpenAI SDK, Ollama HTTP | Provider independence; local option. |
| Secrets | `keyring` (Windows Credential Manager / Secret Service), env vars fallback | Never in files or logs. |
| Testing | pytest, hypothesis, import-linter, Playwright (UI e2e), ruff, mypy/pyright | Quality gates. |
| Deployment | Local process (Windows service via NSSM/WinSW optional), Docker Compose for Linux/server | Local-first, portable. |

## 9. Cross-cutting concerns
- **IDs**: ULIDs for runs/orders/events; human IDs for features/changes/decisions.
- **Time**: all storage in UTC; exchange tz kept as metadata; a `Clock` abstraction for tests.
- **Logging**: JSON structured logs with redaction filter for secret-like fields; correlation ids per run/order.
- **Config**: layered — defaults → `quantlab.toml` in QUANTLAB_HOME → env vars (`QUANTLAB_*`) → per-run overrides; secrets only via keyring/env references.
- **Security**: API binds `127.0.0.1` by default, token auth always on; remote access only via explicit config + TLS reverse proxy.
- **Audit**: append-only `audit_log` for sensitive operations (mode switches, risk limit changes, kill switch, credential changes, AI tool calls).

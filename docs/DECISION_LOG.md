# Decision Log

States: PROPOSED (needs owner approval) · ACCEPTED · SUPERSEDED (by DEC-x) · REJECTED.
Never edit an ACCEPTED decision in place; supersede it with a new entry.

### DEC-0001 — Repository documentation is the canonical project memory
State: ACCEPTED (mandated by master prompt) · 2026-11-06
Context: AI sessions lose context. Decision: CLAUDE.md + docs/* are authoritative; feature status lives only in
IMPLEMENTATION_STATUS.md. Consequence: every session starts/ends with the documented procedure.

### DEC-0002 — Modular monolith in Python 3.11
State: ACCEPTED (owner approval 2026-11-06) · 2026-11-06
Decision: one backend process (FastAPI) with internal modules + separate LEAN worker processes; Python pinned to
3.11 to match LEAN's embedded pythonnet interpreter so strategy code, research and platform share one runtime.
Alternatives: microservices (rejected: operational overhead for a single-user platform); Python 3.13 (rejected:
LEAN algorithms run on 3.11, shared libraries must be 3.11-compatible).

### DEC-0003 — No dependency on Lean CLI / QuantConnect account
State: ACCEPTED (owner approval 2026-11-06) · 2026-11-06
Decision: QuantLab launches LEAN directly (Docker image or native build) with its own generated config. Lean CLI may
be used manually but is never required. Reason: CLI workflows assume `lean login`; charter forbids mandatory QC cloud.

### DEC-0004 — EngineAdapter abstraction; LEAN Docker launcher default, native optional
State: ACCEPTED (owner approval 2026-11-06) · 2026-11-06
Decision: services depend on `EngineAdapter`. Default launcher runs `quantconnect/lean` pinned **by digest**
(reproducibility). Native .NET 10 launcher for Windows users without Docker (Phase 3).
Risk: Docker Desktop licensing (free for personal/small business) and WSL2 requirement on Windows.

### DEC-0005 — QuantLab-owned data catalog (Parquet + DuckDB) with deterministic LEAN export
State: ACCEPTED (owner approval 2026-11-06) · 2026-11-06
Decision: canonical data in Parquet; immutable content-hashed dataset versions; LEAN reads a per-version export.
Research and backtests read the same versions. Raw prices + factor files stored (point-in-time).
Alternative: store only LEAN format (rejected: poor analytics, no versioning, LEAN-format lock-in).

### DEC-0006 — Strategies are user-owned Git directories
State: ACCEPTED (owner approval 2026-11-06) · 2026-11-06
Decision: strategy = directory with LEAN entry point + metadata; version = Git SHA + tree hash; platform code never
contains strategy logic.

### DEC-0007 — Live/paper orders flow through QuantLab OMS + risk gate (gateway brokerage)
State: ACCEPTED — Option A (owner approved architecture incl. recommended option, 2026-11-06; may be revisited before Phase 5) · 2026-11-06
Options and trade-offs: ARCHITECTURE.md §6. Recommendation: Option A (custom LEAN brokerage plugin forwarding to
QuantLab OMS).

### DEC-0008 — Broker-confirmed fills are the only source of position truth
State: ACCEPTED (owner approval 2026-11-06) · 2026-11-06
Decision: confirmed book updated only from execution reports; theoretical book separate; reconciliation on start,
reconnect and periodically. Orders in SUBMIT_UNKNOWN are never auto-resubmitted.

### DEC-0009 — SQLite (WAL) default metadata store, PostgreSQL optional
State: ACCEPTED (owner approval 2026-11-06) · 2026-11-06
Decision: SQLAlchemy 2 + Alembic; append-only tables for runs, orders, fills, risk decisions, audit.

### DEC-0010 — Paper trading default; live behind gates
State: ACCEPTED (mandated by master prompt) · 2026-11-06
Decision: live requires explicit config, separate credentials, risk limits, broker verification, typed user
confirmation, persistent visible indicator. AI cannot enable it.

### DEC-0011 — AI via provider abstraction and permissioned tool gateway
State: ACCEPTED (owner approval 2026-11-06) · 2026-11-06
Decision: AI tools are allowlisted read/propose capabilities; no trading, credential, risk-write or deletion tools;
enforced by import contracts and gateway allowlist; every call audited.

### DEC-0012 — Backend Python lockfiles via `uv`
State: ACCEPTED (owner approval 2026-11-06) · 2026-11-06
Decision: `pyproject.toml` + `uv.lock`; UI via `pnpm-lock.yaml`.

### DEC-0013 — Alembic migrations live in a top-level `quantlab.migrations` package
State: ACCEPTED (implementation decision within approved DEC-0002/DEC-0009; owner may revisit) · 2026-11-06
Context: `migrations/env.py` must import every module's models, but `core` may not import other modules.
Decision: move `src/quantlab/core/migrations` → `src/quantlab/migrations` (top layer in the import-linter layers contract);
`core.db` resolves it by package name at runtime. Revision ids unchanged (0001), so existing databases are unaffected.

### DEC-0014 — Backtests execute a per-run copy of the strategy code
State: ACCEPTED (implementation of DEC-0006 / BT-002; owner may revisit) · 2026-11-06
Decision: at submission the strategy directory is copied to `runs/<id>/algorithm/` and the version is the tree hash of the
copy; the engine mounts the copy read-only. Re-runs use the stored copy. Consequence: editing a strategy can never change
what a recorded run executed, at the cost of a few KB–MB per run (strategy dirs capped at 50 MB).

### DEC-0015 — Market data storage: raw prices, immutable content-addressed versions, quarantine on error
State: ACCEPTED (implementation of DEC-0005; owner may revisit) · 2026-11-06
Decisions: (1) datasets declare provenance and price adjustment once (raw preferred; adjustments applied at read time
in DATA-008, avoiding look-ahead); (2) version identity = hash of logical rows, not Parquet bytes; (3) each import is a
copy-on-write merge producing a new immutable version (old versions remain readable for reproducibility);
(4) validation errors reject + quarantine the whole batch, never partial imports; (5) conflicting revisions of existing
rows are rejected unless the user chooses `replace`, which is recorded on the version; (6) SQLite is the catalog of
record, DuckDB the query engine. Trade-off: full rewrite per import is costly for large intraday datasets (KI-0024).

### DEC-0016 — Dataset runs: LEAN export overlaid inside the container; sample prices removed; RAW normalization
State: ACCEPTED (implementation of DEC-0005/BT-002; owner may revisit) · 2026-11-06
Context: LEAN reads one data folder that must also contain its databases (market hours, symbol properties) shipped in
the image. Mounting an export over `/Lean/Data` would hide them; adding files next to the sample data would let a run
silently read sample prices for symbols/resolutions the dataset lacks.
Decision: mount the export read-only at `/QuantLab/dataset`; the container entrypoint script deletes every price folder
(tick/second/minute/hour/daily) and all map/factor files under `/Lean/Data/*/*/`, copies the export in, then starts LEAN.
Exports carry neutral map/factor files; strategies use `DataNormalizationMode.RAW` until corporate actions (DATA-008).
Consequence: per-run copy cost (KI-0030); behaviour verified with a real shell against a fake tree, not yet with LEAN.

### DEC-0017 — Research reads only the versioned catalog; JupyterLab is an optional extra
State: ACCEPTED (implementation of RES-001/002; owner may revisit) · 2026-11-07
Decision: notebooks use `quantlab.research.Research`, a read-only facade over DataCatalog (same dataset versions as
backtests), analytics (same metric code as backtests) and backtest history; frames carry provenance in `attrs` and non-real
data emits `NonRealDataWarning`. JupyterLab/matplotlib live in the `research` optional dependency group so the base install
and CI stay light; `quantlab research` binds 127.0.0.1 and leaves Jupyter's token auth on.

### DEC-0018 — Web UI served same-origin by `quantlab serve`; token via URL fragment; strict CSP
State: ACCEPTED (implementation of UI-001; owner may revisit) · 2026-11-07
Decision: React 19 + TypeScript + Vite app in `web/`, built to `web/dist` (not committed) and served by the API process
(no CORS, one port). `quantlab serve` prints `http://127.0.0.1:<port>/#token=<token>`; fragments are never sent to the
server, so tokens stay out of access logs; the UI moves the token to sessionStorage and removes it from the URL, and calls
the API with `Authorization: Bearer`. Responses carry CSP `default-src 'self'` (no inline script/style), `frame-ancestors
'none'`, `nosniff`, `no-referrer`. TradingView Lightweight Charts' attribution logo injects inline CSS, so it is disabled
and the NOTICE attribution + link is rendered as text under each chart. Toolchain pinned to established majors
(Vite 7, TypeScript 5.9, Vitest 4.0); component tests render statically (no jsdom) — see KI-0033.

### DEC-0019 — Corporate actions live inside dataset versions; one factor computation for LEAN and research
State: ACCEPTED (implementation of DEC-0015/DATA-008; owner may revisit) · 2026-11-07
Decisions: (1) splits and cash dividends are stored with each version (immutable, hashed), merged across imports with the
same reject/replace conflict policy as prices; (2) only raw-price datasets may carry actions (otherwise adjustments would
be applied twice); (3) `factor_file_rows` implements LEAN's factor semantics and is used both for LEAN factor files and for
read-time adjustment (`raw`/`split`/`split_dividend`, back-adjusted to the version's latest event; research use — LEAN
applies factor files point-in-time in backtests); (4) adding actions changes the version hash, datasets without actions
keep their previous hashes.

### DEC-0020 — Tiingo EOD as the first network data provider
State: ACCEPTED (owner chose Tiingo, 2026-11-07)
Decision: `TiingoProvider` behind the DataProvider protocol: raw OHLCV + `divCash`/`splitFactor` as corporate actions;
`Authorization: Token` header (keys never in URLs/logs); API key from `secret://quantlab/tiingo/api-key` (OS keyring via
`quantlab secret set`, env fallback); polite pacing + 429 retry; Tiingo's personal-use licence enforced (datasets marked
redistributable are refused); no real Tiingo data in the repository. Cloud development uses a personal environment
"Quantlab" that allows the Tiingo hosts, with the key held by the environment proxy.

### DEC-0021 — Market-data migration Tiingo → IBKR by configuration; orders stay behind OMS + risk gate
State: ACCEPTED (owner requirements 2026-11-07; slicing is an implementation decision the owner may revisit)
Decisions: (1) `MarketDataProvider` = historical (`fetch_bars`, existing DataProvider) + real-time (`RealtimeProvider`:
snapshot/stream/status) with an explicit `DataState` on every quote; (2) providers are chosen in `[market_data]`
(historical: tiingo|ibkr, realtime: mock|ibkr, require_live) and built by `data.factory`; strategies never see the
provider; (3) historical data from any provider enters only via the versioned catalog (local cache), so backtests stay
independent of live feeds and a migration cannot overwrite existing versions, strategies or modules; (4) `QuoteGuard`
refuses non-LIVE data when live data is required — delayed data is never substituted silently; (5) IBKR account,
positions, executions and order submit/modify/cancel are a **BrokerAdapter** behind the OMS and the central risk gate
(DEC-0007), not part of the market-data provider; paper trading first (DEC-0010). Delivery: slice A (interface, Tiingo
intraday, mock streaming, config switch, comparison) → slice B (IBKR market data) → slice C (Phase 5: IBKR broker adapter,
OMS, risk gate, paper trading).

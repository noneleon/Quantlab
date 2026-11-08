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

### DEC-0022 — Phase 3 delivery order; normalised results file; report contents
State: ACCEPTED (owner approved Phase 3, 2026-11-07; slicing is an implementation decision the owner may revisit)
Decisions: (1) Phase 3 is delivered in slices: **3a** analytics depth that can be verified without a LEAN daemon (AN-002,
BT-008, AN-003, UI-002); **3b** `quantlab_lean` helper library + cost/fill/settlement model configuration (ENG-009,
BT-005..007) and strategy templates (STR-005); **3c** golden-result regression suite on real LEAN runs, native
launcher (ENG-003), C# (ENG-008), editor (UI-003). 3b/3c need real LEAN runs on the owner's machine to be VERIFIED.
(2) Every successful run writes `results.json` (normalised equity + orders) next to the engine output, so analytics
never re-parse engine formats (engine adapters stay the only parsers). (3) Reports are derived artifacts under
`<run>/reports/`; the run record stays immutable. Bundles contain report, chart data, metrics, manifest, results and
the strategy snapshot, **never market data** (vendor licences, SECURITY_POLICY). The API renders reports on request
and writes nothing. (4) Benchmarks are catalog datasets read split+dividend adjusted (total return).

### DEC-0023 — Execution models via an opt-in in-container helper; recorded and confirmed per run
State: ACCEPTED (owner approved Phase 3b, 2026-11-07)
Decisions: (1) Fee, slippage, fill, brokerage/account-type and settlement models are configured in QuantLab
(`[backtest.models]`, per-run `-m key=value`) and applied inside LEAN by the `quantlab_lean` helper, which the strategy
calls explicitly (`quantlab_lean.configure(self)`) — QuantLab never rewrites strategy code. (2) The helper and a
`models.json` are mounted read-only and added to LEAN's `python-additional-paths`; the strategy snapshot is not changed,
so its tree hash is unaffected. (3) The resolved models are part of the run's inputs hash and manifest; reruns reuse the
recorded models (runs recorded before this decision rerun with LEAN defaults). (4) The helper logs a marker with the
models hash; QuantLab records `models_applied` and warns everywhere (CLI, charts, report) when a strategy did not apply
the configured models. (5) Defaults ("brokerage") keep LEAN's own models, so configuring nothing changes nothing.

### DEC-0024 — No QuantConnect subscription; editor writes via the local API; golden suite
State: ACCEPTED (owner 2026-11-07: "我不想使用clean的订阅 所以需要订阅的功能我希望你能够自己实现" — interpreted as the
QuantConnect/LEAN subscription; reaffirms DEC-0003)
Decisions: (1) QuantLab never requires a QuantConnect account, `lean login`, cloud nodes or a paid plan; every
subscription-only QuantConnect capability QuantLab offers is implemented locally (map in FEATURE_MATRIX
"QuantConnect subscription features"). Only the open-source LEAN engine (Apache-2.0 Docker image / source) is used.
(2) The local API gains its first write endpoints, limited to strategy source files (create from template, save file):
same bearer token, loopback-only and Host allow-list as the rest, no cookies (no CSRF surface); path-safe,
conflict-checked (sha256), size-limited, audited. Backtests/trading/risk/broker endpoints stay read-only.
(3) The editor is CodeMirror 6 (MIT), not Monaco: smaller, bundles without a CDN, and mounted in a shadow root so its
styles use constructable stylesheets — the CSP stays `style-src 'self'` with no nonce or 'unsafe-inline'.
(4) Golden-result regression cases are rerun from stored snapshots and compared by result hash (same engine) or
metric tolerance (allowed engine change); cases are immutable files.

### DEC-0025 — Optimization: trials are backtests; selection bias always reported; Phase 4 slices
State: ACCEPTED (owner approved Phase 4, 2026-11-07; slicing is an implementation decision the owner may revisit)
Decisions: (1) `quantlab.optimize` sits between `migrations` and `backtest` in the layer contract and submits every
trial through `BacktestService` (no second simulator; each trial is an immutable, reproducible run). LEAN's Optimizer
launcher is not used: it would bypass QuantLab's run records and data binding. (2) The first trial pins the dataset
version for the whole study. (3) Every sweep summary states the number of trials and includes the Deflated Sharpe
Ratio of the selected trial, PBO (CSCV) and parameter sensitivity; walk-forward keeps IS/OOS runs labelled and builds
its equity curve from OOS runs only. (4) Objectives/constraints are an allow-listed arithmetic language over metrics,
never `eval`. (5) Walk-forward windows are passed to strategies through declared `start`/`end` parameters (names
configurable). (6) Studies are started from the CLI; the API exposes them read-only. Slices: 4a (this) engine-free
optimisation + statistics; 4b UI-005 views + Optuna (OPT-002) + AN-004; 4c STR-004 lifecycle + RES-006 promotion.

### DEC-0026 — Strategy lifecycle: evidence-gated, version-bound, append-only; no path to live from here
State: ACCEPTED (owner approved Phase 4c, 2026-11-07)
Decisions: (1) Stages research → backtested → validated → paper → live_candidate, plus retired; demotion to
research and retirement allowed from anywhere with a reason. (2) Transitions are CLI-only (the API is read-only for
lifecycle), recorded in an append-only table (DB triggers) and the audit log, each with the tree hash of the
strategy files and the gate checks + thresholds applied. (3) Gates check evidence produced by QuantLab: backtested =
succeeded run(s) on real data of the current files; validated = walk-forward of the current files on real data with
≥ N OOS windows, OOS Sharpe and drawdown within `[lifecycle]` limits, plus a sweep with deflated Sharpe probability and
PBO within limits; paper = validation still matches the current files (approval only until Phase 5). (4)
live_candidate requires paper-trading records and is therefore refused until Phase 5 exists; it never enables live
trading (LIVE-003 + human activation, CLAUDE.md §4). (5) Synthetic/sample data never satisfies a gate unless the owner
explicitly sets `lifecycle.require_real_data = false` (used only in tests). (6) Notebook promotion attaches the
notebook with outputs stripped (vendor data must not land in a possibly-committed strategy folder).

### DEC-0027 — Phase 5 order path: OMS is the only broker caller; risk gate fails closed; paper only
State: ACCEPTED (owner approved Phase 5, 2026-11-07; implements DEC-0007/0008/0010)
Decisions: (1) `OMS.submit` is the only code that calls `BrokerAdapter.submit`; an import-linter contract forbids
platform modules (backtest, optimize, lifecycle, research, strategies, analytics, engine, data) from importing
oms/brokers/risk/portfolio. (2) Every order gets a stored risk decision (each check + limits-file sha256) before
anything is sent; `PENDING_SUBMIT` is committed before the broker call. (3) The gate fails closed: no/invalid limits
file, no quote, unknown equity or start-of-day equity → rejected; limits have no implicit defaults. (4) Kill switch =
append-only events; engaging needs a reason, disengaging the typed phrase "DISENGAGE KILL SWITCH"; both audited.
(5) Unknown submission outcomes (timeouts, unexpected adapter errors, crashes after PENDING_SUBMIT) become
SUBMIT_UNKNOWN and are resolved only from broker facts by reconciliation (new terminal state NOT_PLACED when the broker
never had the order); nothing is resubmitted. (6) Positions/cash only from fills; reconciliation reports, never
rewrites. (7) Paper only: the OMS refuses non-paper brokers; order and kill-switch commands are CLI-only (API
read-only). (8) Slices: 5a (this) local order path; 5b web console (UI-006), IBKR paper adapter (BRK-003/004,
migration slice C), deployment supervisor (LIVE-001); OMS-007 (LEAN gateway brokerage, C#) needs .NET (KI-0055).
Addendum (CHANGE-0027): broker events are acknowledged by sequence number (`acknowledge(through_seq)`), never by
count; reconciliation reads one `BrokerSnapshot`; adapters must serialise state changes with an OS-level lock (or the
broker's own server-side state), never an age-broken lock file; an order the broker works but the OMS does not track as
working blocks new orders.
Addendum (CHANGE-0028): new limits are added as required keys (old files fail closed; `quantlab risk template`
shows the current set); the kill switch cancels working orders by default but never flattens positions; cooldowns
and loss limits always let position-reducing orders through.

### DEC-0028 — Web console: read-only trading views; engaging the kill switch is the only trading write
State: ACCEPTED (owner approved Phase 5b, 2026-11-07; refines DEC-0027 point 7)
Decisions: (1) The web console (UI-006) shows portfolio, orders (state history, every risk check, fills with their
price data state) and risk (limits, decisions, kill switch) read-only. (2) `POST /api/risk/kill-switch/engage` is the
only trading write in the API, because engaging can only reduce risk: it needs the session token, a reason and the
typed phrase "ENGAGE KILL SWITCH", is committed before working orders are cancelled, records the channel
("web console") in the audit log, and the response lists anything that may still be working. (3) Disengaging, placing
or cancelling individual orders and changing limits stay CLI-only (CLAUDE.md §4); there is no API route for them and
a test asserts it. (4) Cancelling orders never depends on market data: the paper OMS builds its quote provider on
first use.

### DEC-0029 — IBKR paper broker adapter: paper accounts only, verified on every connection
State: ACCEPTED (owner approved Phase 5b, 2026-11-07; implements DEC-0010 for paper)
Decisions: (1) `IBKRPaperBroker` connects only to the configured paper port; the configured live port and IBKR's
standard live ports (7496, 4001) are refused before connecting. (2) After every (re)connect all managed accounts must
be IBKR paper accounts (ids `DU…`); otherwise the adapter disconnects and refuses before anything is sent. The OMS
account (`ibkr-<DU id>`) and the paper account id are explicit configuration (`paper.ibkr_account`,
`paper.ibkr_starting_cash`); orders always name that account and carry the QuantLab order id as `orderRef`.
(3) The broker connection uses its own client id (`brokers.ibkr.client_id`, readonly=False), separate from the
read-only market-data connection. (4) Orders without a QuantLab reference (placed by hand) count as untracked broker
orders and block new orders. (5) Failures after handing an order to IBKR, and missing acknowledgements, are
SUBMIT_UNKNOWN (reconcile), never "rejected". (6) Live trading stays unavailable (LIVE-003); this adapter is not a
step towards it without a new decision.

### DEC-0030 — Paper trading supervisor: keeps the account in step, never trades
State: ACCEPTED (owner approved Phase 5b, 2026-11-07)
Decisions: (1) `quantlab paper supervise` runs one supervisor per OMS account (OS-level lock): broker events every
tick, reconciliation and equity samples on a schedule, start-of-day equity at the session open, DAY-order expiry
after the close for brokers that do not expire orders themselves. (2) It never submits or modifies orders and never
disengages the kill switch. (3) It engages the kill switch (risk-reducing, actor "supervisor") when reconciliation
finds a position difference; configurable via `[supervisor] engage_kill_switch_on_mismatch`, on by default.
(4) Equity samples are append-only and only stored when every held symbol is marked; the drawdown high-water mark is
the highest of the start-of-day rows and the samples. (5) Errors never stop the loop; they are kept in the status file
and a stale heartbeat (more than three intervals) shows the supervisor as not running in the CLI, API and console.
Strategy deployments (start/pause/stop of algorithms) need the LEAN gateway (OMS-007) and are not part of this.

### DEC-0031 — LEAN gateway protocol and deployment scope (OMS-007)
State: ACCEPTED (owner chose "finish Phase 5" = OMS-007 on 2026-11-07; implements DEC-0007 option A)
Decisions: (1) A LEAN algorithm reaches QuantLab only through `quantlab paper gateway --deployment <name>`: newline
JSON over TCP on a loopback address, one plugin connection at a time, authenticated by a random per-start token in an
owner-only run file (`QUANTLAB_HOME/run/gateway-<name>.json`, deleted on stop). (2) Every `place` is an `OMS.submit`
(risk gate, kill switch, stored decision); orders are keyed `lean:<deployment>:<run>:<LEAN id>` with a run id per
algorithm start (LEAN restarts its ids at 1), so reconnect replays are idempotent and restarts never collide; a replay
with different parameters is refused. `lean:` keys are reserved for the gateway. (3) A connection sees, cancels and
replays only its run's orders; its positions are its deployment's own (from its fills) and a sell may not exceed
them, so strategies never sell manual or other deployments' positions. Cash is the shared paper account's. (4) The
gateway cannot touch the kill switch, limits, credentials or mode; malformed input is answered with an error and never
ends the gateway. (5) Orders of an earlier run that are still working stay under the risk gate and the supervisor,
but the new run does not manage them (cancel by hand or with the kill switch).
Amendment (CHANGE-0035): run ids are issued by the gateway; earlier runs' unfinished orders block new orders until
cancelled (`cancel_orphans`) or resolved; refusals are audited and stay refused for the run.

### DEC-0032 — Native LEAN launcher; metric definitions aligned with LEAN after the first real engine run
State: ACCEPTED (owner chose "finish Phase 5" incl. 5c-3, 2026-11-07)
Decisions: (1) `[engine.lean] launcher = "native"` runs LEAN 2.5.18229 from QuantConnect's NuGet packages on the
local .NET 10 runtime (`lean/QuantLabLauncher`, installed by `quantlab engine native-install` together with LEAN's
databases and sample data from the matching repository tag 18229; versions and checksums are recorded). C# strategies
are compiled per run against the same LEAN version. Python algorithms stay on the Docker launcher until pythonnet is
set up natively (KI-0078; done for backtests in CHANGE-0045: LEAN's pythonnet loads QuantLab's own Python 3.11,
the install copies LEAN's Python modules next to the launcher). (2) Dataset runs get a data folder with LEAN's databases and the dataset only (no sample
prices), as in the Docker launcher. (3) The first real LEAN run showed that QuantLab's daily-close metrics disagreed
with LEAN's statistics because they ignored the starting equity, the exact time span and intraday drawdowns. From now
on total return, CAGR and max drawdown are computed on the full-resolution equity curve (starting equity, exact
elapsed time, all points); volatility, Sharpe, Sortino and best/worst day stay on daily returns (with the starting
equity as the base of the first day). Earlier runs keep their recorded metrics; recomputed metrics can differ
(more conservative drawdowns).

### DEC-0033 — Paper deployments of LEAN strategies: QuantLab prices in, QuantLab orders out
State: ACCEPTED (owner: "go ahead with the next slice", 2026-11-08; builds on DEC-0007/0030/0031/0032)
Decisions: (1) `quantlab paper deploy <strategy> --deployment <name>` runs a C# strategy on the native LEAN engine in
LEAN's real-time mode; it is allowed only for strategies promoted to `paper` (or `live_candidate`) whose files have not
changed since (the lifecycle approval is the owner's go-ahead). (2) Orders go LEAN → QuantLabBrokerage → gateway →
OMS → risk gate → paper broker; prices come from QuantLab's own quote source through the same gateway
(`QuantLabDataQueue`), so the strategy and the risk gate see the same quotes and their data state (non-LIVE data is
reported to the algorithm). (3) The strategy is snapshotted and compiled per deployment; one deployment per name (OS
lock); fixed LEAN command line and existing config file; start/stop audited; LEAN, gateway and token file are always
cleaned up. (4) `force-exchange-always-open` exists for synthetic tests only and is not reachable from the CLI.
(5) No live trading: everything stays behind the paper-only OMS (DEC-0010).

### DEC-0034 — Pausing one paper deployment
State: ACCEPTED (owner: "1 2" — pause/resume and strategy isolation, 2026-11-08; builds on DEC-0027/0033)
Decisions: (1) `quantlab paper pause <deployment> --reason …` makes the gateway refuse every new order of that
deployment from its next order on; LEAN keeps running and receiving prices, cancels stay allowed; working orders are
cancelled unless `--keep-orders`; positions are never flattened. (2) `quantlab paper resume <deployment>` needs a
person at an interactive terminal and the typed phrase `RESUME DEPLOYMENT` (same rule as disengaging the kill
switch, CLAUDE.md §4). (3) The state is the latest `paper.deploy.pause` / `paper.deploy.resume` entry of the
append-only audit log (no new table or migration): it survives restarts and is checked on every order. (4) The web
console shows the paused state only; it gets no pause or resume control (DEC-0028 unchanged). (5) The kill switch
still blocks every order of every deployment and wins over a resumed deployment.

### DEC-0035 — Paper deployments run without network (strategy isolation)
State: ACCEPTED (owner chose "Linux/WSL2 kernel isolation (recommended)", 2026-11-08; addresses KI-0082)
Decisions: (1) On Linux / WSL2 the LEAN process of `quantlab paper deploy` runs in its own network namespace
(`unshare --user --map-root-user --net`): no network, the host's loopback unreachable, so strategy code cannot reach
IB Gateway, the internet or host services. (2) The gateway then listens on an owner-only Unix socket in a private
temporary folder (TCP otherwise); the C# plugin reads `unix` from the run file. (3) Before every deployment
`core.isolation.probe` starts a process the same way and checks that a host loopback listener is unreachable.
(4) `[paper] deployment_isolation`: `auto` (default) — required on Linux (a failed probe refuses the deployment),
on Windows / macOS the deployment runs unisolated with a warning; `required` — refuses without isolation; `off` —
the owner's explicit choice. The result is recorded in the start audit entry, `status.json`, `provenance.json` and
shown in the console. (5) Not covered: file access (the strategy runs as the owner and can read the owner's files)
and Windows/macOS network isolation (needs administrator rights or containers).

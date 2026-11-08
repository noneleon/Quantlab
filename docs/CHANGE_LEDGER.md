# Change Ledger

Every meaningful modification gets a sequential `CHANGE-NNNN`. Check here before starting work on a problem —
it may already have been addressed (or attempted and failed).

---

### CHANGE-0001 — Phase 0 audit: project memory, architecture, parity matrix, env probe
Date: 2026-11-06 · Module: docs, .claude, tools · Feature IDs: MEM-001, MEM-002, MEM-003
Problem: New repository (only README.md) with no architecture, roadmap or project memory.
Files added:
- `CLAUDE.md`
- `docs/` PROJECT_CHARTER, PROJECT_STATUS, ARCHITECTURE, FEATURE_MATRIX, IMPLEMENTATION_STATUS, MODULE_OWNERSHIP,
  DECISION_LOG, CHANGE_LEDGER, KNOWN_ISSUES, TESTING_STRATEGY, DATA_DICTIONARY, CONFIGURATION_REFERENCE,
  SECURITY_POLICY, ROADMAP, SESSION_HANDOFF
- `.claude/commands/{session-start,session-end,feature-check}.md`, `.claude/agents/trading-safety-reviewer.md`
- `tools/env_check.py`, `tests/tools/test_env_check.py`, `tests/__init__.py`, `tests/tools/__init__.py`, `.gitignore`
Files modified: `README.md` (project description + doc links)
Reason: Mandatory Phase 0 deliverables; establish anti-forgetting system before any code.
Tests: `python3 -m unittest discover -s tests -v` → Ran 8 tests, OK (after fixing test-loader `sys.modules` registration;
first run: 1 loader error). `python3 tools/env_check.py` executed in dev container (output in SESSION_HANDOFF).
Result: PASS · Regression status: n/a (no prior code)

### CHANGE-0002 — Phase 1 step 1a: package skeleton, configuration, logging, home layout, CI
Date: 2026-11-06 · Module: core, repo · Feature IDs: CORE-001, CORE-002, CORE-003, CORE-005, MEM-004
Problem: Phase 1 start (owner approved architecture and DEC-0002..0012 on 2026-11-06).
Files added: `pyproject.toml`, `uv.lock`, `.python-version`, `src/quantlab/{__init__,py.typed}`,
`src/quantlab/core/{__init__,paths,config,logging}.py`, `tests/conftest.py`,
`tests/unit/core/{test_paths,test_config,test_logging}.py`, `.github/workflows/ci.yml`
Files modified: `docs/DECISION_LOG.md` (DEC-0002..0012 → ACCEPTED, DEC-0007 Option A),
`tools/env_check.py` (ruff format only, no behaviour change), `tests/tools/test_env_check.py` (ruff format only),
memory docs, `CLAUDE.md` (commands), `README.md`.
Reason: Foundation every later module depends on. Safety rules enforced in config itself: live mode rejected,
loopback-only API unless remote_access, LEAN image must be digest-pinned, unknown keys rejected, AI keys only as
`secret://` references.
Tests: `uv run pytest --cov` → 41 passed (Python 3.11.15, Linux), coverage 97 %. `ruff check`, `ruff format --check`,
`mypy --strict` (5 files) → clean. CI workflow not yet observed on GitHub (Windows leg unverified).
Note: `ruff format` also reformatted a code block inside docs/ARCHITECTURE.md; reverted, `docs/` excluded from ruff.
Result: PASS · Regression status: PASS (env_check tests still pass under pytest)

### CHANGE-0003 — Phase 1 step 1b: metadata DB, migrations, audit log, ids/clock, secret store
Date: 2026-11-06 · Module: core · Feature IDs: CORE-004, CORE-006 (partial), CORE-010, SEC-001, MEM-004
Problem: Persistence, auditability and secret handling needed before engine/strategy/backtest records (1c–1d).
Files added: `src/quantlab/core/{ids,clock,db,audit,secret_store}.py` (secret_store was first named secrets.py — see CHANGE-0004), `src/quantlab/core/migrations/{env.py,script.py.mako}`,
`src/quantlab/core/migrations/versions/0001_audit_log.py`, `tests/unit/core/{test_ids_clock,test_db_audit,test_secrets}.py`
Files modified: `pyproject.toml`/`uv.lock` (+sqlalchemy 2.1.8, alembic, keyring), `tests/conftest.py` (db fixtures), memory docs.
Design: audit_log append-only via SQLite/Postgres triggers + ORM flush guard; sha256 hash chain with `verify_chain`;
details redacted before storage. Secrets by `secret://` ref, keyring then `QUANTLAB_SECRET__*` env fallback, returned
as SecretStr; set/delete audited first (same transaction) so no keyring change goes unrecorded.
Tests: `uv run pytest --cov` → 75 passed, 96 % coverage (Linux, Py 3.11.15); ruff + mypy --strict clean (12 files).
Wheel build verified to include migrations. Two test bugs found and fixed during the session (not code bugs):
ULID test assumed global generator state (→ refactored into `UlidGenerator` class, behaviour unchanged) and a float
equality in the overflow test.
CI: step-1a runs #1/#2 green on ubuntu + windows → MEM-004 VERIFIED, KI-0009 resolved.
Result: PASS · Regression status: PASS (all 1a tests still pass)

### CHANGE-0004 — Fix: secret store module was never committed (gitignored); CI red on 1b
Date: 2026-11-06 · Module: core, repo hygiene · Feature IDs: SEC-001, MEM-004
Problem: CI run #3 failed (ruff I001 in `tests/unit/core/test_secrets.py`, both OSes). Root cause, found by
diffing a fresh clone against the working tree: `.gitignore` rule `secrets*` matched `src/quantlab/core/secrets.py`,
so the module was never committed. Locally everything passed because ruff also honours `.gitignore` and silently
skipped the file (hiding 3 E501 errors), while pytest/mypy used the untracked file on disk.
Fix: renamed module to `src/quantlab/core/secret_store.py` (keeps the broad secrets-file ignore rule and avoids
shadowing stdlib `secrets`); fixed the 3 line-length errors; added regression guard
`tests/unit/test_repo_hygiene.py` (fails if any file under src/, tests/, tools/ is gitignored — verified it fails
with the original filename). Doc references updated.
Public API change: import path `quantlab.core.secrets` → `quantlab.core.secret_store` (no consumers yet).
Tests: `uv run pytest` → 76 passed; ruff check/format + mypy clean; fresh-clone check before push (see handoff).
Lesson: verify from a fresh clone (`git clone` + `uv sync --locked` + checks) before pushing new files.
Result: PASS · Regression status: PASS

### CHANGE-0005 — Fix: Alembic config broke on Windows SQLite URLs (`%` interpolation)
Date: 2026-11-06 · Module: core (db) · Feature IDs: CORE-004
Problem: CI run #7 — Ubuntu green, Windows 12 errors: `ValueError: invalid interpolation syntax in
'sqlite:///C%3A/...'`. SQLAlchemy renders the drive colon as `%3A`; Alembic's ConfigParser-based Config treats `%`
as interpolation. Not reproducible on Linux paths.
Fix: escape `%` → `%%` in `_alembic_config` (`src/quantlab/core/db.py`). Regression test
`test_alembic_config_handles_percent_encoded_urls` uses a `%3A` path on Linux; verified it fails without the fix.
Tests: `uv run pytest` → 77 passed (Linux). CI run #9: the 12 previously failing Windows tests passed; the new
regression test itself failed on Windows (it created a directory decoding to `pct:dir`, invalid on Windows) →
rewritten to use an un-connected engine; verified it still fails without the fix.
Result: PASS — CI runs #11 (push) and #12 (PR) green on ubuntu + windows · Regression status: PASS

### CHANGE-0006 — Phase 1 step 1c: EngineAdapter + LEAN Docker launcher, config builder, result parser
Date: 2026-11-06 · Module: engine (new), core (config) · Feature IDs: ENG-001, ENG-002, ENG-004, ENG-005, ENG-006, MEM-004
Problem: Milestone M1 needs QuantLab to launch LEAN locally and read its results without Lean CLI / QC account.
Research (upstream, 2026-11-06): LEAN Launcher/config.json keys; `algorithm-id` names result files
(Engine/Results/BacktestingResultHandler.cs, Queues/JobQueue.cs); image WORKDIR `/Lean/Launcher/bin/Debug`, bundled
sample data at `/Lean/Data` (Dockerfile); Lean CLI mounts Python projects at `/LeanCLI` and results at `/Results`;
order enums from Common/Orders/OrderTypes.cs. `quantconnect/lean:latest` amd64 digest recorded in
CONFIGURATION_REFERENCE.md as a candidate pin (not yet run).
Files added: `src/quantlab/engine/{__init__,base}.py`, `src/quantlab/engine/lean/{__init__,config,results,docker}.py`,
`tests/unit/engine/{test_lean_config,test_lean_results,test_lean_docker}.py`, `tests/integration/test_lean_docker_e2e.py`,
`tests/fixtures/{fake_docker.py,lean/README.md,lean/sample_result.json,algorithms/buy_and_hold_spy/main.py}`
Files modified: `src/quantlab/core/config.py` (+engine.lean.docker_command/network/timeout_s), `tests/unit/core/test_config.py`,
`pyproject.toml`/`uv.lock` (+import-linter dev dep, 2 contracts), `.github/workflows/ci.yml` (+lint-imports),
`docs/ARCHITECTURE.md` §5.1 (protocol updated to the implemented shape), memory docs.
Design notes: runs are immutable (non-empty run dir refused); `--network none` default; strategy/data/config mounts
read-only; docker invoked without shell via configurable command; fake docker executable enables launcher tests on
any OS; synthetic fixture clearly labelled.
Tests: `uv run pytest --cov` → 127 passed, 1 skipped (real-Docker e2e: no daemon/image here), coverage 96 %;
ruff, mypy --strict (18 files), lint-imports (2 kept; verified both break when core imports engine) clean.
Result: PASS — CI #15/#16 green on ubuntu + windows · Regression status: PASS (all earlier tests pass)

### CHANGE-0007 — Phase 1 step 1d: strategy registry, backtest service, reproducibility, metrics
Date: 2026-11-06 · Modules: strategies, backtest, analytics (new), migrations (moved), engine, core · Feature IDs:
STR-001..003, BT-001..003, AN-001 (partial), CORE-004
Problem: M1 needs registered, versioned strategies; recorded, reproducible backtests; and QuantLab's own metrics.
Files added: `src/quantlab/strategies/{__init__,snapshot,registry}.py`, `src/quantlab/analytics/{__init__,metrics}.py`,
`src/quantlab/backtest/{__init__,models,service}.py`, `src/quantlab/migrations/__init__.py`,
`src/quantlab/migrations/versions/0002_strategies_backtests.py`, `strategies/README.md`,
`strategies/examples/sma_crossover/{main.py,strategy.toml}`, `tests/unit/{strategies,analytics,backtest}/*`
Files moved: `src/quantlab/core/migrations/` → `src/quantlab/migrations/` (DEC-0013); `core/db.py` script location updated.
Files modified: `src/quantlab/engine/base.py` (+`EngineResult.statistic_values`), `engine/lean/results.py` (fills it),
`src/quantlab/migrations/env.py` (registers all model modules), `tests/unit/core/test_db_audit.py` (head = 0002, schema-drift
guard), `tests/unit/engine/test_lean_results.py`, `pyproject.toml` (layered + 2 forbidden import contracts), docs.
Decisions: DEC-0013 (migrations package), DEC-0014 (per-run code snapshot).
Tests: `uv run pytest --cov` → 180 passed, 1 skipped (real Docker), 94 % coverage; ruff, mypy --strict (28 files),
lint-imports (3 contracts kept; each verified to break on a deliberate violation). New schema-drift test proves
migration 0002 == ORM models.
Incident (no impact): a bulk `sed` to drop `# fmt: skip` markers also edited committed `core/audit.py` and
`engine/lean/docker.py`; detected via `git diff` and restored before commit.
Result: PASS · Regression status: PASS (all earlier tests pass)

### CHANGE-0008 — Phase 1 step 1e: CLI, local read-only API with token auth; secret write verification
Date: 2026-11-06 · Modules: cli, api, bootstrap (new), backtest, core · Feature IDs: CORE-008, CORE-009 (partial), SEC-003, SEC-001
Problem: M1 needs a usable interface (CLI) and the read API the UI will build on.
Files added: `src/quantlab/bootstrap.py` (AppContext composition root, `initialize`), `src/quantlab/cli/{__init__,main}.py`,
`src/quantlab/__main__.py`, `src/quantlab/api/{__init__,app}.py`, `tests/fixtures/{__init__,m1}.py`,
`tests/unit/test_bootstrap.py`, `tests/unit/api/test_app.py`, `tests/unit/cli/test_cli.py`
Files modified: `src/quantlab/backtest/service.py` (engine optional for read-only use; fail fast before snapshot),
`src/quantlab/core/secret_store.py` (read-back verification), `tests/conftest.py` (autouse in-memory keyring — tests never
touch the real OS keyring), `tests/unit/core/test_secrets.py`, `pyproject.toml` (+typer, fastapi, uvicorn, dev httpx;
`[project.scripts] quantlab`; layers contract now cli > api > bootstrap > …), docs.
Bug found by smoke test: with keyring's null backend `quantlab init` claimed the API token was "stored in the OS keyring"
although nothing was stored. Root cause: some backends accept and drop writes. Fix: `SecretStore.set` reads back and raises
`SecretError` if the value did not persist (audit entry rolled back). Regression tests verified to fail without the fix.
Tests: `uv run pytest --cov` → 214 passed, 1 skipped (real Docker), 92 % coverage; ruff, mypy --strict (34 files),
lint-imports (3 kept). Manual smoke: `quantlab init` + `quantlab serve` + curl — health 200, no/bad token 401, valid token
200, foreign Host 400, token absent from all logs.
Result: PASS — CI #21/#22 green on ubuntu + windows · Regression status: PASS

### CHANGE-0009 — Phase 2a: market-data foundation (providers, CSV import, validation, versioned catalog, export)
Date: 2026-11-06 · Modules: data (new), cli, api, bootstrap · Feature IDs: DATA-001..007, DATA-016, CORE-009 (datasets endpoint)
Problem: Backtests and research need real, validated, versioned data with provenance instead of LEAN sample data.
Files added: `src/quantlab/data/{__init__,schema,calendars,validation,storage,catalog}.py`,
`src/quantlab/data/providers/{__init__,base,csv_file}.py`, `src/quantlab/migrations/versions/0003_datasets.py`,
`src/quantlab/cli/{common,data}.py`, `tests/unit/data/*`, `tests/unit/cli/test_cli_data.py`
Files modified: `src/quantlab/cli/main.py` (shared helpers moved to `cli/common.py`; `data` command group),
`src/quantlab/bootstrap.py` (`AppContext.data`), `src/quantlab/api/app.py` (`GET /api/datasets`),
`src/quantlab/migrations/env.py` (data models), `pyproject.toml`/`uv.lock` (+pyarrow, duckdb, pandas, exchange-calendars;
mypy overrides for untyped libs; `quantlab.data` in the engine/strategies layer), `tests/unit/core/test_db_audit.py`
(head 0003, data models in drift check), `tests/unit/api/test_app.py`, docs. Decision: DEC-0015.
Tests: `uv run pytest --cov` → 248 passed, 1 skipped (real Docker), 92 % coverage; ruff, mypy --strict (46 files; pandas/
pyarrow/exchange_calendars/duckdb treated as untyped), lint-imports (3 kept). Manual CLI smoke on a fresh home: import →
missing 2024-07-05 detected (completeness 0.8), early close mapped to 17:00 UTC, conflicting revision rejected and
quarantined, head shows unchanged data. Remaining pandas deprecation warnings originate in exchange_calendars/duckdb.
Result: PASS · Regression status: PASS

### CHANGE-0010 — Phase 2b: LEAN-format export; backtests on pinned dataset versions
Date: 2026-11-06 · Modules: data, engine, backtest, cli · Feature IDs: DATA-009, DATA-006, BT-002, ENG-002
Research (LEAN sources + bundled sample data, 2026-11-06): daily/hour/minute file layout and line formats, ×10 000 price
scaling, map-file rows (exchange column optional, `MapFileRow.Parse`), factor-file rows and minimum-date rule
(`CorporateFactorRow.Parse`: data before min date − 1 day is ignored).
Files added: `src/quantlab/data/lean_export.py`, `tests/unit/data/test_lean_export.py`,
`strategies/examples/sma_daily/{main.py,strategy.toml}`
Files modified: `src/quantlab/data/catalog.py` (`lean_export`), `src/quantlab/engine/lean/config.py` (dataset mount point +
entrypoint script), `src/quantlab/engine/lean/docker.py` (dataset overlay instead of replacing `/Lean/Data`),
`src/quantlab/backtest/service.py` (`DataBinding`; `run(..., dataset=, dataset_version=)`; rerun re-binds and verifies the
export hash), `src/quantlab/cli/{main,data,common}.py` (`--dataset/--dataset-version`, `data lean-export`, provenance
warning for non-real data), `tests/fixtures/fake_docker.py` (records dataset mount contents),
`tests/unit/engine/test_lean_docker.py` (new argv expectations + real `sh` execution of the entrypoint script),
`tests/unit/backtest/test_service.py`, `tests/unit/cli/test_cli_data.py`, docs. Decision: DEC-0016.
Tests: `uv run pytest` → 263 passed, 1 skipped; ruff, mypy --strict (47 files), lint-imports clean. Golden tests: LEAN
sample lines (daily, hour, minute SPY) → canonical frame → export reproduces LEAN's bytes exactly. Manual CLI smoke:
`data lean-export` writes the expected tree and reuses/verifies it on the second call.
Result: PASS — CI #29/#30 green on ubuntu + windows · Regression status: PASS (one existing test updated for the intended mount change)

### CHANGE-0011 — Phase 2c: research API, statistics, JupyterLab launcher
Date: 2026-11-07 · Modules: research (new), analytics, cli · Feature IDs: RES-001, RES-002, RES-004 (partial)
Files added: `src/quantlab/research/{__init__,api,notebooks}.py`, `src/quantlab/analytics/statistics.py`,
`tests/unit/research/{__init__,test_research}.py`, `tests/unit/analytics/test_statistics.py`
Files modified: `src/quantlab/cli/main.py` (`quantlab research`), `pyproject.toml`/`uv.lock` (optional extra `research`:
jupyterlab, matplotlib; layers contract `quantlab.api | quantlab.research` above bootstrap), docs. Decision: DEC-0017.
Tests: `uv run pytest --cov` → 278 passed, 1 skipped (real Docker), 92 %; ruff, mypy --strict (51 files), lint-imports clean.
Manual verification: installed the extra, created a synthetic `spy-daily` dataset, executed the starter notebook headless
(`jupyter nbconvert --execute`): all cells produced output (datasets, provenance attrs + warning, distribution, metrics,
backtests). Found and fixed: the provenance warning pointed at `api.py` instead of the notebook cell (stacklevel); test
added. Base environment restored afterwards (`uv sync`).
Incident: CI run #33 failed `ruff format --check` — this ruff version formats Python code blocks inside Markdown and
the new README snippet was not ruff-formatted. The fresh-clone check caught it, but the push was chained with `;` and
ran anyway. Fix (commit "Exclude Markdown from ruff"): `extend-exclude = ["docs", "*.md"]`; CI run #35 green on
ubuntu + windows. Process fix: push is now chained with `&&` after every fresh-clone check.
Result: PASS (after fix) · Regression status: PASS

### CHANGE-0012 — Phase 2d: web UI shell, dataset browser, backtest views, same-origin serving
Date: 2026-11-07 · Modules: web (new), api, cli, core (config) · Feature IDs: UI-001 (partial), UI-002 (partial), UI-004, UI-008,
CORE-009
Files added: `web/{package.json,package-lock.json,tsconfig.json,vite.config.ts,index.html}`,
`web/src/{main.tsx,App.tsx,api.ts,hooks.ts,format.ts,modules.ts,styles.css}`, `web/src/components/{Badges,States,PriceChart}.tsx`,
`web/src/pages/{Dashboard,Datasets,Backtests,Strategies,ModulePage}.tsx`, `web/src/{api,modules}.test.ts`,
`web/src/components.test.tsx`
Files modified: `src/quantlab/api/app.py` (`GET /api/datasets/{name}`, `/api/datasets/{name}/bars`, security headers
middleware, UI static serving with SPA fallback and "UI not built" page, `default_ui_dir`), `src/quantlab/cli/main.py`
(`serve` prints the token URL and serves the UI), `src/quantlab/core/config.py` (`server.ui_dir`),
`tests/unit/api/test_app.py`, `tests/unit/test_repo_hygiene.py` (covers `web/`), `.github/workflows/ci.yml` (`web` job:
npm ci, typecheck, test, build), docs. Decision: DEC-0018.
Tooling notes: npm 10.9 crashed (`Cannot read properties of null (reading 'edgesOut')`) resolving vitest 4.1's optional
peers; bisected to vitest ≥ 4.1 → pinned `~4.0`. jsdom/testing-library dropped; components tested with static rendering.
Tests: Python 282 passed, 1 skipped; ruff, mypy --strict, lint-imports clean. Web: `tsc --noEmit` clean, vitest 16 passed,
`vite build` ok. Manual Playwright (Chromium) smoke against a real `quantlab serve` with a synthetic dataset and a
fake-engine backtest: token removed from URL and kept across reload; new tab without token shows auth help; dashboard,
dataset (chart, completeness, missing-session warning) and backtest pages render; planned module page has 0 controls;
token absent from access log and quantlab.log. Found and fixed: Lightweight Charts' logo `<style>` violated the CSP →
logo disabled, NOTICE attribution rendered as text (test added); re-run had 0 console errors.
Result: PASS — CI #39/#40 green (ubuntu, windows, web) · Regression status: PASS

### CHANGE-0013 — Phase 2e (part 1): corporate actions (DATA-008); calendar history fix
Date: 2026-11-07 · Modules: data, research, api, cli, backtest, web · Feature IDs: DATA-008, DATA-007 (fix), DATA-009, RES-002
Research: LEAN factor-file semantics derived from LEAN's real AAPL factor file and checked by hand against AAPL's actual
dividends ($2.65 2012-08-09, $0.82 2020-08-07, $0.205 2020-11-06) and splits (2:1 2000/2005, 7:1 2014, 4:1 2020).
Files added: `src/quantlab/data/corporate_actions.py`, `tests/fixtures/lean/aapl_factor_file.csv` (LEAN, Apache-2.0,
attributed in fixtures README), `tests/unit/data/{test_corporate_actions,test_actions_integration}.py`
Files modified: `data/{catalog,storage,lean_export,calendars,validation}.py`, `data/providers/{base,csv_file}.py`
(`RawBars.actions`, `load_actions_csv`), `cli/data.py` (`import --actions`, `data actions`, `head --adjust`),
`research/api.py` (`adjust=`, `actions()`), `api/app.py` (detail `corporate_actions`, bars `adjust`),
`backtest/service.py` (manifest records corporate-action count + normalization from the export),
`web/src/{api.ts,pages/Datasets.tsx}` (actions table), tests in cli/research/api/backtest/calendars, docs. Decision: DEC-0019.
Bug found and fixed: exchange_calendars covers only ~20 years by default, so any daily history before ~2006 would have
been rejected as "non-session bars" and quarantined. Calendars now load from 1970 (fallback to the library default for
calendars that cannot); regression test added. `session_dates` moved from validation into calendars (shared, now also
correct for intraday bars on 24/7 calendars).
Tests: Python 301 passed, 1 skipped; web 16 passed + build; ruff, mypy --strict, lint-imports clean. Golden: recomputed
factor rows reproduce LEAN's AAPL factor file (40 rows, rel. tol 2e-6).
Result: PASS · Regression status: PASS (no existing test changed; datasets without actions keep identical hashes)

### CHANGE-0014 — Phase 2e (part 2): Tiingo EOD provider, `data fetch`, `quantlab secret`
Date: 2026-11-07 · Modules: data, cli · Feature IDs: DATA-011, DATA-008 (verification), SEC-001
Research: Tiingo's docs are a JS app; endpoint, auth, field semantics, rate limits and permitted-use text were read from
its documentation bundle (2026-11-07). Environment: personal cloud environment "Quantlab" created (hosts api/www/apimedia
.tiingo.com, owner-approved); Tiingo key saved by the owner as an environment network secret (never seen by the agent).
Files added: `src/quantlab/data/providers/tiingo.py`, `src/quantlab/cli/secret_commands.py`, `tests/unit/data/test_tiingo.py`
Files modified: `src/quantlab/data/catalog.py` (`fetch`: resolution + licence checks, then normal import),
`src/quantlab/cli/{data,main}.py` (`data fetch`, `secret` group), `pyproject.toml`/`uv.lock` (httpx promoted to a runtime
dependency), `tests/unit/cli/test_cli_data.py`, docs. Decision: DEC-0020.
The repo-hygiene guard (CHANGE-0004) caught a new `cli/secrets.py` matched by `.gitignore` `secrets*`; renamed to
`cli/secret_commands.py` before commit.
Bug found by tests: a persistent HTTP 429 on the last retry fell through to the generic error branch → fixed.
Real verification (scratch home only; data not committed — Tiingo forbids redistribution):
- AAPL 2020-08: raw close 499.23 on 08-28 (= LEAN reference price), splitFactor 4.0 on 08-31 (new shares per old),
  divCash 0.82 on 08-07 → KI-0038 resolved.
- AAPL 1998–2021 from Tiingo through `factor_file_rows` reproduces LEAN's AAPL factor file: 41/41 row dates, max relative
  difference 2.6e-6 (two independent sources agree).
- CLI: `data create` → `data fetch` (566 rows, 10 actions) → `data lean-export`: factor rows equal LEAN's to its precision.
Tests: Python 316 passed, 1 skipped; web unchanged (16); ruff, mypy --strict, lint-imports clean.
Result: PASS · Regression status: PASS

### CHANGE-0015 — Market-data migration slice A: provider-agnostic interface, Tiingo intraday, mock streaming, config switch
Date: 2026-11-07 · Modules: data, core (config), backtest, cli · Feature IDs: DATA-018, DATA-020, DATA-021, DATA-022,
DATA-011 (minute), DATA-013 (partial), DATA-017, DATA-005 (minute coverage)
Problem: owner requirements for a Tiingo (dev) → IBKR (prod) migration by configuration (see DEC-0021).
Research (real Tiingo API, 2026-11-07): IEX 1-minute `date` = bar start (390 bars 13:30Z–19:59Z); a response holds at most
10 000 bars and silently keeps the most recent ones (6-month request started 2024-05-24) → windowed fetching + cap
detection; hourly bucketing ambiguous → minute only; intraday volume IEX-only.
Files added: `src/quantlab/data/{market_data,factory,compare}.py`, `src/quantlab/data/providers/mock_stream.py`,
`src/quantlab/cli/market.py` (`quantlab md status|snapshot|stream`), `tests/unit/market/{__init__,test_market_data}.py`
Files modified: `data/providers/tiingo.py` (IEX minute bars), `data/validation.py` + `data/calendars.py`
(`session_bounds`, minute coverage: missing minutes, extended-hours bars), `core/config.py` (`[market_data]`),
`backtest/service.py` (`compare_results`), `cli/{data,main,common}.py` (`data fetch` via factory + `--since-last`,
`data compare`, `backtest compare`, `md` group, MarketDataError reported cleanly), tests, docs. Decision: DEC-0021.
Bugs found: `MarketDataError` surfaced as a traceback in the CLI (now a one-line error).
Real verification (scratch home; Tiingo data not committed): SPY 1-minute 2024-06-24..07-12 → 5 850 bars, 100 %
regular-session coverage; validation flagged 180 bars Tiingo returned after the 2024-07-03 13:00 ET early close; mock
stream labelled SIMULATED and refused under `--require-live`; Tiingo vs LEAN sample (QuantConnect) SPY daily 2013–2021:
1 888/1 888 common bars, max close difference 0.114 %.
Not verified: same-strategy results across providers through real LEAN (no Docker daemon here); IBKR (slice B).
Tests: Python 335 passed, 1 skipped; ruff, mypy --strict, lint-imports clean.
Result: PASS · Regression status: PASS (one CLI test updated: `data fetch` now checks the dataset before the API key)

### CHANGE-0016 — Market-data migration slice B: IBKR market data provider (TWS / IB Gateway)
Date: 2026-11-07 · Modules: data, core (config), cli · Feature IDs: DATA-019, DATA-022, DATA-013 (partial), DATA-017
Problem: DEC-0021 slice B — IBKR historical bars + Level I real-time quotes behind the provider-agnostic interface.
Research: `ib_async` 2.1.0 (BSD, PyPI 2025-12-08) source: `Ticker.time` = local arrival (UTC), `lastTimestamp` = tick 45,
`marketDataType` 1/2/3/4 = live/frozen/delayed/delayed-frozen, `reqHistoricalData(formatDate=2)` → UTC datetimes
(daily → dates), `connect(readonly=True)`, warning codes. IB's API docs could not be fetched here (hosts blocked) →
unverified assumptions listed in KI-0044.
Files added: `src/quantlab/data/providers/ibkr.py` (IBClient protocol, IBKRProvider, HistoricalPacer),
`src/quantlab/data/providers/ib_async_client.py` (thin ib_async adapter), `tests/unit/market/test_ibkr.py`
Files modified: `core/config.py` (`brokers.ibkr.market_data_client_id`, `market_data.ibkr_data_type`, validators),
`data/factory.py` (IBKR wiring; paper port), `data/catalog.py` (fetch refuses provider/dataset price-adjustment
mismatch), `data/providers/tiingo.py` (`adjustment = "raw"`), `cli/market.py` (status shows reconnects/last error),
`pyproject.toml` + `uv.lock` (`ibkr` extra and dev dependency `ib_async>=2.1,<3`), tests, docs.
Behaviour: read-only market-data connection with its own client id (cannot place orders); quote state from what IB
delivered (fallback to delayed → DELAYED, rejected under require_live; IB 10167/354 shown in status); require_live
requests live only; reconnect with exponential backoff + resubscribe, gives up loudly after 5 attempts; 1101 →
resubscribe; historical: daily windows ≤ 365 days, minute per regular session, bar start + 1 min = bar end, paced,
pacing violations retried with backoff, no-security-definition errors clear; non-session daily bars passed to
validation (rejected, never hidden).
Real checks: CLI against a closed port 4002 → clear "Start IB Gateway or TWS…" error, `md status` shows last error.
Not verified: anything against a real IB Gateway/TWS (owner action, paper account).
Tests: Python 354 passed, 1 skipped; ruff, mypy --strict, lint-imports clean.
Result: PASS (fake client) · Regression status: PASS (factory test updated: IBKR now builds instead of failing)

### CHANGE-0017 — Phase 3a: chart datasets, benchmark comparison, downloadable report, run-detail charts
Date: 2026-11-07 · Modules: analytics, backtest, api, cli, web · Feature IDs: AN-002, AN-003, BT-008, UI-002, AN-001
Problem: Phase 3 (DEC-0022 slice 3a) — runs had metrics only; no charts, trades, benchmark or report.
Files added: `src/quantlab/analytics/{charts,report}.py`, `web/src/pages/BacktestCharts.tsx`,
`web/src/components/TimeSeriesChart.tsx`, `tests/unit/analytics/{test_charts,test_report}.py`
Files modified: `backtest/service.py` (`results.json`, `results`, `chart_data`, `report_html`, `build_report`,
benchmark loading), `api/app.py` (`/api/backtests/{id}/charts`, `/report`), `cli/main.py` (`backtest report`),
`web/src/{api.ts,pages/Backtests.tsx,components/PriceChart.tsx,styles.css}` (PriceChart now uses the shared chart),
tests, `pyproject.toml` (E501 exempt for the HTML template module), docs. Decision: DEC-0022.
Bugs found during the change: report x-axis end label clipped (fixed: anchored start/middle/end).
Verification: fake-engine run in a scratch home served by `quantlab serve`, Playwright: run detail rendered 14
canvases, no console/CSP errors; report downloaded via the token-authenticated fetch and opened offline with zero
network requests. All values synthetic (LEAN fixture) — no performance claims.
Tests: Python 376 passed, 1 skipped; web 18 passed; ruff, mypy --strict, lint-imports clean; web typecheck + build OK.
Not verified: charts/report on a real LEAN run (no Docker daemon here; KI-0002).
Result: PASS · Regression status: PASS

### CHANGE-0018 — Phase 3b: quantlab_lean helper, configurable execution models, strategy templates
Date: 2026-11-07 · Modules: engine (lean), core (config), backtest, cli, analytics (report), strategies · Feature IDs:
ENG-009, BT-005, BT-007, STR-005
Problem: DEC-0022 slice 3b — fees/slippage/fills/settlement were whatever LEAN defaulted to and not recorded.
Research: LEAN source (QuantConnect/Lean cc98c42, sparse clone): constructors of ConstantFeeModel, ConstantSlippageModel,
VolumeShareSlippageModel, ImmediateFillModel, DelayedSettlementModel(days, TimeSpan), ImmediateSettlementModel,
BrokerageModelSecurityInitializer(model, seeder), FuncSecuritySeeder(PyObject), SetBrokerageModel(BrokerageName,
AccountType), Security.Set*Model, QCAlgorithm.GetLastKnownPrices; `python-additional-paths` in Launcher/config.json;
`Equity.DefaultSettlementDays = 1`, `DefaultSettlementTime = 06:00`; ROC/RSI/MonthStart/AfterMarketOpen signatures.
Files added: `src/quantlab/engine/lean/library/quantlab_lean/__init__.py`, `strategies/templates/*` (3 templates),
`tests/fixtures/lean_stub/AlgorithmImports.py` (recording stub, not LEAN), `tests/unit/engine/test_quantlab_lean.py`,
`tests/unit/strategies/test_templates.py`
Files modified: `core/config.py` (`[backtest.models]`), `engine/base.py` (`BacktestSpec.models`,
`EngineResult.models_applied`), `engine/lean/config.py` (`python-additional-paths`), `engine/lean/docker.py` (library
mount, marker check), `backtest/service.py` (resolve/record/rerun models, warnings), `cli/main.py` (`-m/--model`, models
line + warning), `analytics/report.py` (models row), `tests/fixtures/fake_docker.py` (runs the real helper against the
stub when a strategy opts in), tests, `pyproject.toml` (mypy exclude + ruff PTH exemption for the in-container
library), docs. Decision: DEC-0023.
Tests: Python 398 passed, 1 skipped; ruff, mypy --strict, lint-imports clean; wheel contains the library.
Not verified: anything inside a real LEAN container — model behaviour, `python-additional-paths`, where LEAN writes
algorithm logs (marker detection), template behaviour (KI-0050/0051).
Result: PASS (stub/fake engine) · Regression status: PASS

### CHANGE-0019 — Phase 3c (part 1): strategy library + in-browser editor, golden regression suite, vitest fix
Date: 2026-11-07 · Modules: strategies, api, bootstrap, backtest, cli, core (paths), web · Feature IDs: UI-003, STR-005,
BT-010 · Decision: DEC-0024
Problem: owner wants no QuantConnect subscription; the cloud IDE/project library and regression tooling had no local
equivalent. `npm audit`: critical/moderate advisories in vitest ≤ 4.1.10 (dev tooling).
Files added: `src/quantlab/strategies/workspace.py`, `src/quantlab/backtest/golden.py`,
`web/src/pages/Editor.tsx`, `web/src/components/CodeEditor.tsx`, `tests/unit/strategies/test_workspace.py`,
`tests/unit/backtest/{conftest,test_golden}.py`
Files modified: `api/app.py` (template list, create strategy, list/read/save files), `bootstrap.py` (workspace),
`core/paths.py` (`strategies_dir`, `golden_dir`), `backtest/service.py` (`rerun(require_same_engine=)`,
`probe_engine`), `cli/main.py` (`backtest golden record|list|check`), `web/src/{api.ts,App.tsx,modules.ts,
pages/Strategies.tsx,styles.css}` (editor lazy-loaded), `web/package.json` + lockfile (CodeMirror; vitest ~4.1.12,
lockfile regenerated with npm 11 because npm 10 crashes resolving vitest 4.1 — `npm ci` with npm 10 verified; runtime
dependency versions unchanged), `tests/fixtures/fake_docker.py` (`FAKE_DOCKER_EQUITY_SHIFT`), tests, docs.
Bugs found during the change: CodeMirror's style-mod injects a `<style>` element in the main document (blocked by the
CSP) → editor mounted in a shadow root; input `pattern` invalid under the `v` regex flag → escaped.
Verification: browser (Chromium/Playwright) against `quantlab serve` on a scratch home: create from template → editor
opens with Python highlighting → type + Ctrl+S saves to disk → file changed on disk → save refused (409) and the outside
change kept; zero CSP violations.
Tests: Python 419 passed, 1 skipped; web 20 passed, typecheck + build OK, `npm audit` 0 vulnerabilities; ruff,
mypy --strict, lint-imports clean.
Not done: native .NET launcher (ENG-003) and C# algorithms (ENG-008) — no .NET SDK and its download hosts are blocked
here; golden cases from real LEAN runs (owner).
Result: PASS · Regression status: PASS

### CHANGE-0020 — Fix: Windows CI failure in workspace tests; editor preserves CRLF line endings
Date: 2026-11-07 · Modules: tests, web · Feature IDs: UI-003
Problem: CI #55 (windows-latest) failed 3 tests in `tests/unit/strategies/test_workspace.py`: fixtures used
`Path.write_text`, which writes CRLF on Windows, so file hashes differed from the LF strings the tests hashed (test
bug). It exposed a real defect: CodeMirror's `doc.toString()` always joins lines with LF, so saving a CRLF file from the
editor silently rewrote all line endings.
Fix: tests write bytes; the editor sets `EditorState.lineSeparator` from the file and reports changes with
`state.sliceDoc()` (which honours it). Added a CRLF round-trip test (backend).
Verification: browser — CRLF file edited + saved stays CRLF, LF file stays LF (byte comparison on disk).
Tests: workspace tests 16 passed locally; full gate before push. Result: PASS · Regression status: PASS

### CHANGE-0021 — Phase 4a: parameter optimization, walk-forward, Monte Carlo/bootstrap, overfitting statistics
Date: 2026-11-07 · Modules: optimize (new), analytics, backtest, migrations, bootstrap, cli, api · Feature IDs:
OPT-001, OPT-003, OPT-004, OPT-005, OPT-006 · Decision: DEC-0025
Files added: `src/quantlab/optimize/{__init__,objective,space,models,service}.py`,
`src/quantlab/analytics/robustness.py`, `src/quantlab/migrations/versions/0004_optimizations.py`,
`src/quantlab/cli/optimize.py`, `tests/unit/optimize/{__init__,conftest,test_objective_space,test_service}.py`,
`tests/unit/analytics/test_robustness.py`
Files modified: `backtest/service.py` (`robustness`), `bootstrap.py` (`optimizer`, `read_only_optimizer`),
`migrations/env.py`, `pyproject.toml` (layer contract), `cli/{main,common}.py` (`optimize` group, `backtest
robustness`, OptimizationError), `api/app.py` (`/api/optimizations[/{id}]`, `/api/backtests/{id}/robustness`),
`tests/fixtures/fake_docker.py` (`FAKE_DOCKER_SYNTH`: synthetic equity over the start..end parameters), tests
(migration head 0003 → 0004), docs.
Verification of the statistics: expected maximum Sharpe of 100 unit-variance trials = 2.5306 (closed form) vs 2.51
(Monte Carlo of 5 000 draws); PBO = 0 when one trial dominates every period, ≈0.5 for pure noise; seeded bootstrap is
reproducible.
Tests: Python 447 passed, 1 skipped; ruff, mypy --strict, lint-imports clean. All optimization tests run on the fake
engine with synthetic equity — no market data, no performance claims.
Not verified: optimization on real LEAN runs (KI-0002); UI views (Phase 4b).
Result: PASS · Regression status: PASS

### CHANGE-0022 — Fix: concurrent backtests raced on strategy_versions (CI #59)
Date: 2026-11-07 · Modules: strategies · Feature IDs: STR-002, OPT-001
Problem: CI #59 (ubuntu) failed `tests/unit/optimize/test_service.py::test_parallel_trials` with
`UNIQUE constraint failed: strategy_versions.strategy_id, tree_hash`; CI #60 on the same commit passed — a race.
Parallel trials of the same strategy each found no version row and both inserted it. Not caught locally (timing).
Fix: `StrategyRegistry.record_version` inserts inside a savepoint and, on IntegrityError, returns the row the other
session inserted. Regression test `test_concurrent_runs_record_one_strategy_version` (4 concurrent runs): fails 3/5
times against the old code, passes 10/10 with the fix.
Result: PASS · Regression status: PASS

### CHANGE-0023 — Phase 4b: Optuna TPE + multi-objective, optimization views, multi-run comparison
Date: 2026-11-07 · Modules: optimize, analytics, backtest, api, cli, web · Feature IDs: OPT-002, UI-005, AN-004 ·
Decision: DEC-0025
Files added: `src/quantlab/analytics/comparison.py`, `web/src/pages/{Optimizations,Compare}.tsx`
Files modified: `optimize/service.py` (method `tpe`, `extra_objectives`, Pareto front, first trial alone whenever
trials run in parallel), `backtest/service.py` (`compare_many`), `api/app.py` (`/api/compare`), `cli/optimize.py`
(`--tpe`, `--extra-objective`, Pareto output), `pyproject.toml` + `uv.lock` (extra `optimize` = optuna 5.0.0, MIT;
also in the dev group), `web/src/{api.ts,App.tsx,modules.ts,modules.test.ts,components.test.tsx,styles.css,
pages/Backtests.tsx,pages/BacktestCharts.tsx}` (run selection for comparison, robustness panel), tests, docs.
Verification: browser (Playwright) on a scratch home with fake-engine studies (12-trial 2-D sweep, 4-window
walk-forward): heatmap colours apply under the strict CSP (React sets styles via CSSOM), compare + robustness panels
load; zero console/CSP errors. Synthetic numbers only.
Tests: Python 453 passed, 1 skipped; web 21 passed, typecheck + build OK; ruff, mypy --strict, lint-imports clean.
Result: PASS · Regression status: PASS

### CHANGE-0024 — Phase 4c: strategy lifecycle with evidence gates; notebook → strategy promotion
Date: 2026-11-07 · Modules: strategies, lifecycle (new), core (config), migrations, bootstrap, cli, api, web ·
Feature IDs: STR-004, RES-006 · Decision: DEC-0026
Files added: `src/quantlab/strategies/lifecycle.py`, `src/quantlab/lifecycle/{__init__,service}.py`,
`src/quantlab/migrations/versions/0005_strategy_lifecycle.py`, `src/quantlab/cli/lifecycle.py`,
`tests/unit/lifecycle/{__init__,conftest,test_lifecycle}.py`
Files modified: `core/config.py` (`[lifecycle]` thresholds), `strategies/workspace.py` (`promote_notebook`),
`bootstrap.py` (`lifecycle()`), `cli/main.py` (`strategy status|promote|demote|retire|from-notebook`), `api/app.py`
(`stage` in strategy list, `GET /api/strategies/{name}/lifecycle`), `migrations/env.py`, `pyproject.toml` (layer
contract: lifecycle above optimize), `web/src/{api.ts,pages/Strategies.tsx,styles.css}` (stage column), tests
(migration head 0004 → 0005), docs.
Bugs found during the change: the ORM model lacked the migration's unique (strategy_id, seq) constraint — caught by
the schema-drift test (`test_migrations_match_orm_models`).
Verification: browser check of the library stage column on a scratch home (migration 0005 applied to an existing
database), zero console/CSP errors. Gates exercised on the fake engine (sample data refused by default; passing path
with `require_real_data=false` only in tests).
Tests: Python 461 passed, 1 skipped; web 21 passed, typecheck + build OK; ruff, mypy --strict, lint-imports clean.
Result: PASS · Regression status: PASS

### CHANGE-0025 — Phase 5a: OMS, central risk gate, kill switch, paper broker, confirmed book, reconciliation
Date: 2026-11-07 · Modules: brokers, portfolio, risk, oms (all new), core (config), bootstrap, cli, api, migrations ·
Feature IDs: OMS-001..006, RISK-001..004, BRK-001/002/005, PORT-001/002, LIVE-002 · Decision: DEC-0027
Files added: `src/quantlab/brokers/{__init__,base,paper}.py`, `src/quantlab/portfolio/{__init__,book}.py`,
`src/quantlab/risk/{__init__,limits,models,gate}.py`, `src/quantlab/oms/{__init__,models,states,queries,service}.py`,
`src/quantlab/migrations/versions/0006_oms_risk.py`, `src/quantlab/cli/trading.py`,
`tests/unit/trading/{__init__,test_paper_broker_book,test_oms_risk}.py`
Files modified: `core/config.py` (`[paper]`), `bootstrap.py` (`paper_oms`, `kill_switch`), `cli/{main,common}.py`
(`risk init|show|decisions|kill-switch`, `paper order|cancel|orders|positions|reconcile`), `api/app.py` (read-only
`/api/paper/orders[/{id}]`, `/api/paper/portfolio`, `/api/risk`), `migrations/env.py`, `pyproject.toml` (layers +
"no back door to brokers" contract), tests (migration head 0005 → 0006), docs.
Bugs found during the change: paper broker forgot rejected order ids (an id could be reused); a triggered stop was
not persisted until filled; OMS refreshed an order in a foreign session; `research` reached the OMS through
`bootstrap` (contract scoped to direct imports, rationale in pyproject).
Safety review (trading-safety-reviewer, before commit): no BLOCKER; all structural guarantees held (OMS is the only
broker caller; every order has a stored decision before any send; reconcile never rewrites/resubmits; kill switch on
every order; limits fail closed; no API writes). 5 MAJOR + 8 MINOR findings, fixed before commit, each with a
regression test: working orders counted at worst case in position/exposure limits; orders valued at max(touch, limit,
stop); fill dedup key (broker, account, exec_id) + anomaly on colliding ids + globally unique paper exec ids; fills
carry `price_state` and the CLI/API label simulated P&L; contract forbids api/cli/research importing the paper adapter
and the OMS accepts only known paper adapter classes; kill-switch disengage phrase is an interactive prompt; kill-switch
order by sequence (not wall clock); missing bid/ask and future-dated quotes fail; `weekdays` calendar enforced;
account starting cash pinned (`oms_accounts`); new orders blocked while submissions are unresolved; reconcile skips
PENDING_SUBMIT younger than 60 s and scans all orders; broker events persisted until acknowledged after the OMS
commit; refused cancel restores the prior state. A second review pass of the fixes was started before the push;
its outcome is recorded in the next ledger entry.
Tests: Python 504 passed, 1 skipped; ruff, mypy --strict, lint-imports (5 contracts) clean. Paper tests use synthetic
SIMULATED quotes only.
Result: PASS · Regression status: PASS

### CHANGE-0026 — Phase 5a follow-up: second safety-review findings fixed
Date: 2026-11-07 · Modules: risk, oms, brokers, cli, api · Feature IDs: RISK-001..004, OMS-003..006, BRK-002, PORT-002
Problem: the second trading-safety review of CHANGE-0025 (no BLOCKER) found 4 MAJOR + 8 MINOR issues, one a regression
introduced by the conservative-valuation fix.
Fixes (each with a regression test):
- MAJOR: equity / daily-loss check valued at market marks; only notional, position and exposure use the conservative
  reference (a far-away limit could inflate equity and pass the loss limit).
- MAJOR: OMS anomalies are persisted to the audit log; reconcile re-applies every broker execution so colliding exec
  ids are always compared.
- MAJOR: kill-switch disengage requires an interactive terminal (stdin and stdout TTYs; piped input refused); the
  audit entry records the channel.
- MAJOR: paper broker state is changed only under an exclusive lock file with reload-under-lock (two processes can no
  longer overwrite each other's fills); a held lock makes the broker unavailable (nothing sent).
- MINOR: API portfolio uses the pinned starting cash and warns on mismatch; paper results always carry a
  simulated-execution warning; same-symbol working orders valued at max(own price, reference); working orders on
  unheld symbols are marked from quotes; PENDING_SUBMIT grace measured from the event; a submit outcome after a
  concurrent reconciliation becomes an anomaly, not a crash; replayed events already in an order's history are ignored
  (no false anomalies); a cancel failing with an unexpected error stays PENDING_CANCEL and reconcile resolves it from
  broker facts; the import contract forbids api/cli/research importing any broker module (enums re-exported by the
  OMS).
Tests: Python 508 passed, 1 skipped; ruff, mypy --strict, lint-imports (5 contracts) clean. A third review pass of these
fixes was started at commit time; its outcome is recorded in the next entry.
Result: PASS · Regression status: PASS

### CHANGE-0027 — Phase 5a follow-up: third safety-review findings fixed
Date: 2026-11-07 · Modules: brokers, oms, risk, core.audit, cli · Feature IDs: BRK-002, OMS-003..006, RISK-001/004
Problem: the third trading-safety review of CHANGE-0026 verified those fixes (no BLOCKER) and found 3 MAJOR + 5 MINOR
concurrency/replay issues.
Fixes (each with a regression test; the new OMS tests were confirmed to fail on the previous code):
- MAJOR M1: the paper broker's age-broken O_EXCL lock file (two writers possible) is replaced by an OS lock
  (`fcntl.flock` / `msvcrt.locking`) on a never-deleted `.lock` file, released by the OS if the holder dies; every call,
  reads and the initial save included, runs under it; quotes are fetched before the lock is taken, and quotes that
  waited longer than 2 s for the lock are not used to fill.
- MAJOR M2: broker events carry a persisted sequence number; `events()` returns `SequencedEvent`s and
  `acknowledge(through_seq)` replaces ack-by-count (two readers could drop an event neither had seen); old state files
  are upgraded on load.
- MAJOR M3: fill-driven state changes are never suppressed as replays (a late fill after a cancel is recorded and
  reported); reconcile compares against one consistent `BrokerSnapshot`; a PENDING_CANCEL younger than 60 s whose order
  is still working is reported, not resolved; terminal OMS orders still working at the broker are reported **and block
  new orders** (gate "unresolved submissions"; fails closed, also when the broker cannot be read).
- MINOR: refused cancel after a concurrent reconcile no longer crashes (`_try_event`); anomalies are audited once per
  distinct message (fill anomalies include the exec id; `core.audit.encode_details`); the TTY check is documented as
  defence in depth (KI-0068); the "reduces the position" exemption now requires no working order on the symbol, either
  side (stricter).
Review of these fixes (trading-safety-reviewer, before commit): no BLOCKER/MAJOR; M1/M2/M3/m1/m2/m5 verified, plus a
4-process stress run of the paper broker (400 orders, no lost writes). Its minors #1 (stale prefetched quotes), #2
(terminal-but-open orders not blocking) and #4 (dedupe merging distinct fills) were fixed above; #3, #5, #6 are
recorded as KI-0069..0071.
Tests: see the session handoff for the gate results of this commit. Paper tests use synthetic SIMULATED quotes only.
Result: PASS · Regression status: PASS

### CHANGE-0028 — Phase 5b-1: drawdown limit, buying power, rejection cooldown, cancel-all on kill switch
Date: 2026-11-07 · Modules: risk, oms, cli · Feature IDs: RISK-002, RISK-004, OMS-006
Problem: KI-0062 / KI-0065 — no drawdown limit, cooldown or reserved buying power; engaging the kill switch left
working orders working.
Change:
- Limits file: new **required** keys `max_drawdown_fraction`, `max_rejections_per_hour`, `allow_margin` (no implicit
  defaults: an older file now rejects every order, with a hint to the new `quantlab risk template` command);
  `allow_short = true` with `allow_margin = false` is invalid (a cash account cannot short).
- Gate: drawdown vs the high-water mark of recorded equity (daily granularity) → reduce-only; cash-account buying
  power (order + every working buy at max(own price, mark) ≤ cash; an unpriceable working buy fails); rejection
  cooldown (≥ N rejected orders created in the last hour → only position-reducing orders; unknown count → reject).
- `OMS.cancel_all(reason)` cancels every SUBMITTED/ACCEPTED/PARTIALLY_FILLED order, never sends orders, audits intent
  and per-order results, and lists everything that may still be working (refused cancels, in-flight/unknown
  submissions, broker-only orders). `quantlab risk kill-switch engage` commits the switch first, then cancels working
  paper orders by default (`--keep-orders` to skip) and exits non-zero if anything may still be working. Positions are
  never flattened automatically (a human decision).
Safety review (trading-safety-reviewer, before commit): no BLOCKER; 2 MAJOR fixed with regression tests (cooldown
blocked reducing orders and sustained itself; cancel-all could report success with orders still working); minors fixed:
unpriceable working buy fails buying power, short+cash-account combination invalid, cancel-all results audited.
Remaining minors recorded as KI-0072/0073.
Tests: Python 529 passed, 1 skipped; ruff, mypy --strict, lint-imports (5 contracts) clean. Synthetic SIMULATED quotes.
Result: PASS · Regression status: PASS

### CHANGE-0029 — Phase 5b-2: paper trading console (UI-006)
Date: 2026-11-07 · Modules: web, api, risk, bootstrap · Feature IDs: UI-006, RISK-004 · Decision: DEC-0028
Change: web pages Portfolio (`/portfolio`), Orders (`/orders`, `/orders/:id` with append-only state history, every
risk check with the limits sha256, fills labelled with their price data state) and Risk (`/risk`: kill switch,
limits, recent decisions). Engage-only kill-switch form (reason + typed phrase; no disengage control; nothing is
shown once engaged). `POST /api/risk/kill-switch/engage` (DEC-0028); `KillSwitch.engage(channel=)`; the paper OMS
creates its quote provider lazily so cancel-all works without market-data configuration (found while testing: the
first version failed to cancel when no mock dataset was configured). Module registry: Portfolio, Order Management
and Risk are now available pages; Paper Trading is CLI.
Verification: Python API tests (engage guard, no disengage/order/limits routes, cancel on engage); vitest 27 tests (6 new);
real Chromium (Playwright) against `quantlab serve` on a scratch home with synthetic data: engage button disabled
until the exact phrase, engaging cancelled the working limit order (confirmed via `quantlab paper orders`), no
controls once engaged, no console errors.
Safety review (trading-safety-reviewer, before commit): no BLOCKER; no API path disengages, orders, cancels single
orders or changes limits. 1 MAJOR fixed with a regression test (confirmed failing without the fix): a quote-source
outage other than MarketDataError (e.g. IB Gateway down) aborted cancel-all and left orders working — the paper quote
function now treats any failure as "no quote" and remembers it for that OMS, the paper broker guards each quote fetch,
and cancel-all continues past any per-order failure and always reports/audits what may still be working. Minors fixed:
reason length bounded, concurrent engage handled, clearer recovery hint in the UI. Remaining minor: the engage
endpoint test covers zero working orders only (cancel paths are covered at OMS level).
Tests: Python 533 passed, 1 skipped; web 27 passed; ruff, mypy --strict, lint-imports (5 contracts), tsc, vite build clean.
Result: PASS · Regression status: PASS

### CHANGE-0030 — Phase 5b-3: IBKR paper broker adapter (BRK-003/004)
Date: 2026-11-07 · Modules: brokers, oms, risk, core.config, bootstrap, api · Feature IDs: BRK-001, BRK-003, BRK-004 ·
Decision: DEC-0029
Change: `brokers/ibkr.py` (`IBKRPaperBroker` over the small `IBTradingClient` protocol: paper-port and paper-account
guards, orderRef mapping, duplicate check, acknowledgement timeout → SUBMIT_UNKNOWN, events from order statuses and
executions with sequence-numbered acknowledgement, executions held until their commission report, untracked orders
reported as `ib-perm:<id>`, reconnect with re-verification); `brokers/ib_async_trading.py` (thin `ib_async` client,
not yet run against a real gateway); `[paper] broker = "local" | "ibkr"` with required `ibkr_account` (DU…) and
`ibkr_starting_cash`; the OMS accepts the IBKR paper adapter; new gate check "broker reachable" (the broker's open
orders could not be read → no new orders, with an honest reason instead of "unresolved submissions").
Tests: fake-client tests for every guard and mapping plus OMS integration (fill booked from IBKR report, unreachable
gateway → rejected by the gate with nothing sent, unknown submission resolved, manual TWS order blocks trading).
Not verified against a real IB Gateway / TWS paper login (KI-0075).
Safety review: a trading-safety review of this change was still running when it was committed (the adapter is off by
default: `paper.broker = "local"`); its outcome and any fixes are recorded in the next ledger entry.
Tests: Python 553 passed, 1 skipped; ruff, mypy --strict, lint-imports (5 contracts) clean.
Result: PASS · Regression status: PASS

### CHANGE-0031 — Phase 5b-3 follow-up: IBKR adapter safety-review findings fixed
Date: 2026-11-07 · Modules: brokers, oms · Feature IDs: BRK-003, RISK-001
Review (trading-safety-reviewer, started before CHANGE-0030 was committed): no BLOCKER — no path to connect to or
trade a live account was found. 2 MAJOR + 6 MINOR; the two `ib_async` behaviours behind the MAJORs are from the
reviewer's knowledge of the library and still need confirming on a real gateway (KI-0075).
Fixes (each with a regression test, all confirmed failing on the previous code):
- MAJOR: an order IBKR cancels with an error before it was ever working is reported as REJECTED (with the IBKR
  error), so rejections count towards the cooldown and the audit trail is right; warnings (399, 21xx) are ignored.
- MAJOR: the client refreshes all open orders (`reqAllOpenOrders`) on every read, so orders placed by hand in TWS
  after QuantLab connected are seen and block new orders.
- MINOR: a client error raised before the order is handed over (unknown symbol) is a definite rejection, not
  SUBMIT_UNKNOWN; a fill is always reported before its order's final state (final states wait for the fill's
  commission); executions with an unknown side are never guessed (skipped, logged → reconciliation mismatch); any
  failure reading the broker's open orders blocks new orders ("broker reachable"); non-stock instruments are ignored
  in positions/executions; IBKR fills carry their own warning ("fills simulated by IBKR").
Remaining (KI-0074/0075): late commission reports are not applied; IBKR execution corrections are not merged;
`Inactive` is treated as final.
Tests: Python 560 passed, 1 skipped; ruff, mypy --strict, lint-imports (5 contracts) clean.
Result: PASS · Regression status: PASS

### CHANGE-0032 — Phase 5b-4: paper trading supervisor (LIVE-001)
Date: 2026-11-07 · Modules: oms, core, migrations, cli, api, web · Feature IDs: LIVE-001, RISK-002, OMS-005 ·
Decision: DEC-0030
Change: `oms/supervisor.py` (tick loop: events, scheduled reconciliation with kill-switch engagement on position
differences, equity samples during the session with start-of-day equity at the first sample of the session day, DAY
expiry after the close, status/heartbeat file, single instance); `OMS.record_equity`, `OMS.expire_day_orders`; the
drawdown peak includes equity samples (migration 0007, append-only `oms_equity_samples`); `core/filelock.py` (the
OS-level lock, moved out of the paper broker and reused); `[supervisor]` settings; `quantlab paper supervise
[--ticks N]`, `quantlab paper supervisor-status`; `GET /api/paper/supervisor`; supervisor line on the Risk page.
Tests: supervisor tests with a manual clock on the XNYS calendar (event application, reconcile schedule, heartbeat
and staleness, equity samples only in session, intraday peak drives the drawdown limit, DAY expiry once after the
close, kill switch engaged on mismatch and configurable, failures recorded without stopping, never submits, single
instance), CLI and API tests, web test. Synthetic SIMULATED quotes only.
Safety review: a trading-safety review was still running when this was committed (the supervisor only runs when
started with `quantlab paper supervise`); its outcome and any fixes are recorded in the next ledger entry.
Tests: Python 570 passed, 1 skipped; web 28 passed; ruff, mypy --strict, lint-imports (5 contracts), tsc, build clean.
Result: PASS · Regression status: PASS

### CHANGE-0033 — Phase 5b-4 follow-up: supervisor safety-review findings fixed
Date: 2026-11-07 · Modules: oms, brokers, bootstrap, core.config, web · Feature IDs: LIVE-001, OMS-005, RISK-002
Review (trading-safety-reviewer, started before CHANGE-0032 was committed): no BLOCKER — the supervisor cannot submit
orders or disengage the kill switch; the lock refactor is equivalent. 4 MAJOR + 6 MINOR.
Fixes (regression tests confirmed failing on the previous code):
- MAJOR: DAY expiry looks back over recent sessions (a close missed while the supervisor was down is caught up after
  a restart) and only expires DAY orders **created before** that close (`OMS.expire_day_orders(closed_at=)`, paper
  broker expires only those ids); the last expired session is restored from the status file after a restart.
- MAJOR: equity samples (permanent; they feed the high-water mark and start-of-day equity) use only checked quotes:
  no stale/crossed/no-price flags, bid ≤ ask, not DELAYED/FROZEN, age within `[supervisor] equity_quote_max_age_s`;
  otherwise nothing is recorded. Samples store the quote states (`supervisor:SIMULATED`), so synthetic prices stay
  labelled.
- MAJOR: a quote-source failure is retried after 30 s (and the provider rebuilt) instead of disabling quotes for the
  supervisor's lifetime.
- MAJOR: a failed reconcile sets `last_reconcile_clean = null` with the error; the status has `healthy` (no errors in
  the last tick and a clean reconcile within two intervals); the console is only green when healthy, and shows a
  warning when the status cannot be read.
- MINOR: status writes are retried and never stop the loop; an unknown calendar fails at start-up; heartbeats in the
  future are not "running"; corrupt status files are reported; lock/tmp names keep the full file name (dotted
  account names).
Remaining: KI-0076.
Tests: Python 579 passed, 1 skipped; web 28 passed; ruff, mypy --strict, lint-imports (5 contracts), tsc, build clean.
Result: PASS · Regression status: PASS

### CHANGE-0034 — Phase 5c-1: LEAN → OMS gateway service (OMS-007, server side)
Date: 2026-11-07 · Modules: oms, cli · Feature IDs: OMS-007 · Decision: DEC-0031 (DEC-0007 option A)
Environment: a .NET 10 SDK (10.0.403) was installed user-space in the session scratchpad after the owner approved
allowing `builds.dotnet.microsoft.com` in the environment (needed for 5c-2/5c-3); a system-wide apt install was
refused by the session's permission classifier and was not attempted another way.
Change: `oms/gateway.py` (protocol, auth, run-scoped idempotent keys, own-position sell cap, fills-then-states push
with replay per run, snapshot, strict input validation, per-message error containment, loopback-only bind),
`write_run_file`; `quantlab paper gateway --deployment`; `OMS.submit` reserves `lean:` keys.
Safety review (trading-safety-reviewer, before commit): 1 BLOCKER (LEAN ids restart per algorithm run: old keys
collided, new orders were silently treated as duplicates and old fills replayed against them), 2 MAJOR (client
input or DB errors could end the gateway; account-wide positions exposed and sellable), 5 MINOR — all fixed with
regression tests (restart with reused ids, conflicting replay, sell cap, slow pre-auth client, malformed numbers /
NaN / deep nesting / over-long input, reserved keys, loopback-only hosts). A second review of the fixes was started
before commit; its outcome is recorded in the next entry if it finds anything.
Tests: socket-level tests against the gateway in a thread (risk rejection and kill switch reach the algorithm,
idempotent reconnect, deployment-scoped cancel, auth refusal, snapshot), stable over repeated runs. Python 593
passed, 1 skipped; ruff, mypy --strict, lint-imports (5 contracts) clean. Synthetic SIMULATED quotes only.
Result: PASS · Regression status: PASS

### CHANGE-0035 — Phase 5c-1 follow-up: second gateway review fixed
Date: 2026-11-07 · Modules: oms, cli · Feature IDs: OMS-007 · Decision: DEC-0031 (amended)
Review (second trading-safety review, started before CHANGE-0034 was committed): the first round of fixes held; no
BLOCKER; 1 MAJOR + 11 MINOR.
Fixes (each with a regression test):
- MAJOR: unfinished orders of an earlier run of the deployment ("orphans") are listed in `welcome`/`snapshot`, block
  new orders from the new run, and can be cancelled with `cancel_orphans` (pending/unknown ones are left to
  reconciliation and reported).
- Run ids are issued by the gateway (`hello` without `run` = new algorithm start); a reconnect must present an id the
  gateway issued or one known from its orders, so a reused or invented run id can no longer revive the collision.
- Deployment orders are selected by `source = lean:<deployment>` and an exact-case key match (SQLite LIKE ignores
  case); `lean:` keys are reserved case-insensitively.
- The sell cap uses min(own, account position) and subtracts every unfinished own sell (pending/unknown included).
- Refusals are audited (`gateway.order_refused`) and remembered per run, so a stale replay stays refused.
- Error replies say `outcome: not_placed` or `unknown`; replays compare time in force too; this run's orders are
  filtered in SQL; one gateway per deployment (lock in the CLI).
Remaining (KI-0077): cash in the snapshot is the shared account's; the CLI keeps one DB session open for the quote
provider (as the supervisor does); the per-deployment lock is not covered by an automated CLI test.
Tests: Python 598 passed, 1 skipped; ruff, mypy --strict, lint-imports (5 contracts) clean; gateway tests repeated.
Result: PASS · Regression status: PASS

### CHANGE-0036 — Phase 5c-2: C# QuantLabBrokerage plugin for LEAN (OMS-007, client side)
Date: 2026-11-07 · Modules: lean (C#), oms.gateway, CI · Feature IDs: OMS-007 · Decision: DEC-0031
Change: `lean/QuantLabBrokerage` (net10.0, compile-time references to QuantConnect.Brokerages 2.5.18229 only):
`GatewayClient` (hello with gateway-issued run id, request/reply matching, reconnect re-joins the run),
`QuantLabBrokerage : Brokerage` (place/cancel through the gateway; Invalid only when QuantLab says definitely not
placed, unknown outcomes stay Submitted; fills deduplicated by exec id; no order updates; equities with market /
limit / stop / stop-limit and DAY/GTC only; holdings use the algorithm's own symbols; orphans reported and
`CancelOrphans`), `QuantLabBrokerageFactory` (`quantlab-gateway-file` config, cash-account brokerage model). Gateway:
direct replies tagged `reply: place|cancel`, snapshot positions carry the average price. CI job `lean-plugin`
(Linux + Windows) builds the plugin and runs its tests.
Tests: `lean/QuantLabBrokerage.Tests` — 10 xUnit tests against the **real** Python gateway and paper OMS
(`tests/fixtures/gateway_harness.py`, synthetic SIMULATED quotes), passed in three consecutive runs locally.
Python 598 passed, 1 skipped; ruff, mypy --strict, lint-imports clean.
Not verified: no real LEAN engine has loaded the plugin yet (needs 5c-3, native launcher); no automatic reconnect
(LEAN is told of the disconnect; `Connect` re-joins the run).
Safety review: a trading-safety review was still running when this was committed; its outcome and any fixes are
recorded in the next entry.
Result: PASS · Regression status: PASS

### CHANGE-0037 — Phase 5c-2 follow-up: plugin safety-review findings fixed
Date: 2026-11-07 · Modules: lean (C#), oms.gateway · Feature IDs: OMS-007 · Decision: DEC-0031
Review (trading-safety-reviewer, started before CHANGE-0036 was committed): no BLOCKER; the risk-gate path holds.
4 MAJOR + 9 MINOR, mostly ways LEAN's view of an order could drift from the order QuantLab works. Some points
about how the LEAN engine reacts come from the reviewer's knowledge of LEAN and are not verified on a real engine.
Fixes:
- MAJOR: `QuantLabBrokerageModel` (cash account) refuses order updates and unsupported orders, so LEAN never
  applies an update to its order object that QuantLab does not have (C# test).
- MAJOR: the gateway reports `outcome: not_placed` only for refusals before `OMS.submit`; any failure inside or
  after it is `unknown` (Python test, confirmed failing on the previous code).
- MAJOR: automatic reconnect with back-off (same run); connection generations so a finished reader of an earlier
  connection can never mark a new one dead; an intentional disconnect does not reconnect (C# tests).
- MAJOR: a late `not_placed` reply is still turned into Invalid; place requests without a reply are re-sent after a
  reconnect (idempotent within the run), so unknown outcomes resolve.
- MINOR: order events are emitted only by the reader thread, and the gateway sends direct replies after the fills and
  states it pushes, so LEAN sees fills before the final state; repeated states after a change are no longer dropped;
  send failures are `GatewayException`s (unknown outcome); the client refuses a gateway not in paper mode; fills
  without an exec id are ignored; fill timestamps are read as UTC; a sell-fill sign test was added.
Remaining (KI-0077): cash is the shared account's; holdings are read at start-up only; not yet run inside a real
LEAN engine.
Tests: C# 14 passed (against the real Python gateway); Python 599 passed, 1 skipped; ruff, mypy --strict,
lint-imports (5 contracts) clean.
Result: PASS · Regression status: PASS

### CHANGE-0038 — Phase 5c-3a: native LEAN launcher, C# strategies, first real LEAN run (ENG-003, ENG-008)
Date: 2026-11-07 · Modules: engine, analytics, cli, lean (C#), CI · Feature IDs: ENG-003, ENG-008, AN-001 ·
Decision: DEC-0032
Change: `engine/lean/native.py` (install, per-run C# compile, LeanNativeAdapter mirroring the Docker adapter,
dataset-only data folders), `lean/QuantLabLauncher` (LEAN NuGet launcher + QuantLab brokerage in one folder),
`quantlab engine native-install`, `Settings.lean_native_dir`, LEAN config builder generalised (`LeanPaths`, C#),
example `strategies/examples/buy_and_hold_cs`, metrics per DEC-0032, CI steps (Linux + Windows) that install the
native engine and run the real-LEAN golden check.
**First real LEAN runs (KI-0002):** LEAN's own BasicTemplateAlgorithm and QuantLab's C# example reproduce LEAN's
published ExpectedStatistics at tag 18229 exactly (Total Orders 1, End Equity 101691.92, Net Profit 1.692%,
CAGR 271.453%, Drawdown 2.200%, Sharpe 8.854, OrderListHash 3da9fa60…), run natively on .NET 10.0.403 in this
session, also through `quantlab backtest run` end to end (compile → run → parse → record). LEAN sample data —
provenance `sample`, not a performance claim.
**Bug found by that run:** QuantLab's cross-checks flagged CAGR (344% vs 271%) and max drawdown (1.15% vs 2.2%) as
mismatches. Root cause: daily-close metrics ignored the starting equity, the exact time span and intraday drawdowns.
Fixed (DEC-0032); the same run now matches on total return, CAGR and drawdown. QuantLab's Sharpe (4.75) still differs
from LEAN's (8.854) by definition (LEAN's risk-free rate and estimator; not a cross-checked metric; KI-0079).
Tests: unit tests for the metric definitions and the native adapter (guards, data isolation, config, compile errors);
`tests/integration/test_lean_native.py` passed against the real engine here. Python 608 passed, 2 skipped; ruff,
mypy --strict, lint-imports clean. The Docker launcher is still unverified against a real image (KI-0002 remains open
for Docker / Python algorithms).
Result: PASS · Regression status: PASS

### CHANGE-0039 — Phase 5c-3b: first real LEAN paper order through the QuantLab gateway (OMS-007 end to end)
Date: 2026-11-07 · Modules: engine (native), lean (C#), tests · Feature IDs: OMS-007, ENG-003 · Decision: DEC-0031
Change: `native.build_live_config` (LEAN live-mode configuration whose brokerage is always `QuantLabBrokerage`;
data-queue handler configurable), probe algorithm `tests/fixtures/lean_live_probe`, integration test
`test_real_lean_paper_order_goes_through_the_gateway_and_risk_gate`. Plugin fix found by the first real run: a refused
TCP connection surfaced as `AggregateException` (which the reconnect loop does not catch) instead of
`GatewayException` (C# regression test).
**Verified (this session, real LEAN 2.5.18229 natively):** LEAN in live mode loaded the QuantLab brokerage, connected
to the gateway, and a 1-share limit order went NEW → PENDING_SUBMIT → SUBMITTED → ACCEPTED → FILLED in the QuantLab
OMS with an approved risk decision (18 checks) and a paper fill at 50.01; LEAN received `Filled qty 1 @ 50.01` with
the broker's exec id and exited cleanly. All prices synthetic: gateway harness quotes, LEAN's FakeDataQueue, and —
because the market was closed — a synthetic price the probe seeds so LEAN accepts the order.
Not yet: a live data feed for LEAN deployments (prices for the algorithm come from a data-queue handler; only
FakeDataQueue was used), a `quantlab paper deploy` command, Python algorithms on the native launcher (KI-0078).
Safety review: a trading-safety review was still running when this was committed; its outcome and any fixes are
recorded in the next entry.
Tests: C# 15 passed; Python 608 passed, 3 skipped (real-engine tests run separately here: 2 passed).
Result: PASS · Regression status: PASS

### CHANGE-0040 — Phase 5c-3b follow-up: live-config safety review
Date: 2026-11-07 · Modules: engine (native), lean (C#) · Feature IDs: OMS-007
Review (trading-safety-reviewer, started before CHANGE-0039 was committed): no BLOCKER/MAJOR — no path to a real
broker or real money (brokerage hard-wired to QuantLabBrokerage, LEAN stops rather than falling back when a factory
is missing, the bin folder contains only LEAN's simulated brokerages, `build_live_config` has no production caller).
Fixed: the data-queue handler is allow-listed (FakeDataQueue only, until QuantLab's own data handler exists), and the
plugin now requires the gateway to state `mode: "paper"` explicitly. Recorded for the future launch command: check
that the config file exists (LEAN falls back to `config.json`) and pass no extra launcher arguments; label LEAN live
result files by price provenance before they are ever ingested (KI-0077).
Tests: unit test for the allow-list; C# 15 passed; Python 609 passed, 3 skipped; real-LEAN tests 2 passed.
Result: PASS · Regression status: PASS

### CHANGE-0041 — Phase 5c-4: LEAN paper deployments on QuantLab prices (`quantlab paper deploy`)
Date: 2026-11-08 · Modules: oms (gateway, deployment), lean (C#), engine (native), cli · Feature IDs: LIVE-001,
OMS-007 · Decision: DEC-0033
Change: gateway `subscribe` / `quote` messages from `OMS.quote` (the risk gate's quote source, with data state);
`QuantLabDataQueue` (LEAN `IDataQueueHandler`) sharing the brokerage's gateway connection (`GatewayClient.Shared`),
re-subscribing after reconnects, exchange-local tick times, non-LIVE data reported once per symbol;
`oms/deployment.deploy` and `quantlab paper deploy` (lifecycle stage `paper`, C#, native install, snapshot +
compile, lock, fixed LEAN argv, audited start/stop, guaranteed cleanup, Ctrl+C / `--max-minutes`); live config
allow-list extended to `QuantLabDataQueue`; test-only `force_exchange_open`.
Bug found by the first real run: ticks were stamped in UTC instead of the exchange time zone, so LEAN held them
back as future data and the algorithm never received a price (C# test now asserts exchange-local times).
**Verified (real LEAN, native, this session):** a deployment ran the probe strategy on QuantLab's (synthetic,
SIMULATED-labelled) quotes — no seeded price — and its order went through gateway, OMS and risk gate to the paper
broker and back (`Filled qty 1 @ 50.01`); start/stop audited; token file removed.
Tests: Python 613 passed, 4 skipped; C# 16 passed; real-LEAN integration 3 passed (golden backtest, live probe,
deployment). A trading-safety review was started before commit; its outcome is recorded in the next entry.
Not yet: a real-time LIVE feed run (only mock/synthetic quotes were used), a deployment registry/console view, Python
strategies on the native engine (KI-0078).
Result: PASS · Regression status: PASS

### CHANGE-0042 — Phase 5c-4 follow-up: deployment safety-review findings fixed
Date: 2026-11-08 · Modules: oms (gateway, deployment), lean (C#), cli · Feature IDs: LIVE-001, OMS-007
Review (trading-safety-reviewer, started before CHANGE-0041 was committed): no BLOCKER — no order bypasses the risk
gate, live trading cannot be enabled, the test-only exchange-open flag cannot reach production. 2 MAJOR + 7 MINOR.
Fixes (Python regression tests confirmed failing on the previous gateway):
- MAJOR: quotes for the algorithm are fetched on a separate thread and pushed after the order replies, so a slow
  provider (e.g. an IBKR snapshot on a closed market) can no longer time out order replies or delay fills.
- MAJOR: quote flags (stale / halted / crossed / …) are forwarded; flagged quotes are never used as prices by
  `QuantLabDataQueue`; every change of a symbol's data state is reported to the algorithm (not only the first);
  the data states seen are recorded in `provenance.json` (with a `synthetic` flag) and in the stop audit.
- MINOR: `deploy()` itself verifies that the snapshot matches the hash the strategy was promoted with (no
  unapproved code, no race after the CLI's check) and audits strategy, tree hash and actor; every cleanup step runs
  even after a failure or a second Ctrl+C; the real LEAN exit code is returned; a dead gateway stops the deployment;
  tick times use QuantLab's receive time; a malformed quote can no longer kill the plugin's reader.
Remaining: IBKR snapshots subscribe/unsubscribe per quote (pacing; prefer the streaming path, KI-0080).
Tests: Python 616 passed, 4 skipped; C# 17 passed; real-LEAN integration 3 passed.
Result: PASS · Regression status: PASS

### CHANGE-0043 — Phase 5c-5: deployments view; IBKR quotes from kept subscriptions
Date: 2026-11-08 · Modules: oms (deployment), api, web, data (IBKR), bootstrap · Feature IDs: LIVE-001, UI-006,
DATA-019
Change: each deployment run keeps `status.json` (state, 5 s heartbeat, strategy, approved tree hash, data states,
stop reason, exit code); `read_deployments` derives running / stopped / lost (no heartbeat). Read-only API
`GET /api/paper/deployments` and `/api/paper/deployments/{name}/{run}/log` (validated names, resolved path confined
to the deployments folder, bounded tail). Web: "Paper Trading" page lists deployments with state, code version,
price data states ("not a performance claim" unless LIVE) and the LEAN log; nothing on it starts or stops anything.
IBKR: `IBKRProvider.latest` keeps subscriptions open (the first request waits like a snapshot; idle ones are dropped
after 5 minutes; reconnects restore them) and the paper quote source prefers it (KI-0080); all quote calls are
serialised with a lock because the gateway's quote thread and the risk gate share one non-thread-safe client (an
issue introduced with CHANGE-0042's quote thread, found while designing this slice).
Verified: real LEAN deployment test now also checks the status file and listing; the Deployments page was opened in
real Chromium on that run's files (state, code hash, SIMULATED label, log with the fill line; no console errors).
Tests: Python 619 passed, 4 skipped; web 29 passed; real-LEAN integration 3 passed. A trading-safety review was
started before commit; its outcome is recorded in the next entry if it finds anything.
Result: PASS · Regression status: PASS

### CHANGE-0044 — Phase 5c-5 follow-up: quote safety-review findings fixed
Date: 2026-11-08 · Modules: data (IBKR, market_data), brokers (paper), risk (gate), oms (service, deployment),
bootstrap, api · Feature IDs: DATA-019, BRK-002, RISK-*, OMS-007, LIVE-001
Review (trading-safety-reviewer, started before CHANGE-0043 was committed): no BLOCKER, 2 MAJOR, 4 MINOR.
Fixes:
- MAJOR 1 (frozen quotes looked LIVE): `IBKRProvider.latest` flags a quote `stale` when its ticker has not updated
  for `stale_after_s` (measured against now, not the exchange time) and every quote while IB 1100 (connectivity
  lost) is pending; on 1101 it resubscribes before reading tickers. `PaperBroker` never fills a working order on a
  quote with a no-trade flag (`NO_TRADE_FLAGS`: stale, clock_skew, crossed, no_price, halted, frozen) or a real-time
  (LIVE / DELAYED / FROZEN) quote older than `max_quote_age_s` (60 s) by receipt time; SIMULATED replays carry
  historical times by design and stay governed by the gate's `max_quote_age_s`.
- MAJOR 2 (long waits under the quote lock): `latest` never reconnects or sleeps; a lost connection raises at once,
  the paper quote function closes the provider (frees the IB client id) and rebuilds it only after its 30 s
  back-off; a `MarketDataError` now backs off that symbol for 30 s; `OMS.submit` asks for the order's quote before
  opening its write transaction, so a first-subscription wait never holds the database (kill switch).
- MINOR: the risk gate rejects quotes with a no-trade flag (a tightening; a crossed quote no longer passes the spread
  check); a failed heartbeat `status.json` write is logged instead of ending the deployment; the log endpoint
  redacts `token` values; a heartbeat up to 5 s in the future (clock skew) is shown as running.
Tests (new: flagged / old quotes never fill, gate rejects flagged quotes, `latest` stale + 1100/1101, no reconnect in
`latest`, per-symbol and source back-off, log redaction, skewed heartbeat): Python 634 passed, 4 skipped; C# 17
passed; real-LEAN integration 3 passed. Web unchanged (not re-run).
Not covered by a test: the status-write guard inside the deploy loop (needs a LEAN run with a failing write).
Result: PASS · Regression status: PASS

### CHANGE-0045 — Python strategies on the native LEAN launcher (KI-0078)
Date: 2026-11-08 · Modules: engine (lean/native), lean (QuantLabLauncher), strategies (example) · Feature IDs:
ENG-003, ENG-007, ENG-009
Context: the owner asked whether QuantLab reimplements LEAN or builds on it, and whether LEAN needs a QuantConnect
login or payment. Answer: it builds on LEAN; the open-source engine needs neither (only QuantConnect's cloud, the
Lean CLI and QuantConnect data do; QuantLab uses none of them). The owner chose to keep LEAN.
Change: `lean/QuantLabLauncher` copies LEAN's Python modules (`AlgorithmImports.py`, `PandasMapper.py`, …) from the
QuantConnect.Common package next to the launcher. `native.python_runtime()` points LEAN's pythonnet at QuantLab's own
Python 3.11 (shared library, home, site-packages; user site off). Python runs execute a copy of the strategy in the
run directory; custom execution models (`-m`, quantlab_lean helper) are applied natively as on Docker, and
`collect` confirms them. Installs made earlier are told to re-run `native-install`. New example
`strategies/examples/buy_and_hold_py` (LEAN's BasicTemplateAlgorithm logic, sample data).
Verified on real LEAN (fresh `native-install`): the Python golden run reproduces LEAN's published statistics and
order list hash `3da9fa60…`; `quantlab backtest run` of the SMA example (parameters) and of the RSI template with
`-m slippage=constant -m slippage_percent=0.005` succeeded on the native launcher with the models applied
(sample data: not a performance claim).
Tests: Python 637 passed, 5 skipped; real-LEAN integration 4 passed (new Python golden test). Paper deployments stay
C# only (KI-0078).
Result: PASS · Regression status: PASS

### CHANGE-0046 — Python strategies on paper deployments (KI-0078)
Date: 2026-11-08 · Modules: oms (deployment), engine (lean/native live config), cli · Feature IDs: LIVE-001,
ENG-007, OMS-007
Change: `deploy()` and `quantlab paper deploy` accept `.py` strategies: the approved snapshot runs directly under
LEAN's pythonnet (QuantLab's Python 3.11, `python_runtime`), C# is still compiled per run; `build_live_config` takes
the algorithm language (checked). Installs without LEAN's Python modules are refused with a clear message. The
lifecycle check (stage `paper`, approved tree hash) and every other deployment guard are unchanged.
Verified on real LEAN (synthetic quotes, exchange forced open — test only): the Python probe received QuantLab's
price (no seeded value), its order passed the gateway, OMS and risk gate and was filled; start/stop audited, token
removed, provenance `SIMULATED` / synthetic. Found while checking: strategy code is not sandboxed (KI-0082).
Tests: Python 638 passed, 6 skipped; real-LEAN integration 5 passed (C# and Python
deployments, both goldens, live probe). No trading-safety subagent review this time (no order-path logic changed;
only how the strategy process is started).
Result: PASS · Regression status: PASS

### CHANGE-0047 — Pause / resume one paper deployment (DEC-0034)
Date: 2026-11-08 · Modules: oms (pausing, gateway, deployment), cli, api, web · Feature IDs: LIVE-001, UI-006
Change: new `oms/pausing.py` (state from the audit log; `pause` needs a reason; `resume` needs `RESUME DEPLOYMENT`
and a paused deployment). The gateway checks the pause on every `place` before anything is stored; a refusal is
audited and reported to LEAN as `not_placed`. `quantlab paper pause` (cancels the deployment's working orders unless
`--keep-orders`) and `quantlab paper resume` (interactive terminal only). `GET /api/paper/deployments` adds `paused`
and `pause_reason`; the Deployments page shows "paused · new orders refused" (no new controls).
Verified on real LEAN: a paused Python deployment received QuantLab's price, its order was refused with the pause
reason and nothing reached the OMS.
Tests: Python 640 passed, 6 skipped; web 29 passed; real-LEAN integration 6 passed.
Result: PASS · Regression status: PASS

### CHANGE-0048 — Strategy isolation: paper deployments without network (DEC-0035, KI-0082)
Date: 2026-11-08 · Modules: core (isolation, config), oms (gateway, deployment), lean (QuantLabBrokerage), cli, web
· Feature IDs: LIVE-001, OMS-007
Change: `core/isolation.py` (`probe`: starts a process in a user + network namespace and verifies a host loopback
listener is unreachable). `Gateway(unix_path=…)` listens on an owner-only Unix socket (removed on stop); the run file
carries `unix`; the C# `GatewayClient` connects over Unix or TCP (`Socket` instead of `TcpClient`). `deploy(isolation=)`
/ `[paper] deployment_isolation` (auto | required | off): on Linux LEAN starts under `unshare --user --map-root-user
--net`; a failed probe refuses the deployment unless "off"; elsewhere "auto" warns and records "not isolated". The
isolation result is in the start audit entry, `status.json`, `provenance.json` and the console (badge).
Verified on real LEAN (fresh `native-install` with the changed plugin): C# and Python deployments ran isolated,
traded through the Unix-socket gateway and risk gate, and the Python probe could not reach a host listener
("DEPLOY host network unreachable"); without isolation the same probe reaches it (asserted by the test's branch).
Tests: Python 643 passed, 7 skipped; C# 17 passed; web 29 passed; real-LEAN integration 6 passed.
Not covered: Windows/macOS network isolation, filesystem sandboxing (KI-0082).
Result: PASS · Regression status: PASS

### CHANGE-0049 — Health checks and alerts (OPS-001 / OPS-002)
Date: 2026-11-08 · Modules: oms (health, alerts, supervisor), core (config), bootstrap, cli, api, web · Feature IDs:
OPS-001, OPS-002
Change: `oms/health.py` — read-only checks (database, risk limits, kill switch, supervisor heartbeat / health,
deployments: lost / not isolated / paused, engine install) with ok / warn / fail and an overall status; never contacts a
broker. `quantlab health [--json] [--alert]` exits 1 on a failure (for Windows Task Scheduler / cron);
`GET /api/health/checks` (token required; the public `/api/health` ping is unchanged); Dashboard "Health" panel.
`oms/alerts.py` — alerts on status changes, recoveries and hourly reminders (a healthy first run is quiet), appended to
`logs/alerts.jsonl`, logged, and POSTed to an https webhook whose URL comes from the secret store
(`[alerts] webhook_secret`); webhook failures are logged without the URL and never fatal. `paper supervise` runs checks +
alerts after every tick (`Supervisor.run(after_tick=)`, errors never stop supervision).
Verified: CLI end to end on a fresh home (fail without limits → exit 1 and FAIL alert; `risk init` → RECOVERED, exit
0); supervisor run with alerting; Dashboard panel in real Chromium (no console errors); webhook delivery to a local
receiver.
Tests: Python 652 passed, 7 skipped; web 30 passed.
Result: PASS · Regression status: PASS

### CHANGE-0050 — Settlement guard for cash accounts (RISK-006)
Date: 2026-11-08 · Modules: risk (settlement, limits, gate), oms (service) · Feature IDs: RISK-006
Change: `risk/settlement.py` computes when sale proceeds settle (T+1 on the trading calendar of the risk limits, by the
US/Eastern trade date; weekdays when no exchange calendar is set). The OMS passes the last 14 days of sale fills (net
proceeds) to the gate; with `allow_margin = false` the "buying power" check now compares the order plus working buys
with *settled* cash and shows cash, unsettled sales and T+N. Unknown sales = rejected. New optional limit
`settlement_days` (default 1, 0 = previous behaviour; in the template). A tightening: a cash account can no longer
buy again the same day with the proceeds of a sale.
Tests: settlement dates (weekend, Thanksgiving, Eastern date boundary, no calendar), unsettled amounts, template /
older files, OMS end to end (same-day re-buy refused, next day filled), `settlement_days = 0`. Python 661 passed,
7 skipped; real-LEAN integration 6 passed.
Result: PASS · Regression status: PASS

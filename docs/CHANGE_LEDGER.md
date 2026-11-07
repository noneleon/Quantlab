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

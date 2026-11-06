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
Tests: `uv run pytest` → 77 passed (Linux). Windows confirmation: CI run after push (see handoff).
Result: PASS (Linux) · Regression status: PASS

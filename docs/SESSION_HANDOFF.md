# Session Handoff

**Last session:** 2026-11-06 · branch `claude/quantlab-self-hosted-platform-q9f3l1` · CHANGE-0005 · PR noneleon/Quantlab#1

## What was done this session
- CHANGE-0002 (step 1a): package skeleton, layered config with safety validation, structlog logging with redaction,
  QUANTLAB_HOME layout, CI (ubuntu + windows, **green**).
- CHANGE-0003 (step 1b): SQLAlchemy/Alembic metadata DB (SQLite WAL), migration 0001 `audit_log` (append-only
  triggers + ORM guard + sha256 hash chain), ULID ids + Clock, keyring secret store with env fallback.
  Doc updates for 1b landed in two commits (the first commit missed SECURITY_POLICY/PROJECT_STATUS/handoff).
- CHANGE-0004: CI run #3 was red — `secrets.py` had been gitignored and never committed. Renamed to
  `quantlab.core.secret_store`, added `tests/unit/test_repo_hygiene.py` guard.
- CHANGE-0005: Windows CI failed in Alembic (`C%3A` in SQLite URL vs ConfigParser interpolation); fixed by escaping `%`.

## Tests (actually executed)
`uv run pytest` → 77 passed (Linux, after CHANGE-0005). CI runs #11/#12 green on ubuntu + windows (Python 3.11.9 on Windows). (Linux, Py 3.11.15). ruff + mypy --strict clean.

## Key design facts to remember
- `load_settings()` never creates files; `create_db_engine()` creates the db dir; `migrate(engine)` is idempotent.
- New tables: add an Alembic revision under `src/quantlab/core/migrations/versions/` and import the model module in
  `migrations/env.py`. Never use `create_all` on real databases.
- Use `quantlab.core.ids.new_id()` for ids and a `Clock` for time — never `datetime.now()` in platform code.
- Sensitive operations → `AuditLog().record(session, actor_type=..., actor=..., action=..., target=..., details=...)`.
- Secrets only via `quantlab.core.secret_store.SecretStore().get("secret://quantlab/...")`.
- Before pushing new files, verify from a fresh clone: ruff skips gitignored files silently.

## Waiting for owner
- Run `python tools/env_check.py` on the Windows PC (KI-0001) — especially Docker daemon status, needed for 1c tests.

## Next recommended task
Phase 1 step 1c: ENG-001 EngineAdapter protocol, ENG-004 LEAN config builder, ENG-005 result parser (test against a
recorded LEAN result JSON first), ENG-002 Docker launcher + ENG-006 version probe (Docker-gated integration test),
and add import-linter contracts (core must not import engine) to CI.

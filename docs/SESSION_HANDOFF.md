# Session Handoff

**Last session:** 2026-11-06 · branch `claude/quantlab-self-hosted-platform-q9f3l1` · CHANGE-0002

## What was done
- Owner approved architecture; DEC-0002..0012 marked ACCEPTED (DEC-0007 → Option A).
- Phase 1 step 1a: `pyproject.toml`/`uv.lock` (Python 3.11), `quantlab.core.paths` (QUANTLAB_HOME per OS),
  `quantlab.core.config` (layered pydantic-settings with safety validation), `quantlab.core.logging`
  (structlog JSON + redaction, file logs under home), CI workflow for Ubuntu + Windows.

## Tests (actually executed)
`uv run pytest --cov` → 41 passed, 97 % coverage (Linux, Python 3.11.15). ruff check/format and mypy --strict clean.

## Key design facts to remember
- `load_settings(home=None, overrides=None)` never creates files. Precedence overrides > env > toml > defaults.
- Config validation rejects: `trading.mode='live'`, non-loopback host without `remote_access`, non-digest LEAN image,
  unknown keys, AI keys not given as `secret://` refs.
- `docs/` is excluded from ruff (formatter rewrote code blocks in markdown).

## Waiting for owner
- Run `python tools/env_check.py` on the Windows PC (KI-0001).
- Check first GitHub Actions run, especially the Windows leg (KI-0009).

## Next recommended task
Phase 1 step 1b: CORE-004 metadata DB (SQLAlchemy 2 + Alembic, SQLite WAL), CORE-010 append-only audit log,
CORE-006 ULID ids/clock, SEC-001 keyring secret store. Then 1c LEAN adapter (ENG-001/002/004/005/006).

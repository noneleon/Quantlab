# Session Handoff

**Last session:** 2026-11-06 · branch `claude/quantlab-self-hosted-platform-q9f3l1` · CHANGE-0006 · PR noneleon/Quantlab#1

## What was done (this session, cumulative)
- CHANGE-0002/0003: core infra (config, logging, paths, DB + migrations, audit log, ids/clock, secret store).
- CHANGE-0004/0005: fixes found by CI (gitignored `secrets.py` → `secret_store.py`; Windows `%` in Alembic URL).
- CHANGE-0006 (step 1c): `quantlab.engine` — EngineAdapter protocol, LEAN config builder, result parser,
  `LeanDockerAdapter` (probe/start/wait/timeout/cancel/collect), settings factory `create_engine_adapter`,
  import-linter contracts in CI.

## Tests (actually executed)
`uv run pytest --cov` → 127 passed, 1 skipped (real Docker e2e), 96 % coverage (Linux, Py 3.11.15).
ruff, mypy --strict, lint-imports clean. CI result for CHANGE-0006: see latest run on the PR.

## Critical honesty note
LEAN has **never actually run** under QuantLab (no Docker daemon in the dev container). ENG-002/006 are
IMPLEMENTED, ENG-004/005 TESTED against synthetic fixtures only. First real run: see TESTING_STRATEGY
"Real LEAN run" and KI-0002 / KI-0014.

## Key design facts to remember
- `create_engine_adapter(settings)` requires `engine.lean.image` pinned by digest; native launcher → ENG-003.
- Run dir `runs/<id>/` = `lean-config.json`, `docker-command.json`, `engine.log`, `results/`, `storage/`; never reused.
- Result files are `<run_id>.json` (+ `-summary.json`) because `algorithm-id` = run_id.
- `stat_value("-0.160%") == -0.0016` — use for cross-checking LEAN stats in AN-001.
- New module → add import-linter contract in `pyproject.toml`.

## Waiting for owner
- On the Windows PC: `python tools/env_check.py`, then the Docker-gated test (TESTING_STRATEGY) — this is the single
  most valuable verification step right now.

## Next recommended task
Phase 1 step 1d: STR-001..003 strategy registry + versioning (git SHA + tree hash), BT-001..003 backtest service with
reproducibility manifest (records EngineInfo, data provenance `sample`, config hash), AN-001 metrics library
cross-checked against LEAN statistics. Then 1e CLI/API.

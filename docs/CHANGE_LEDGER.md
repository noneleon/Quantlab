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

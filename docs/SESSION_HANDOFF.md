# Session Handoff

**Last session:** 2026-11-06 · branch `claude/quantlab-self-hosted-platform-q9f3l1` · CHANGE-0001

## What was done
Phase 0 audit deliverables: project memory system (CLAUDE.md, 15 docs, Claude Code commands/agent), architecture
specification, feature registry, QuantConnect parity matrix, roadmap with MVP (Milestone M1), and `tools/env_check.py`.

## Verified facts (from upstream sources, 2026-11-06)
- LEAN Apache-2.0; `net10.0`; Python via pythonnet 2.0.68 on Python 3.11.
- Lean.Brokerages.InteractiveBrokers is Apache-2.0.
- Lean CLI 1.0.233 on PyPI; has `lean login` / cloud features — QuantLab won't depend on it (DEC-0003).

## Tests
`python3 -m unittest discover -s tests -v` → **Ran 8 tests, OK** (Python 3.13.18, Linux dev container).
First run errored (test loader did not register the module in `sys.modules`, breaking `dataclasses`); fixed in the test file, not the tool.
`python3 tools/env_check.py` in dev container: Python 3.13 (not 3.11), no .NET, Docker client 29.9.2 without daemon, Git 2.43, uv 0.11.32, Node 22.23, no Lean CLI, no IB ports.

## Blocked / waiting for owner
1. Approve architecture & PROPOSED decisions DEC-0002..0012 (or request changes).
2. Decide DEC-0007 (live order path) — not needed until Phase 5.
3. Run `python tools/env_check.py` on the Windows PC and share output (KI-0001).
4. Choose initial real data source (KI-0003) — needed for Phase 2, not M1.

## Next recommended task
Phase 1 step 1a: CORE-001 package skeleton (pyproject + uv.lock, Python 3.11, ruff/mypy/pytest, CI), then CORE-002/003/005.
Read ROADMAP.md "Step order inside Phase 1" first.

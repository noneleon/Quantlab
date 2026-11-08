---
description: Load QuantLab project memory before doing any work
---
Perform the QuantLab mandatory session initialization:

1. Read `CLAUDE.md`.
2. Read, in order: `docs/PROJECT_STATUS.md`, `docs/IMPLEMENTATION_STATUS.md`, `docs/FEATURE_MATRIX.md`,
   `docs/MODULE_OWNERSHIP.md`, `docs/DECISION_LOG.md`, `docs/CHANGE_LEDGER.md`, `docs/SESSION_HANDOFF.md`.
3. Run `git status` and `git log --oneline -20`; list any uncommitted changes and treat them as user-owned.
4. Print a short briefing: current phase, last CHANGE id, open KNOWN_ISSUES, the next recommended task
   from SESSION_HANDOFF, and any decisions marked PROPOSED that still await approval.

Do not modify any files during this command. $ARGUMENTS

---
description: Finalize a QuantLab session and update project memory
---
Perform the QuantLab mandatory session finalization:

1. Review `git diff` / `git status`; confirm no unrelated files changed.
2. Append a new `CHANGE-NNNN` entry (next sequential id) to `docs/CHANGE_LEDGER.md`: module, problem,
   files modified, reason, tests executed with their REAL results, regression status.
3. Update feature statuses in `docs/IMPLEMENTATION_STATUS.md` (only promote a status with evidence).
4. Update `docs/PROJECT_STATUS.md`, `docs/KNOWN_ISSUES.md`, `docs/FEATURE_MATRIX.md` (if parity changed),
   and rewrite `docs/SESSION_HANDOFF.md` for the next session.
5. Add any new architectural decision to `docs/DECISION_LOG.md`.
6. Produce the standard development report (Task, Objective, Existing Implementation, Files Inspected,
   Changes Made, Files Modified, Architecture Impact, Tests Executed/Passed/Failed, Regression Status,
   Documentation Updated, Remaining Issues, Next Recommended Task).
$ARGUMENTS

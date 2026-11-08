---
description: Check whether a feature already exists before implementing it
---
Feature to check: $ARGUMENTS

1. Search `docs/IMPLEMENTATION_STATUS.md` and `docs/FEATURE_MATRIX.md` for matching feature IDs/keywords.
2. Grep `src/`, `web/`, `tests/`, `strategies/` for related classes, functions, endpoints, tables, config keys.
3. Search `docs/CHANGE_LEDGER.md` and `docs/KNOWN_ISSUES.md` for prior work or failed attempts.
4. Report: EXISTS (where, status, tests) / PARTIAL (gap) / ABSENT, plus the owning module from
   `docs/MODULE_OWNERSHIP.md`. Do not write code.

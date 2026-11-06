# Session Handoff

**Last session:** 2026-11-06 · branch `claude/quantlab-self-hosted-platform-q9f3l1` · CHANGE-0009 · PR noneleon/Quantlab#1

## What was done (cumulative this session)
- Phase 1 complete in code (CHANGE-0002..0008). M1 acceptance on real LEAN still pending (owner, Docker machine).
- CHANGE-0009 (Phase 2a): `quantlab.data` — provider protocol, CSV/Parquet importer, calendars, validation with quarantine,
  immutable content-addressed versions in Parquet (DuckDB reads), provenance/adjustment metadata, licence-aware export,
  `quantlab data …` CLI, `GET /api/datasets`.

## Tests (actually executed)
`uv run pytest --cov` → 248 passed, 1 skipped (real Docker), 92 % (Linux, Py 3.11.15); ruff, mypy --strict, lint-imports
clean; manual CLI smoke of the data workflow passed. CI for CHANGE-0009: see latest run on the PR.

## Key design facts to remember
- Canonical bar frame: symbol, ts (bar END, UTC), open, high, low, close, volume. Daily ts = calendar session close.
- `DataCatalog(settings.data_root)` via `AppContext.data`; `import_bars` = validate → merge with latest → new version.
- Provenance + adjustment are fixed per dataset; never default provenance to real.
- Versions: `data/parquet/<dataset>/<hash[:20]>/`; files' sha256 verified on read; never modified.
- mypy treats pandas/pyarrow/exchange_calendars/duckdb as untyped (pyproject overrides).

## Next recommended task
Phase 2b: DATA-009 LEAN-format exporter (zipped CSV per symbol/resolution, LEAN price scaling, map/factor files for raw
data) and backtests on a pinned dataset version (`BacktestService.run(..., dataset=...)` → manifest data_version and real
provenance, mounted read-only). Needs care: LEAN data format details must be verified against LEAN source before coding.
Then 2c research API + Jupyter, 2d UI shell, 2e network providers.

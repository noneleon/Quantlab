# Decision Log

States: PROPOSED (needs owner approval) · ACCEPTED · SUPERSEDED (by DEC-x) · REJECTED.
Never edit an ACCEPTED decision in place; supersede it with a new entry.

### DEC-0001 — Repository documentation is the canonical project memory
State: ACCEPTED (mandated by master prompt) · 2026-11-06
Context: AI sessions lose context. Decision: CLAUDE.md + docs/* are authoritative; feature status lives only in
IMPLEMENTATION_STATUS.md. Consequence: every session starts/ends with the documented procedure.

### DEC-0002 — Modular monolith in Python 3.11
State: PROPOSED · 2026-11-06
Decision: one backend process (FastAPI) with internal modules + separate LEAN worker processes; Python pinned to
3.11 to match LEAN's embedded pythonnet interpreter so strategy code, research and platform share one runtime.
Alternatives: microservices (rejected: operational overhead for a single-user platform); Python 3.13 (rejected:
LEAN algorithms run on 3.11, shared libraries must be 3.11-compatible).

### DEC-0003 — No dependency on Lean CLI / QuantConnect account
State: PROPOSED · 2026-11-06
Decision: QuantLab launches LEAN directly (Docker image or native build) with its own generated config. Lean CLI may
be used manually but is never required. Reason: CLI workflows assume `lean login`; charter forbids mandatory QC cloud.

### DEC-0004 — EngineAdapter abstraction; LEAN Docker launcher default, native optional
State: PROPOSED · 2026-11-06
Decision: services depend on `EngineAdapter`. Default launcher runs `quantconnect/lean` pinned **by digest**
(reproducibility). Native .NET 10 launcher for Windows users without Docker (Phase 3).
Risk: Docker Desktop licensing (free for personal/small business) and WSL2 requirement on Windows.

### DEC-0005 — QuantLab-owned data catalog (Parquet + DuckDB) with deterministic LEAN export
State: PROPOSED · 2026-11-06
Decision: canonical data in Parquet; immutable content-hashed dataset versions; LEAN reads a per-version export.
Research and backtests read the same versions. Raw prices + factor files stored (point-in-time).
Alternative: store only LEAN format (rejected: poor analytics, no versioning, LEAN-format lock-in).

### DEC-0006 — Strategies are user-owned Git directories
State: PROPOSED · 2026-11-06
Decision: strategy = directory with LEAN entry point + metadata; version = Git SHA + tree hash; platform code never
contains strategy logic.

### DEC-0007 — Live/paper orders flow through QuantLab OMS + risk gate (gateway brokerage)
State: PROPOSED — **explicit owner decision required** · 2026-11-06
Options and trade-offs: ARCHITECTURE.md §6. Recommendation: Option A (custom LEAN brokerage plugin forwarding to
QuantLab OMS). Blocks OMS-007.

### DEC-0008 — Broker-confirmed fills are the only source of position truth
State: PROPOSED · 2026-11-06
Decision: confirmed book updated only from execution reports; theoretical book separate; reconciliation on start,
reconnect and periodically. Orders in SUBMIT_UNKNOWN are never auto-resubmitted.

### DEC-0009 — SQLite (WAL) default metadata store, PostgreSQL optional
State: PROPOSED · 2026-11-06
Decision: SQLAlchemy 2 + Alembic; append-only tables for runs, orders, fills, risk decisions, audit.

### DEC-0010 — Paper trading default; live behind gates
State: ACCEPTED (mandated by master prompt) · 2026-11-06
Decision: live requires explicit config, separate credentials, risk limits, broker verification, typed user
confirmation, persistent visible indicator. AI cannot enable it.

### DEC-0011 — AI via provider abstraction and permissioned tool gateway
State: PROPOSED · 2026-11-06
Decision: AI tools are allowlisted read/propose capabilities; no trading, credential, risk-write or deletion tools;
enforced by import contracts and gateway allowlist; every call audited.

### DEC-0012 — Backend Python lockfiles via `uv`
State: PROPOSED · 2026-11-06
Decision: `pyproject.toml` + `uv.lock`; UI via `pnpm-lock.yaml`.

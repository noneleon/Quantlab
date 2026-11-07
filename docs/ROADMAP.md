# Roadmap

Incremental, independently testable phases. Each phase ends with a demo-able vertical slice and updated memory docs.
Feature IDs → [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

| Phase | Deliverable | Key features | Exit criteria |
|---|---|---|---|
| 0 Audit | Architecture spec + roadmap + memory system | MEM-001..003 | Owner approves architecture & DEC-0002..0012 |
| 1 Core infra + **M1** | Working local foundation and first reproducible LEAN backtest | CORE-001..010, ENG-001,002,004..007, STR-001..003, BT-001..004, AN-001, SEC-001,003, MEM-004 | M1 acceptance tests pass on owner's Windows PC |
| 2 Data & research | Local research environment on versioned real data | DATA-001..011,016, RES-001..005, UI-001,004,008 | Ingest → validate → version → query in Jupyter → export to LEAN |
| 2f Market-data migration | Tiingo (dev) → IBKR (prod) by configuration (DEC-0021) | A: DATA-018/020/021/022 + Tiingo minute (done) · B: DATA-019 IBKR market data (done, fake-client tested) · C (Phase 5): IBKR BrokerAdapter + OMS + risk + paper | Same strategy, compatible results on Tiingo vs IBKR data (`data compare`, `backtest compare`) |
| 3 Backtesting depth | Realistic, reportable backtests + UI (DEC-0022: 3a analytics/report/UI done · 3b helper lib + models + templates done (stub-tested) · 3c editor + golden suite done · native + C# pending (.NET)) | ENG-003,008..013, BT-005..008, AN-002,003, UI-002,003, STR-005 | Regression suite with golden results; downloadable report |
| 4 Optimization | Professional evaluation (DEC-0025: 4a engine/statistics done · 4b UI + Optuna + comparison · 4c lifecycle + promotion) | OPT-001..006, AN-004, STR-004, RES-006, UI-005 | Walk-forward with IS/OOS separation; robustness report |
| 5 Portfolio & paper | Paper trading via OMS + risk | PORT-001..004, OMS-001..007, BRK-001..005, RISK-001..006, LIVE-001,002, DATA-012,013,017, OPS-001,002, SEC-002, UI-006 | IBKR paper: execution tests incl. disconnect/restart/reconcile pass |
| 6 Live | Controlled live environment | LIVE-003,004, OPS-003 | All safety gates verified; owner explicitly activates |
| 7 Advanced | Multi-strategy, derivatives, ML, advanced risk | BT-009, PORT-005, DATA-014,015, ML-001..006, RISK-007, BRK-006 | per-feature |
| 8 AI | AI-native development | AI-001..005, SEC-005, UI-007 | Gateway permission tests pass |
| 9 Hardening | Production-ready self-hosting | OPS-004,005, SEC-004, perf, docs | Backup/restore drill, security audit |

## Milestone M1 — "First reproducible local LEAN backtest" (the MVP)

Status (CHANGE-0008): all six steps implemented and tested end to end against a **fake** engine
(`tests/unit/cli/test_cli.py::test_m1_flow`). **Acceptance pending:** the same flow with real LEAN on a Docker machine (KI-0002).
API token is stored in the OS keyring by `quantlab init`.
A thin but **real** end-to-end slice, no placeholders:

1. `quantlab init` creates `QUANTLAB_HOME`, SQLite DB (Alembic migration), config, local API token.
2. `quantlab strategy add ./strategies/sma_crossover` registers a Python LEAN strategy; version = git SHA + tree hash.
3. `quantlab backtest run sma_crossover --start 2013-10-07 --end 2013-10-11` using LEAN's bundled **sample** data
   (labelled `sample`), runs LEAN via Docker (image pinned by digest), streams logs.
4. Results parsed (orders, equity, LEAN statistics); QuantLab metrics (AN-001) computed and cross-checked against LEAN's.
5. Immutable `backtest_runs` row + manifest + artifacts; `quantlab backtest rerun <id>` reproduces identical stats and reports a diff.
6. `GET /api/backtests`, `GET /api/backtests/{id}` (token-protected, localhost) return the run.

Acceptance tests: unit (config, manifest hashing, metrics vs hand-computed values, result parser on recorded LEAN output),
integration (Docker-gated full run + rerun equality). No UI in M1 (UI starts in Phase 2 on top of the same API).

Why this first: it validates the riskiest integration (LEAN launch, data mount, result parsing, reproducibility) before
building data, UI or trading layers on top of it.

## Step order inside Phase 1
1a. CORE-001/002/003/005 package, config, logging, paths (+ CI MEM-004)
1b. CORE-004/010 DB + migrations + audit · SEC-001 keyring
1c. ENG-001/002/004/005/006 LEAN adapter (recorded-output tests first, Docker test second)
1d. STR-001..003 strategy registry · BT-001..003 service + manifest · AN-001 metrics
1e. CORE-008/009 CLI + API · SEC-003

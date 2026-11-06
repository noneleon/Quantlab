# Module Ownership & Boundaries

Each responsibility has exactly one owning module. If you need a capability, use the owner's public interface —
do not re-implement it elsewhere. Dependency rules: see ARCHITECTURE.md §7.

| Module | Path | Owns (single source of truth for) | Must NOT |
|---|---|---|---|
| core | `src/quantlab/core/` | config, logging/redaction, DB session & migrations, ids, clock, event bus, audit log, secret store access | contain domain logic |
| engine | `src/quantlab/engine/` | EngineAdapter, LEAN launch/config/result parsing, engine version | compute metrics; touch broker |
| data | `src/quantlab/data/` | providers, catalog, storage, validation, calendars, corporate actions, dataset versions, LEAN export | be bypassed by research/backtests reading raw files |
| research | `src/quantlab/research/` | notebook helpers, research API over data catalog | reimplement indicators (reuse LEAN/shared) |
| strategies | `src/quantlab/strategies/` | strategy registry, versioning, parameters, lifecycle state | contain strategy logic in platform code |
| backtest | `src/quantlab/backtest/` | backtest runs, manifests, immutable history | simulate trades itself (LEAN does) |
| analytics | `src/quantlab/analytics/` | all performance metrics & report datasets | be duplicated in UI or optimize |
| optimize | `src/quantlab/optimize/` | searches, walk-forward, Monte Carlo, robustness | bypass BacktestService |
| portfolio | `src/quantlab/portfolio/` | books, positions, P&L, exposure, sizing models | update positions from submissions |
| oms | `src/quantlab/oms/` | orders, state machine, idempotency, order/fill event store | send orders without RISK-001 |
| risk | `src/quantlab/risk/` | risk rules, limits, decisions log, kill switch | be configurable by strategies or AI |
| brokers | `src/quantlab/brokers/` | broker adapters, connection mgmt, reconciliation data | import oms |
| live | `src/quantlab/live/` | deployment supervisor, recovery, live gates | resubmit orders after restart without reconciliation |
| ml | `src/quantlab/ml/` | features, labels, CV splitters, model registry, drift | leak test data into training |
| ai | `src/quantlab/ai/` | providers, context retrieval, tool gateway, AI audit | import write APIs of oms/risk/brokers/live/secrets |
| ops | `src/quantlab/ops/` | health, alerts, supervision, backup/restore | — |
| api / cli | `src/quantlab/api/`, `src/quantlab/cli/` | transport only | contain business logic |
| web | `web/` | UI | call anything but the API; show controls without backend |
| docs | `docs/`, `CLAUDE.md` | project memory | — |
| tools | `tools/` | developer utilities | be imported by runtime code |

Current state (2026-11-06, CHANGE-0006): `core`, `engine`, `docs`, `.claude`, `tools` exist. Boundaries enforced by
import-linter contracts in `pyproject.toml` (`uv run lint-imports`); add a contract when a module is created.

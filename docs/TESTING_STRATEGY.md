# Testing Strategy

Rule: **a test is "passed" only if it was executed and the output was observed.** Record real counts in
CHANGE_LEDGER/SESSION_HANDOFF.

## Test layers
| Layer | Location | Scope | Runs |
|---|---|---|---|
| Unit | `tests/unit/` | pure functions/classes (metrics, state machine, validators, config) | every commit |
| Integration | `tests/integration/` | module interaction: DB, catalog, BacktestService ↔ LEAN adapter (Docker-gated) | every commit (Docker tests skip with explicit reason if no daemon) |
| Contract | `tests/contract/` | BrokerAdapter, DataProvider, EngineAdapter suites every implementation must pass | every commit (live-broker cases opt-in) |
| Backtest regression | `tests/regression/` | pinned strategy + pinned dataset version + pinned engine digest → golden statistics/orders | every commit touching engine/data/backtest |
| Execution | `tests/contract/brokers/`, `tests/integration/oms/` | submit, partial fill, reject, cancel, duplicate prevention, disconnect/reconnect, restart recovery | every commit (simulated broker); IBKR paper opt-in |
| Risk | `tests/unit/risk/`, property tests | limits cannot be bypassed (hypothesis-generated order streams), kill switch, rejection logging | every commit |
| Security | `tests/security/` | no secrets in logs (redaction), API auth required, AI gateway allowlist, import-linter contracts | every commit |
| E2E | `tests/e2e/` (Playwright) | UI workflows: create strategy → backtest → view report | pre-release |

## Data used in tests
- Unit/integration: small **synthetic** fixtures, labelled `provenance=synthetic`; never shown as real results.
- Regression: LEAN sample data (labelled `sample`) or owner-provided pinned datasets stored outside Git.

## Tooling (Phase 1)
pytest, pytest-cov, hypothesis, import-linter, ruff, mypy (strict on core/oms/risk), Playwright for UI.
Until Phase 1 lands, tests use stdlib `unittest`: `python -m unittest discover -s tests -v`.

## Gates
- Coverage targets: oms/risk/portfolio ≥ 90 % branch; others ≥ 75 %.
- Any change under `oms/`, `risk/`, `brokers/`, `live/` must be reviewed by the `trading-safety-reviewer` agent.

# Project Charter — QuantLab

**Owner:** repository owner (single user, self-hosted). **Started:** 2026-11-06.

## Mission
Build a professional-grade, fully self-hosted quantitative research, backtesting, optimization, paper and live
trading platform around the open-source QuantConnect LEAN engine, with all code, data, results, credentials and
infrastructure under the owner's control.

## In scope
Research environment · strategy management · reproducible event-driven backtesting (LEAN) · optimization and
walk-forward/robustness analysis · portfolio management · OMS with formal state machine · centralized risk engine ·
IBKR paper then live trading · ML research & deployment · AI-native development tools · professional web UI ·
monitoring, recovery, backup · documentation and persistent project memory.

## Out of scope (until explicitly approved)
Mandatory cloud services · dependency on QuantConnect Cloud or `lean login` · redistribution of licensed data ·
multi-tenant SaaS · high-frequency / co-located trading · autonomous AI trading.

## Principles (summary — full text in CLAUDE.md)
Local-first, Windows-first but portable · reuse LEAN, don't rewrite it · paper by default · broker-confirmed state
only · non-bypassable risk gate · reproducibility of every backtest · no fabricated data in reports ·
no fake completion · minimal diffs · repository docs are the memory.

## Success criteria for v1.0
1. A strategy can go Research → Backtest → Optimize → Paper on the owner's Windows PC with no cloud account.
2. Any historical backtest can be re-run from its manifest and reproduce identical statistics.
3. Paper trading through IBKR paper account with reconciliation, risk gate, kill switch, crash recovery verified.
4. Live trading possible only after all LIVE-003 gates pass and the owner explicitly activates it.

## Governance
- Architectural decisions recorded in DECISION_LOG.md; PROPOSED decisions need owner approval.
- AI agents operate under CLAUDE.md §4 hard limits.

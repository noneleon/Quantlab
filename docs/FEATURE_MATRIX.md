# QuantConnect → QuantLab Feature Parity Matrix

Purpose: show, per QuantConnect capability, **how** QuantLab will provide it and **what limits it**.
Status lives only in [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) (referenced by ID). QuantLab does
**not** claim parity for any row until the referenced features are `VERIFIED`.

Approach: **L** LEAN provides it (open source, local) · **LI** LEAN + QuantLab integration layer ·
**C** custom QuantLab development · **X** requires external licensed data/service (QuantLab provides adapter) ·
**N/A** depends on proprietary QuantConnect Cloud infrastructure; not reproducible as-is.

Last reviewed: 2026-11-07

## 1. Engine & algorithm API
| QuantConnect capability | Appr. | QuantLab feature IDs | Notes / limitations |
|---|---|---|---|
| Event-driven backtesting engine | L | ENG-002, BT-004 | Same LEAN engine; results should match QC for identical data. |
| Python algorithms | L | ENG-007 | Python 3.11 via pythonnet; only packages present in the LEAN image/env. |
| C# algorithms | L | ENG-008 | Compiled inside LEAN container or native build. |
| Algorithm Framework (Universe/Alpha/PCM/Execution/Risk) | L | ENG-010 | Exposed unchanged. |
| 100+ indicators, consolidators, scheduled events | L | ENG-011, ENG-012 | |
| Custom data (PythonData/BaseData) | LI | ENG-013, DATA-010 | |
| Brokerage/fee/fill/slippage/margin/buying-power models | L | BT-005 | `[backtest.models]` / `-m`, applied by the opt-in `quantlab_lean` helper (ENG-009); recorded per run. |
| Settlement & cash accounts | L | BT-007, RISK-006 | LEAN source: US equity cash accounts settle T+1 (06:00) by default; QuantLab can force immediate/delayed. |
| Multi-algorithm portfolio backtests | C | BT-009 | LEAN is one-algorithm-per-process; QuantLab combines books. |

## 2. Data
| QuantConnect capability | Appr. | QuantLab feature IDs | Notes / limitations |
|---|---|---|---|
| QC Data Library (US equities tick→daily, options, futures, FX, crypto, alt data) | X / N/A | DATA-011..015 | **Proprietary & paid.** QuantLab cannot ship it. Users may buy QC data for local use under QC's terms or use other vendors. |
| LEAN sample data | L | DATA-009 | Small, labelled `sample`, never used for real performance claims. |
| Corporate actions (map/factor files) | LI / X | DATA-008 | QuantLab builds factor files from vendor split/dividend data; accuracy = vendor accuracy. |
| Survivorship-bias-free universes, delisted symbols | X | DATA-014 | Generally requires paid data (e.g. Norgate, CRSP, QC library). Free sources are survivorship-biased — reports must say so. |
| Fundamentals (Morningstar) | X / N/A | — (not yet registered) | Licensed; adapter only if user has a source. |
| Historical data download | X | DATA-011 | Tiingo EOD (daily, raw + dividends/splits) and IEX minute bars; personal-use licence. IBKR (DATA-019) implemented, not yet verified against a real Gateway. |
| Data-source switching (dev → prod) | C | DATA-018, DATA-022 | `[market_data]` config; quotes always labelled LIVE/DELAYED/FROZEN/SIMULATED. |
| Live data feeds | X | DATA-013 | IBKR (subscription) first; crypto exchange public feeds possible. |
| Data quality / validation tooling | C | DATA-005, DATA-006 | QuantLab addition. |
| Dataset versioning | C | DATA-004 | QuantLab addition (QC has no user-visible data versioning). |

## 3. Research
| QuantConnect capability | Appr. | QuantLab feature IDs | Notes |
|---|---|---|---|
| Jupyter research with QuantBook | L / LI | RES-001, RES-003 | Local JupyterLab; `quantconnect/research` image optional. |
| Research → algorithm promotion | C | RES-006 | |
| Statistical/factor analysis helpers | C | RES-004 | |

## 4. Backtest results & analytics
| QuantConnect capability | Appr. | QuantLab feature IDs | Notes |
|---|---|---|---|
| Backtest statistics & charts | LI / C | ENG-005, AN-001, AN-002 | LEAN statistics parsed + independent QuantLab metrics, cross-checked. |
| Backtest reports (HTML; PDF via print) | C | AN-003 | Self-contained HTML + reproducibility bundle; benchmark comparison (BT-008). LEAN `Report` project not used. |
| Backtest history | C | BT-003 | Immutable + reproducibility manifest (stronger than QC). |

## 5. Optimization
| QuantConnect capability | Appr. | QuantLab feature IDs | Notes |
|---|---|---|---|
| Parameter optimization (grid, Euler) | LI / C | OPT-001 | LEAN Optimizer project exists; QuantLab may orchestrate trials itself for richer IS/OOS tracking. |
| Walk-forward, Monte Carlo, bootstrap, robustness | C | OPT-003..005 | Not provided by QC; QuantLab addition. |
| Bayesian / multi-objective | C | OPT-002 | Optuna. |

## 6. Live & paper trading
| QuantConnect capability | Appr. | QuantLab feature IDs | Notes / limitations |
|---|---|---|---|
| QC paper trading | C | BRK-002, LIVE-002 | Local simulated broker + IBKR paper account. |
| Interactive Brokers live | LI / X | BRK-003, OMS-007 | Needs IBKR account, TWS/IB Gateway running locally, data subscriptions. LEAN's IB brokerage is Apache-2.0. |
| Other brokers (Alpaca, Tradier, Binance, Coinbase, …) | LI / X | BRK-006 | LEAN brokerage plugins exist for many; each needs verification of license & local build. |
| Cloud-hosted live nodes, 24/7 co-located servers | N/A | OPS-003 | Replaced by user's own machine/server + process supervision. |
| Live notifications (email/SMS/webhook) | C | OPS-002 | SMS needs a provider. |
| Centralized risk gate, kill switch, reconciliation | C | RISK-001..004, BRK-005 | QuantLab addition; stricter than QC. |

## 7. Platform
| QuantConnect capability | Appr. | QuantLab feature IDs | Notes |
|---|---|---|---|
| Web IDE, project management | C | UI-003, STR-001 | Monaco-based; strategies in user Git. |
| Collaboration / multi-user orgs | N/A (later C) | — | Out of scope for MVP; single-owner platform. |
| Object Store | C | — (not yet registered) | Local filesystem/DB equivalent; register when needed. |
| Mia / AI assistant | C | AI-001..005 | Provider-independent; local Ollama option. |
| Alpha Streams / marketplace | N/A | — | Not applicable to a self-hosted platform. |
| Cloud compute nodes for backtests | N/A → C | CORE-007 | Local process pool; optional remote worker later. |

## 8. Summary
- **Directly from LEAN (L):** engine, algorithm API, framework models, indicators, order types in simulation, brokerage/fee/fill models, IB brokerage plugin.
- **Integration required (LI):** launching LEAN, config, results parsing, data export to LEAN format, research QuantBook, live deployment.
- **Custom (C):** everything platform-level: data catalog/versioning/validation, strategy registry/versioning, reproducibility, analytics & reports, optimization beyond grid, portfolio/OMS/risk/reconciliation, UI, AI, ops, security.
- **Hard external limits (X/N/A):** QC Data Library, fundamentals, survivorship-free data, live data subscriptions, QC cloud infrastructure.

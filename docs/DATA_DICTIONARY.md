# Data Dictionary

Status: **DRAFT** — schemas are proposals until implemented (Phase 1+). Update when tables/columns change and
reference the CHANGE id. All timestamps UTC (`timestamp[us, UTC]`), money as decimal strings in DB / float64 in analytics.

## 1. Market data (Parquet, `QUANTLAB_HOME/data/parquet/`)
Partitioning: `asset_class=/resolution=/symbol=/year=`.

### bars
| Column | Type | Description |
|---|---|---|
| ts | timestamp UTC | bar **end** time (LEAN convention) |
| symbol_id | string | QuantLab canonical id (maps to LEAN SecurityIdentifier) |
| open, high, low, close | float64 | **raw, unadjusted** prices |
| volume | float64 | shares/contracts/units |
| bid_close, ask_close | float64? | optional quote data |
| source | string | provider id |
| ingest_id | string | ingestion batch ULID |

### corporate_actions
| Column | Type | Description |
|---|---|---|
| symbol_id, ex_date | string, date | |
| kind | enum | split, dividend, symbol_change, delisting |
| ratio / amount | float64 | split ratio or cash dividend |
| source | string | |

### dataset_versions (metadata DB)
| Column | Type | Description |
|---|---|---|
| data_version | string | content hash (sha256 of sorted partition hashes + manifest) |
| created_at | timestamp | |
| provenance | enum | real_historical, live_recorded, synthetic, sample |
| completeness | json | gap/duplicate/validation report summary |
| license | string | provider licence terms id; redistribution flag |
| symbols, start, end, resolution | | coverage |
| lean_export_path | string? | path of LEAN-format export |

## 2. Metadata DB (SQLite/Postgres) — proposed tables
| Table | Purpose | Mutability |
|---|---|---|
| strategies | registry (id, name, path, language, lifecycle_state, archived) | mutable (lifecycle) |
| strategy_versions | (strategy_id, git_sha, tree_hash, created_at) | append-only |
| backtest_runs | run_id, strategy_version, data_version, engine_info, config_json, seeds, status, stats_json, artifacts_path | append-only (status updates until terminal) |
| optimization_runs / trials | search spec, trial params, run_id, IS/OOS label, objective values | append-only |
| accounts | broker account refs (no secrets), mode paper/live | mutable |
| orders | order_id, client_order_key (UNIQUE), account, strategy run, spec, current_state | projection |
| order_events | order_id, seq, ts, event_type, payload, broker_ref | append-only |
| fills | fill_id, order_id, broker_exec_id (UNIQUE), qty, price, commission, ts | append-only |
| positions_confirmed / positions_theoretical | per account/strategy | projections |
| risk_decisions | ts, order_id, rule_id, decision, reason, snapshot | append-only |
| risk_limits | versioned limit sets, approved_by, approved_at | append-only versions |
| audit_log | ts, actor (user/ai/system), action, target, details | append-only |
| ml_models | model_id, version, dataset_versions, metrics, artifact_path | append-only |

# Data Dictionary

Status: sections marked IMPLEMENTED are authoritative; others are proposals. Update when tables/columns change and
reference the CHANGE id. All timestamps UTC (`timestamp[us, UTC]`), money as decimal strings in DB / float64 in analytics.

## 1. Market data (IMPLEMENTED, CHANGE-0009)
Files: `QUANTLAB_HOME/data/parquet/<dataset>/<content_hash[:20]>/<SYMBOL>.parquet` (zstd), one immutable directory per
version. Quarantined batches: `QUANTLAB_HOME/data/quarantine/<ingest_id>/{batch.csv,report.json}`.

### bars (canonical frame / Parquet schema)
| Column | Type | Description |
|---|---|---|
| symbol | string | ticker as used by the market, upper case (e.g. `SPY`) |
| ts | timestamp[us, UTC] | bar **end** time (LEAN convention); daily bars end at the calendar's session close |
| open, high, low, close | float64 | prices as declared by the dataset's `price_adjustment` (normally `raw`) |
| volume | float64 | NaN when the source has no volume |

Content hash per symbol = sha256 over `ts|repr(open)|repr(high)|repr(low)|repr(close)|repr(volume)` lines sorted by ts;
version hash = sha256(dataset identity + sorted symbol hashes). Independent of Parquet bytes and OS.

### datasets (metadata DB, migration 0003)
| Column | Description |
|---|---|
| id, name (UNIQUE) | ULID, lowercase name |
| asset_class, market, resolution, calendar | e.g. equity / usa / daily / XNYS |
| provenance | real_historical · live_recorded · synthetic · sample (fixed at creation) |
| price_adjustment | raw · split_adjusted · split_dividend_adjusted (fixed at creation) |
| source, license_terms, redistribution_allowed, license_url, description, created_at | |
Never deleted (trigger).

### dataset_versions (metadata DB, migration 0003)
| Column | Description |
|---|---|
| id, dataset_id, parent_id | ULID; parent = previous version merged into this one |
| content_hash | version hash (UNIQUE per dataset) |
| ingest_id, source, conflict_policy | import batch, provider source, `reject`/`replace` |
| row_count, start_ts, end_ts | coverage |
| symbols | JSON {symbol: content hash} |
| files | JSON {file: sha256} — verified on every read |
| validation | JSON ValidationReport of the merged data (issues, completeness per symbol) |
| notes | JSON list (timestamp mapping, replaced rows, ignored adjusted-close column, …) |
| path | version directory |
Append-only (triggers).

### LEAN export (IMPLEMENTED, DATA-009)
`QUANTLAB_HOME/data/lean/<dataset>/<version_hash[:20]>/` (immutable, verified on reuse):
`equity/<market>/{daily,hour}/<sym>.zip` (→ `<sym>.csv`, `yyyyMMdd HH:mm` bar start, exchange time),
`equity/<market>/{minute,second}/<sym>/<yyyyMMdd>_trade.zip` (→ `<yyyyMMdd>_<sym>_<res>_trade.csv`, ms since midnight),
prices × 10 000 as integers, integer volume; `map_files/<sym>.csv` and neutral `factor_files/<sym>.csv`;
`quantlab-lean-export.json` (dataset identity, version, per-file sha256, export hash).

### corporate actions (IMPLEMENTED, DATA-008)
Stored per dataset version as `_corporate_actions.parquet` (hashed in `files`, included in the version hash only when
non-empty, so versions without actions keep their hashes).
| Column | Type | Description |
|---|---|---|
| symbol | string | ticker |
| ex_date | date | exchange-local ex-date |
| kind | enum | `split`, `dividend` (symbol changes / delistings planned) |
| value | float64 | split: new shares per old (4.0 = 4:1); dividend: cash per share, raw terms |
LEAN factor rows and read-time adjustment both come from `corporate_actions.factor_file_rows` (golden-tested against
LEAN's AAPL factor file).

## 2. Metadata DB (SQLite/Postgres) — proposed tables
| Table | Purpose | Mutability |
|---|---|---|
| datasets / dataset_versions | **IMPLEMENTED (0003)** — see §1 | datasets never deleted; versions append-only |
| strategies | **IMPLEMENTED (0002)**: id ULID, name UNIQUE slug, path, entry_file, language, description, archived, created_at | mutable (archive only); DELETE rejected by trigger |
| strategy_versions | **IMPLEMENTED (0002)**: id, strategy_id FK, tree_hash (UNIQUE per strategy), files JSON {path: sha256}, git_commit, git_dirty, created_at | append-only (triggers) |
| backtest_runs | **IMPLEMENTED (0002)**: id, strategy_id, strategy_version_id, rerun_of, status (running/succeeded/algorithm_error/failed/timed_out/cancelled/engine_error), created_at, finished_at, parameters, data_provenance, data_version, engine_info, inputs_hash, result_hash, engine_status, runtime_error, error, engine_statistics, metrics, cross_checks, manifest, run_dir | updates only while `running`; terminal rows immutable, DELETE rejected (triggers + ORM guard) |
| optimization_runs / trials | search spec, trial params, run_id, IS/OOS label, objective values | append-only |
| accounts | broker account refs (no secrets), mode paper/live | mutable |
| orders | order_id, client_order_key (UNIQUE), account, strategy run, spec, current_state | projection |
| order_amendments | **IMPLEMENTED (0008)**: id, order_id FK, amendment (groups one modification), status requested/applied/refused/rejected/unknown, quantity (new total), limit_price, stop_price, trail_amount, detail JSON, created_at. `orders` gained trail_amount / trail_percent (0008). Current terms = order overlaid by the last applied amendment | append-only (triggers + ORM guard) |
| order_events | order_id, seq, ts, event_type, payload, broker_ref | append-only |
| fills | fill_id, order_id, broker_exec_id (UNIQUE), qty, price, commission, ts | append-only |
| positions_confirmed / positions_theoretical | per account/strategy | projections |
| risk_decisions | ts, order_id, rule_id, decision, reason, snapshot | append-only |
| risk_limits | versioned limit sets, approved_by, approved_at | append-only versions |
| audit_log | **IMPLEMENTED (migration 0001)**: seq PK, id ULID UNIQUE, ts UTC, actor_type CHECK(user/ai/system), actor, action (indexed), target, details (redacted JSON text), prev_hash, hash UNIQUE (sha256 chain) | append-only, enforced by triggers + ORM guard |
| ml_models | model_id, version, dataset_versions, metrics, artifact_path | append-only |

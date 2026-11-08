# Configuration Reference

Status: **IMPLEMENTED** for the keys below (CORE-002, `src/quantlab/core/config.py`). Unknown keys are rejected. Precedence (low → high):
built-in defaults → `QUANTLAB_HOME/quantlab.toml` → environment variables `QUANTLAB_<SECTION>__<KEY>` → per-run overrides.
**Secrets are never stored in config files** — only references to keyring entries (`secret://quantlab/<name>`).

| Key | Default | Description |
|---|---|---|
| `QUANTLAB_HOME` (env) | Win: `%LOCALAPPDATA%\QuantLab`; Linux: `~/.local/share/quantlab` | runtime data root |
| `server.host` | `127.0.0.1` | API bind address; non-loopback requires `server.remote_access=true` |
| `server.port` | `8765` | |
| `server.remote_access` | `false` | |
| `db.url` | `sqlite:///{home}/db/quantlab.sqlite` | SQLAlchemy URL |
| `engine.default` | `lean` | EngineAdapter id |
| `engine.lean.launcher` | `docker` | `docker` or `native` |
| `engine.lean.image` | unset | must be `<repo>@sha256:<64 hex>`; tags rejected |
| `engine.lean.native_path` | — | path to local LEAN Launcher build (launcher `native` not implemented yet, ENG-003) |
| `engine.lean.docker_command` | `["docker"]` | command used to invoke Docker, run without a shell (e.g. `["podman"]`) |
| `engine.lean.network` | `none` | `none` or `bridge`; backtests run without network by default |
| `engine.lean.timeout_s` | `3600` | backtests running longer are stopped (`RunState.TIMED_OUT`) |
| `data.root` | `{home}/data` | |
| `db.url` note | | unset → SQLite file above |
| `trading.mode` | `paper` | `live` is **rejected** by validation until LIVE-003 gates exist; even then config alone will not activate live |
| `brokers.ibkr.host` | `127.0.0.1` | TWS/IB Gateway host |
| `brokers.ibkr.port_paper` | `4002` | IB Gateway paper (TWS paper: 7497) |
| `brokers.ibkr.port_live` | `4001` | IB Gateway live (TWS live: 7496) |
| `brokers.ibkr.client_id` | `17` | broker (orders) connection, Phase 5 |
| `brokers.ibkr.market_data_client_id` | `18` | read-only market-data connection; must differ from `client_id` |
| `market_data.historical` | `tiingo` | `tiingo` or `ibkr` (DATA-019; datasets must be `--adjustment split_adjusted` for IBKR); default for `quantlab data fetch` |
| `market_data.realtime` | `mock` | `mock` (SIMULATED replay of a catalog dataset) or `ibkr` (read-only TWS/IB Gateway connection, paper port) |
| `market_data.ibkr_data_type` | `live` | requested IB market data type: `live`, `frozen`, `delayed`, `delayed_frozen`; quotes are labelled by what IB actually delivers; `require_live` needs `live` |
| `market_data.require_live` | `false` | refuse any quote that is not LIVE; `true` with `realtime = "mock"` is rejected |
| `market_data.mock_dataset` | unset | catalog dataset replayed by the mock real-time provider |
| `market_data.stale_after_s` | `15.0` | LIVE quotes older than this (by exchange time) are flagged `stale` |
| `backtest.models.brokerage` | `default` | `default` or `interactive_brokers` (LEAN brokerage model) |
| `backtest.models.account_type` | `margin` | `margin` or `cash` (cash: settlement applies) |
| `backtest.models.fee` | `brokerage` | `brokerage` (LEAN's), `zero`, `constant` (+ `fee_per_order` > 0) |
| `backtest.models.slippage` | `brokerage` | `brokerage`, `none`, `constant` (+ `slippage_percent`), `volume_share` (+ `volume_limit`, `price_impact`) |
| `backtest.models.fill` | `brokerage` | `brokerage` or `immediate` |
| `backtest.models.settlement` | `brokerage` | `brokerage` (US equity cash: T+1 06:00), `immediate`, `delayed` (+ `settlement_days`, `settlement_hour`) |
| `lifecycle.require_real_data` | `true` | promotion evidence must use real_historical / live_recorded data |
| `lifecycle.min_oos_windows` | `3` | walk-forward OOS windows required for `validated` |
| `lifecycle.min_oos_sharpe` / `max_oos_drawdown` | `0.5` / `0.30` | stitched OOS limits for `validated` |
| `lifecycle.require_overfitting_checks` | `true` | `validated` also needs a sweep with `min_dsr_probability` (0.90) and `max_pbo` (0.50) |
| `paper.account` | `paper` | paper account name (state file `{home}/paper/<account>.json`) |
| `paper.starting_cash` | `100000` | simulated cash |
| `paper.commission_per_share` / `min_commission` | `0.005` / `1.0` | simulated commissions |
| `paper.slippage_bps` | `1.0` | market/stop fills worse than the touch by this |
| `paper.allow_short` | `false` | cash account (no shorting) |
| `paper.deployment_isolation` | `"auto"` | `paper deploy` strategy process without network (DEC-0035): `auto` = required on Linux/WSL2, warning elsewhere; `required`; `off` |
| `risk.limits_file` | `{home}/risk_limits.toml` | changes require UI confirmation + audit |
| `ai.provider` | `none` | `anthropic`, `openai`, `ollama`, `none` |
| `ai.api_key_ref` | unset | must start with `secret://` |
| `ai.ollama_url` | `http://127.0.0.1:11434` | |
| `logging.level` | `INFO` | DEBUG/INFO/WARNING/ERROR/CRITICAL |
| `logging.json_output` | `true` | console format; files always JSON |
| `logging.to_file` | `true` | `{home}/logs/quantlab.log`, rotating 20 MB × 10 |
| `engine.default` note | | only `lean` accepted |

Example `QUANTLAB_HOME/quantlab.toml`:
```toml
[server]
port = 8765

[engine.lean]
launcher = "docker"
# image = "quantconnect/lean@sha256:<digest>"

[market_data]          # development defaults; production: historical = "ibkr", realtime = "ibkr", require_live = true
historical = "tiingo"
realtime = "mock"
# production (IB Gateway paper on 4002 with API enabled):
# historical = "ibkr"
# realtime = "ibkr"
# require_live = true

[backtest.models]      # applied only by strategies that call quantlab_lean.configure(self)
fee = "constant"
fee_per_order = 1.0
slippage = "constant"
slippage_percent = 0.0005

[logging]
level = "INFO"
```
Per-run override: `quantlab backtest run <strategy> -m fee=zero -m account_type=cash`.
Environment override example: `QUANTLAB_SERVER__PORT=9000`.

## Pinning the LEAN image
Find the digest of the image you want and put it in `quantlab.toml`:
```
docker pull quantconnect/lean:latest
docker image inspect --format "{{index .RepoDigests 0}}" quantconnect/lean:latest
```
Candidate observed on Docker Hub 2026-11-06 (`latest`, linux/amd64 — **not yet run by QuantLab**, KI-0002):
`quantconnect/lean@sha256:e3d50f1c7f908abe95f27eb4aed7d02f47bdff2ae25c92059c6bffa2c9559714`
(multi-arch index digest: `sha256:a91d4e3058242d6db408097001e2dea4041e21fe115fda9b26a55cfab8e90031`).

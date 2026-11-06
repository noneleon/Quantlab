# Configuration Reference

Status: **DRAFT** — keys become authoritative once implemented (CORE-002). Precedence (low → high):
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
| `engine.lean.image` | `quantconnect/lean@sha256:<pinned>` | must be a digest, not a tag |
| `engine.lean.native_path` | — | path to local LEAN Launcher build |
| `data.root` | `{home}/data` | |
| `trading.mode` | `paper` | `paper` or `live`; `live` additionally requires the gates in LIVE-003 |
| `brokers.ibkr.host` | `127.0.0.1` | TWS/IB Gateway host |
| `brokers.ibkr.port_paper` | `4002` (Gateway) / `7497` (TWS) | |
| `brokers.ibkr.port_live` | `4001` (Gateway) / `7496` (TWS) | |
| `brokers.ibkr.client_id` | `17` | |
| `risk.limits_file` | `{home}/risk_limits.toml` | changes require UI confirmation + audit |
| `ai.provider` | `none` | `anthropic`, `openai`, `ollama`, `none` |
| `ai.ollama.url` | `http://127.0.0.1:11434` | |
| `logging.level` | `INFO` | |

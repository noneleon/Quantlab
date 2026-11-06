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
| `engine.lean.native_path` | — | path to local LEAN Launcher build |
| `data.root` | `{home}/data` | |
| `db.url` note | | unset → SQLite file above |
| `trading.mode` | `paper` | `live` is **rejected** by validation until LIVE-003 gates exist; even then config alone will not activate live |
| `brokers.ibkr.host` | `127.0.0.1` | TWS/IB Gateway host |
| `brokers.ibkr.port_paper` | `4002` | IB Gateway paper (TWS paper: 7497) |
| `brokers.ibkr.port_live` | `4001` | IB Gateway live (TWS live: 7496) |
| `brokers.ibkr.client_id` | `17` | |
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

[logging]
level = "INFO"
```
Environment override example: `QUANTLAB_SERVER__PORT=9000`.

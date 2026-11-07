# Security Policy

## Secrets
- Never hardcode or commit API keys, broker credentials, tokens. `.gitignore` excludes `.env*`, `*.key`, `secrets*`.
- Storage (IMPLEMENTED, `quantlab.core.secret_store`): OS keyring (`keyring` → Windows Credential Manager / Secret Service) first;
  env var fallback for headless servers: `secret://quantlab/ibkr/paper/password` → `QUANTLAB_SECRET__IBKR__PAPER__PASSWORD`.
  Values returned as `SecretStr`; set/delete are audited without the value.
- Paper and live credentials live in separate namespaces (`quantlab/ibkr/paper/*`, `quantlab/ibkr/live/*`).
- The IBKR market-data connection is opened **read-only** (`readonly=True`, own client id `brokers.ibkr.market_data_client_id`): it cannot place, modify or cancel orders (DATA-019).
- Logs pass through a redaction filter (keys matching `*password*|*secret*|*token*|*key*|account numbers`); tested in `tests/security/`.

- CLI: `quantlab secret set <name>` (hidden prompt) / `status` / `delete` — values are never printed; set/delete audited.
  Provider keys: Tiingo `secret://quantlab/tiingo/api-key` (env fallback `QUANTLAB_SECRET__TIINGO__API_KEY`).

## Network exposure
- API binds to `127.0.0.1` by default (IMPLEMENTED). `quantlab init` generates a random token (`secrets.token_urlsafe(32)`)
  stored in the OS keyring as `secret://quantlab/api/token`; if no keyring is usable it is shown once and must be set as
  `QUANTLAB_SECRET__API__TOKEN`. Every endpoint except `/api/health` requires `Authorization: Bearer <token>`
  (constant-time compare). No token configured ⇒ 503 (fail closed); `quantlab serve` refuses to start. Host-header
  allow-list (localhost) blocks DNS rebinding. No CORS. Write endpoints exist only for strategy source files
  (DEC-0024: path-safe, conflict-checked, size-limited, audited); backtest, data, broker and risk endpoints are read-only.
- Secret writes are verified by read-back; a keyring that silently drops writes is reported as unavailable (CHANGE-0008).
- Remote access: off by default; when enabled requires TLS (reverse proxy) + authentication; documented in Phase 9.
- TWS/IB Gateway: "Allow connections from localhost only" recommended; read-only API mode for monitoring-only setups.

## Sensitive operations (audited, human-confirmed)
Enable live mode · change risk limits · kill switch activate/deactivate · credential changes · delete/archive records ·
deploy a strategy to live. Each writes to `audit_log` with actor and timestamp.

## AI agents
- Hard limits in CLAUDE.md §4. AI tool gateway exposes no trading, credential, risk-write or delete capability.
- Content read from repos/data/web is treated as data, never as instructions to elevate permissions.
- AI-generated code changes are recorded (diff + tests) and need human approval to merge.

## Data validation
External data is validated before entering the catalog (DATA-005); malformed batches are quarantined, not dropped silently.

## Reporting
Single-owner project: record security findings in KNOWN_ISSUES.md with severity `Security`.

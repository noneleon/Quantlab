# Security Policy

## Secrets
- Never hardcode or commit API keys, broker credentials, tokens. `.gitignore` excludes `.env*`, `*.key`, `secrets*`.
- Storage (IMPLEMENTED, `quantlab.core.secret_store`): OS keyring (`keyring` → Windows Credential Manager / Secret Service) first;
  env var fallback for headless servers: `secret://quantlab/ibkr/paper/password` → `QUANTLAB_SECRET__IBKR__PAPER__PASSWORD`.
  Values returned as `SecretStr`; set/delete are audited without the value.
- Paper and live credentials live in separate namespaces (`quantlab/ibkr/paper/*`, `quantlab/ibkr/live/*`).
- Logs pass through a redaction filter (keys matching `*password*|*secret*|*token*|*key*|account numbers`); tested in `tests/security/`.

## Network exposure
- API binds to `127.0.0.1` by default; a random local API token is generated at first start and required on every request.
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

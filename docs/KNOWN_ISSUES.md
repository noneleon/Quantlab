# Known Issues & Open Questions

| ID | Severity | Area | Description | Status |
|---|---|---|---|---|
| KI-0001 | High | env | Owner's Windows environment (Python/.NET/Docker/WSL2/IB Gateway) not yet probed; dev container differs (Linux, Py 3.13, no Docker daemon, no .NET). | OPEN — run `tools/env_check.py` on Windows |
| KI-0002 | High | engine | LEAN has not been executed in any QuantLab environment yet; no end-to-end validation of Docker image, data mount or result parsing. | OPEN — Milestone M1 |
| KI-0003 | High | data | No real historical data source selected. Free sources are survivorship-biased and have ToS limits; QC Data Library is paid. | OPEN — owner decision |
| KI-0004 | Medium | architecture | DEC-0007 (live order path) undecided; blocks OMS-007. | OPEN — owner decision |
| KI-0005 | Medium | engine | Verify LEAN settlement model coverage for US equities T+1 (since May 2024) before claiming BT-007. | OPEN |
| KI-0006 | Medium | brokers | IBKR TWS/Gateway require periodic restarts/re-auth; affects unattended live operation and recovery design. | OPEN — Phase 5/6 |
| KI-0007 | Low | tooling | Python 3.11 is not installed in the dev container (3.13 present; uv 0.11.32 available); Phase 1 must set up 3.11 via `uv python install 3.11`. | OPEN |
| KI-0008 | Low | licensing | Docker Desktop licence terms apply on Windows (free for personal use / small business) — confirm owner's eligibility, else use native LEAN launcher or WSL2 Docker Engine. | OPEN |

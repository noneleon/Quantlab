# Known Issues & Open Questions

| ID | Severity | Area | Description | Status |
|---|---|---|---|---|
| KI-0001 | High | env | Owner's Windows environment (Python/.NET/Docker/WSL2/IB Gateway) not yet probed; dev container differs (Linux, Py 3.13, no Docker daemon, no .NET). | OPEN — run `tools/env_check.py` on Windows |
| KI-0002 | High | engine | LEAN has not been executed in any QuantLab environment yet. Launcher, config and parser are tested only with a fake docker + synthetic fixture. Run `tests/integration/test_lean_docker_e2e.py` on a machine with Docker (see TESTING_STRATEGY). | OPEN — blocks ENG-002/004/005 → VERIFIED |
| KI-0003 | High | data | No real historical data source selected. Free sources are survivorship-biased and have ToS limits; QC Data Library is paid. | OPEN — owner decision |
| KI-0004 | Medium | architecture | DEC-0007 (live order path) undecided; blocks OMS-007. | OPEN — owner decision |
| KI-0005 | Medium | engine | Verify LEAN settlement model coverage for US equities T+1 (since May 2024) before claiming BT-007. | OPEN |
| KI-0006 | Medium | brokers | IBKR TWS/Gateway require periodic restarts/re-auth; affects unattended live operation and recovery design. | OPEN — Phase 5/6 |
| KI-0007 | Low | tooling | Python 3.11 was not installed in the dev container. | RESOLVED (CHANGE-0002: `uv python install 3.11` → 3.11.15) |
| KI-0008 | Low | licensing | Docker Desktop licence terms apply on Windows (free for personal use / small business) — confirm owner's eligibility, else use native LEAN launcher or WSL2 Docker Engine. | OPEN |
| KI-0009 | Medium | ci | CI (incl. Windows leg) had not been observed running. | RESOLVED (runs #1/#2 green on ubuntu + windows) |
| KI-0010 | Low | core | Log redaction is pattern-based; secrets in unusual free-text formats could pass. Mitigation: never log credentials at all; secret store (SEC-001) returns opaque objects. | OPEN |
| KI-0011 | Low | core | PostgreSQL code paths (pool_pre_ping, append-only trigger function) untested — no Postgres in CI. | OPEN — add service container when Postgres is needed |
| KI-0012 | Low | core | Audit append lock is per-process; safe for the single-process backend (DEC-0002) only. | OPEN — revisit if workers write audit entries |
| KI-0013 | High | repo | `.gitignore` `secrets*` excluded `src/quantlab/core/secrets.py`; CI ran without it. | RESOLVED (CHANGE-0004: renamed to `secret_store.py`, guard test added) |
| KI-0014 | Medium | engine | Unverified with real LEAN: (a) `--network none` — LEAN may attempt network calls at startup; (b) read-only `/Lean/Data` mount — LEAN might write caches there; (c) read-only `/LeanCLI` — Python cannot write `__pycache__` (expected harmless). | OPEN — check during first real run |
| KI-0015 | Low | engine | On Linux hosts, files LEAN writes into `runs/<id>/results` are owned by root (container user). | OPEN — consider `--user` once image requirements are known |
| KI-0016 | Low | engine | `engine.log` is captured but not parsed; LEAN runtime errors are taken from the result file `state` only. | OPEN — Phase 3 |
| KI-0017 | Medium | analytics | Cross-check tolerances vs LEAN (Net Profit 0.05 pp, CAGR 0.5 pp, Drawdown 0.15 pp) are provisional; LEAN's CAGR/drawdown definitions may differ from QuantLab's (daily UTC resampling). Mismatches are recorded, not fatal. | OPEN — calibrate on first real LEAN runs |
| KI-0018 | Medium | backtest | Runs are synchronous; if the process dies mid-run the row stays `running` forever. Needs startup recovery (mark `interrupted`) with the job runner. | OPEN — CORE-007 |
| KI-0019 | Medium | analytics | Trade metrics (win rate, profit factor, expectancy, costs), turnover, exposure, alpha/beta not yet computed (need LEAN order-events fills and a benchmark series). | OPEN — AN-001 remainder |
| KI-0020 | Low | strategies | Tree hash is byte-exact: the same Git commit checked out with `core.autocrlf=true` on Windows hashes differently than on Linux. | OPEN — document; consider `.gitattributes eol=lf` for strategy repos |
| KI-0021 | Low | cli | CLI prints structured JSON log lines to stderr (INFO), noisy for interactive use; `--json` output on stdout is clean. | OPEN — separate console level from file level |
| KI-0022 | Low | api | Starlette deprecation warning: TestClient with `httpx` (suggests `httpx2`). Test-only. | OPEN — revisit on dependency update |
| KI-0023 | Low | cli | `quantlab serve` and `engine pull` have no automated test (manual smoke only); CLI coverage 77 %. | OPEN |
| KI-0024 | Medium | data | Each import rewrites the whole dataset version (copy-on-write) and hashes rows in a Python loop; fine for daily data, slow for large minute/second datasets. | OPEN — partitioned versions / vectorised hashing before intraday bulk imports |
| KI-0025 | Medium | data | Gap detection only for daily bars; intraday gaps (missing minutes within sessions) are not checked. | OPEN |
| KI-0026 | Medium | data | QuantLab calendars (exchange_calendars) are not reconciled with LEAN's market-hours database; forex `weekdays` calendar is an approximation. | OPEN — compare when LEAN export (DATA-009) lands |
| KI-0027 | Low | data | pandas 3.1 deprecation warnings raised inside exchange_calendars and duckdb `.df()`; harmless now, may break on pandas 4. | OPEN — track upstream |
| KI-0028 | High | data/engine | A real LEAN run has never read a QuantLab export. Unverified: 2-column map files, neutral factor files, benchmark (LEAN's default SPY benchmark has no data unless SPY is in the dataset — strategies should `set_benchmark` to a dataset symbol), `--network none`. | OPEN — first Docker run with `--dataset` |
| KI-0029 | Medium | data | Hour bars: LEAN labels the first regular-session hour bar 09:00 (covering 09:30–10:00); vendor hour bars stamped 09:30 export as 09:30 and may not align with LEAN consolidators. | OPEN — verify with real LEAN; consider re-bucketing on import |
| KI-0030 | Low | engine | Dataset runs copy the whole export into the container on every run (cheap for daily, slow for large minute datasets). | OPEN |
| KI-0031 | Low | research | The `research` extra (JupyterLab) is not installed or tested in CI; verified manually via nbconvert only. | OPEN — optional CI job |
| KI-0032 | Low | research | ipykernel warns that the local kernel uses TCP without encryption (Jupyter default on localhost). | OPEN — acceptable for local use; revisit for remote research |
| KI-0033 | Low | web | No DOM-level component tests (jsdom/happy-dom dropped after an npm resolver crash); interactive behaviour is covered only by the manual Playwright smoke script, not in CI. | OPEN — add a CI e2e job (Playwright) |
| KI-0034 | Low | web | vitest pinned to `~4.0` because npm 10.9 crashes resolving vitest ≥ 4.1 optional peers. | OPEN — re-test on npm/vitest updates |
| KI-0035 | Low | web | `web/dist` is not committed; users need Node.js 22 to build the UI once (`cd web && npm ci && npm run build`). | OPEN — consider shipping built assets in releases |
| KI-0036 | Medium | data | Corporate actions cover splits and cash dividends only; symbol changes, delistings and spin-offs are not modelled (map files contain only first/last date). | OPEN — DATA-014 |
| KI-0037 | Low | data | Adjusted reads back-adjust to the version's latest event (like LEAN's ADJUSTED mode); not point-in-time — use raw reads or LEAN backtests for point-in-time correctness. | DOCUMENTED |

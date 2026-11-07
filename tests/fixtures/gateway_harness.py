"""Test harness: a real QuantLab paper OMS + LEAN gateway for the C# QuantLabBrokerage tests (OMS-007).

Synthetic SIMULATED quotes only (SPY bid 49.99 / ask 50.01), a scratch SQLite database and paper broker state
in the given directory, and the real risk gate with a fixed limits file. Usage::

    python tests/fixtures/gateway_harness.py <workdir>

Prints ``READY <run file>`` once the gateway listens. Control lines on stdin: ``kill`` engages the kill
switch, ``quit`` (or end of input) stops the gateway.
"""

from __future__ import annotations

import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

from quantlab.brokers.paper import PaperBroker
from quantlab.core.db import create_db_engine, migrate, session_factory
from quantlab.data.market_data import DataState, Quote
from quantlab.oms.gateway import Gateway, write_run_file
from quantlab.oms.service import OMS
from quantlab.risk.gate import KillSwitch, RiskGate

LIMITS = """[limits]
max_order_quantity = 100
max_order_notional = 5000.0
max_position_notional = 8000.0
max_gross_exposure_fraction = 0.5
max_open_orders = 5
max_orders_per_minute = 50
daily_loss_limit_fraction = 0.05
max_drawdown_fraction = 0.10
max_rejections_per_hour = 50
allow_margin = false
max_quote_age_s = 60.0
max_spread_bps = 50.0
allow_short = false
require_live_quotes = false
trading_calendar = "none"
"""


def quote(symbol: str) -> Quote | None:
    if symbol != "SPY":
        return None
    now = datetime.now(UTC)
    return Quote(symbol, DataState.SIMULATED, "harness", now, now, bid=49.99, ask=50.01, last=50.0)


def main(workdir: Path) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    engine = create_db_engine(f"sqlite:///{workdir / 'quantlab.db'}")
    migrate(engine)
    sessions = session_factory(engine)
    limits = workdir / "risk_limits.toml"
    limits.write_text(LIMITS, encoding="utf-8")
    kill = KillSwitch()
    broker = PaperBroker(workdir / "paper.json", quote, starting_cash=20_000.0, slippage_bps=0)
    oms = OMS(sessions, broker, RiskGate(limits, kill), quote, account="paper", starting_cash=20_000.0)
    gateway = Gateway(oms, sessions, "csharp-test", poll_s=0.05)
    run_file = workdir / "gateway.json"
    write_run_file(run_file, gateway.info)

    def control() -> None:
        for line in sys.stdin:
            if line.strip() == "kill":
                with sessions() as session:
                    kill.engage(session, actor="harness", reason="C# test")
                    session.commit()
                print("KILLED", flush=True)
            elif line.strip() == "quit":
                break
        gateway.stop()

    threading.Thread(target=control, daemon=True).start()
    print(f"READY {run_file}", flush=True)
    gateway.serve()


if __name__ == "__main__":
    main(Path(sys.argv[1]))

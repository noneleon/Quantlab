"""Real LEAN on the native .NET launcher (ENG-003/008) — runs only when QUANTLAB_TEST_LEAN_NATIVE points to a
`quantlab engine native-install` directory and `dotnet` is on the PATH.

The C# example has LEAN's BasicTemplateAlgorithm logic and runs on LEAN's bundled sample data, so LEAN's own
published statistics for that algorithm are the expected values (provenance: sample — not a performance
claim).
"""

import json
import os
import shutil
from pathlib import Path

import pytest

from quantlab.engine.base import AlgorithmLanguage, AlgorithmSpec, BacktestSpec, RunState
from quantlab.engine.lean.native import LeanNativeAdapter

INSTALL = os.environ.get("QUANTLAB_TEST_LEAN_NATIVE")
EXAMPLE = Path(__file__).resolve().parents[2] / "strategies" / "examples" / "buy_and_hold_cs"

pytestmark = pytest.mark.skipif(
    not INSTALL or shutil.which("dotnet") is None,
    reason="QUANTLAB_TEST_LEAN_NATIVE not set or dotnet missing",
)

# From LEAN's Algorithm.CSharp/BasicTemplateAlgorithm.cs at tag 18229 (ExpectedStatistics).
EXPECTED = {
    "Total Orders": "1",
    "Start Equity": "100000",
    "End Equity": "101691.92",
    "Net Profit": "1.692%",
    "Compounding Annual Return": "271.453%",
    "Drawdown": "2.200%",
    "Sharpe Ratio": "8.854",
}
ORDER_LIST_HASH = "3da9fa60bf95b9ed148b95e02e0cfc9e"  # same orders as LEAN's own regression run


def test_csharp_strategy_reproduces_leans_published_statistics(tmp_path):
    adapter = LeanNativeAdapter(Path(INSTALL or ""))
    assert adapter.probe().launcher == "native"
    spec = BacktestSpec(
        run_id="native-golden",
        algorithm=AlgorithmSpec(EXAMPLE, "BuyAndHold.cs", AlgorithmLanguage.CSHARP),
        work_dir=tmp_path / "run",
    )
    handle = adapter.start_backtest(spec)
    outcome = adapter.wait(handle, timeout_s=900)
    assert outcome.state is RunState.SUCCEEDED, (handle.work_dir / "engine.log").read_text()[-3000:]
    stats = json.loads((handle.results_dir / "native-golden.json").read_text())["statistics"]
    assert {k: stats[k] for k in EXPECTED} == EXPECTED
    log = handle.log_path.read_text(encoding="utf-8", errors="replace")
    assert f"STATISTICS:: OrderListHash {ORDER_LIST_HASH}" in log
    result = adapter.collect(handle)
    assert result.statistics["Net Profit"] == "1.692%"


def test_python_strategy_reproduces_leans_published_statistics(tmp_path):
    """KI-0078: a Python strategy on the native launcher (LEAN's pythonnet in QuantLab's own Python 3.11)."""
    adapter = LeanNativeAdapter(Path(INSTALL or ""))
    spec = BacktestSpec(
        run_id="native-golden-py",
        algorithm=AlgorithmSpec(EXAMPLE.with_name("buy_and_hold_py"), "main.py", AlgorithmLanguage.PYTHON),
        work_dir=tmp_path / "run",
    )
    handle = adapter.start_backtest(spec)
    outcome = adapter.wait(handle, timeout_s=900)
    assert outcome.state is RunState.SUCCEEDED, (handle.work_dir / "engine.log").read_text()[-3000:]
    stats = json.loads((handle.results_dir / "native-golden-py.json").read_text())["statistics"]
    assert {k: stats[k] for k in EXPECTED} == EXPECTED
    log = handle.log_path.read_text(encoding="utf-8", errors="replace")
    assert f"STATISTICS:: OrderListHash {ORDER_LIST_HASH}" in log


def test_real_lean_paper_order_goes_through_the_gateway_and_risk_gate(tmp_path):
    """OMS-007 end to end: LEAN live mode → QuantLabBrokerage → gateway → OMS + risk gate → paper broker →
    fill back in LEAN. Synthetic prices only (gateway harness quotes; FakeDataQueue; seeded price when
    closed).
    """
    import subprocess
    import sys
    import time

    from sqlalchemy import select

    from quantlab.core.db import create_db_engine, session_factory
    from quantlab.engine.lean.native import LAUNCHER, build_live_config, compile_csharp
    from quantlab.oms import queries
    from quantlab.risk.models import RiskDecision

    repo = Path(__file__).resolve().parents[2]
    install = Path(INSTALL or "")
    dll = compile_csharp(repo / "tests" / "fixtures" / "lean_live_probe", tmp_path / "build")
    harness = subprocess.Popen(
        [sys.executable, str(repo / "tests" / "fixtures" / "gateway_harness.py"), str(tmp_path / "gw")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )  # fmt: skip
    try:
        line = harness.stdout.readline() if harness.stdout else ""
        assert line.startswith("READY "), line
        (tmp_path / "results").mkdir()
        cfg = build_live_config(
            algorithm_location=str(dll),
            type_name="GatewayProbe",
            algorithm_id="live-probe",
            gateway_file=line.split(" ", 1)[1].strip(),
            data_folder=str(install / "Data") + "/",
            results=str(tmp_path / "results"),
            storage=str(tmp_path / "storage"),
            data_queue_handler="QuantConnect.Lean.Engine.DataFeeds.Queues.FakeDataQueue",
        )
        config = tmp_path / "lean-config.json"
        config.write_text(json.dumps(cfg), encoding="utf-8")
        started = time.monotonic()
        lean = subprocess.run(
            ["dotnet", LAUNCHER, "--config", str(config)],
            cwd=install / "bin", capture_output=True, text=True, timeout=300,
        )  # fmt: skip
        log = lean.stdout + lean.stderr
        assert lean.returncode == 0, log[-3000:]
        assert time.monotonic() - started < 300
        assert "PROBE order event 1 Filled qty 1 @ 50.01" in log, log[-3000:]
        sessions = session_factory(create_db_engine(f"sqlite:///{tmp_path / 'gw' / 'quantlab.db'}"))
        with sessions() as s:
            ((order, state),) = queries.orders_with_state(s, "paper", None)
            assert state.value == "FILLED" and order.source == "lean:csharp-test"
            assert [e.state for e in queries.order_events(s, order.id)][:2] == ["NEW", "PENDING_SUBMIT"]
            decision = s.execute(select(RiskDecision).where(RiskDecision.order_id == order.id)).scalar_one()
            assert (
                decision.approved
            )  # the order passed QuantLab's central risk gate before reaching the broker
            (fill,) = queries.fills(s, "paper")
            assert (fill.quantity, fill.price, fill.price_state) == (1.0, 50.01, "SIMULATED")
    finally:
        if harness.stdin:
            harness.stdin.close()
        harness.wait(timeout=30)


def test_paper_deployment_trades_on_quantlab_prices_through_the_risk_gate(tmp_path):
    """5c-4: `deploy` — LEAN live mode, prices from QuantLab via QuantLabDataQueue (no seeded price), order
    through the gateway, OMS and risk gate, fill back, audited start/stop. Synthetic quotes; exchange forced
    open (test only)."""
    import sys

    from quantlab.core.audit import AuditEntry
    from quantlab.oms import queries
    from quantlab.oms.deployment import deploy
    from quantlab.strategies.snapshot import hash_tree

    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo / "tests" / "fixtures"))
    from gateway_harness import build_stack

    oms, sessions, _kill = build_stack(tmp_path / "stack")
    result = deploy(
        oms,
        sessions,
        deployment="deploy-probe",
        strategy="deploy-probe",
        strategy_dir=repo / "tests" / "fixtures" / "lean_deploy_probe",
        approved_tree_hash=hash_tree(repo / "tests" / "fixtures" / "lean_deploy_probe").tree_hash,
        entry_file="DeployProbe.cs",
        install_dir=Path(INSTALL or ""),
        home=tmp_path / "home",
        max_minutes=4,
        force_exchange_open=True,
    )
    log = result.log_path.read_text(encoding="utf-8", errors="replace")
    assert result.stopped_by == "algorithm" and result.exit_code == 0, log[-3000:]
    assert "DEPLOY first price 50" in log  # QuantLab's quote (synthetic), not a seeded value
    assert "SPY prices are SIMULATED, not clean LIVE data" in log  # the data state reached the algorithm
    assert "DEPLOY order event 1 Filled qty 1 @ 50.01" in log
    with sessions() as s:
        ((order, state),) = queries.orders_with_state(s, "paper", None)
        assert state.value == "FILLED" and order.source == "lean:deploy-probe"
        actions = [e.action for e in s.query(AuditEntry).order_by(AuditEntry.seq)]
        assert actions.count("paper.deploy.start") == 1 and actions[-1] == "paper.deploy.stop"
    assert not (tmp_path / "home" / "run" / "gateway-deploy-probe.json").exists()  # token removed
    provenance = json.loads((result.work_dir / "provenance.json").read_text())
    assert provenance["data_states"] == ["SIMULATED"] and provenance["synthetic"] is True
    from datetime import UTC, datetime

    from quantlab.oms.deployment import read_deployments

    (row,) = read_deployments(tmp_path / "home", datetime.now(UTC))
    assert (row["deployment"], row["state"], row["running"]) == ("deploy-probe", "stopped", False)
    assert row["stopped_by"] == "algorithm" and row["exit_code"] == 0 and row["data_states"] == ["SIMULATED"]

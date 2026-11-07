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

"""Real LEAN run in Docker (KI-0002). Skipped unless a daemon is reachable and an image is pinned:

    QUANTLAB_TEST_LEAN_IMAGE=quantconnect/lean@sha256:<digest> uv run pytest -m docker

Uses LEAN's bundled sample data (provenance `sample`) — results are not real performance claims.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from quantlab.core.ids import new_id
from quantlab.engine.base import AlgorithmSpec, BacktestSpec, RunState
from quantlab.engine.lean.docker import LeanDockerAdapter

IMAGE = os.environ.get("QUANTLAB_TEST_LEAN_IMAGE")  # read at import: conftest strips QUANTLAB_* per test
ALGO = Path(__file__).parents[1] / "fixtures" / "algorithms" / "buy_and_hold_spy"


def _daemon_up() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return (
            subprocess.run(["docker", "info"], capture_output=True, timeout=20, check=False).returncode == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not IMAGE, reason="QUANTLAB_TEST_LEAN_IMAGE not set"),
    pytest.mark.skipif(not _daemon_up(), reason="Docker daemon not reachable"),
]


def test_real_lean_backtest_on_sample_data(tmp_path):
    adapter = LeanDockerAdapter(IMAGE)
    info = adapter.probe()
    assert info.image_id
    run_id = new_id()
    handle = adapter.start_backtest(
        BacktestSpec(run_id=run_id, algorithm=AlgorithmSpec(ALGO), work_dir=tmp_path / run_id)
    )
    outcome = adapter.wait(handle, timeout_s=1800)
    assert outcome.state is RunState.SUCCEEDED, handle.log_path.read_text()[-4000:]
    result = adapter.collect(handle)
    assert result.succeeded, result.runtime_error
    assert result.orders, "buy-and-hold should place at least one order"
    assert result.equity and result.statistics

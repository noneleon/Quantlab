"""`quantlab paper deploy` guards (5c-4) without .NET. The real LEAN deployment is in
tests/integration/test_lean_native.py."""

import json

import pytest

from quantlab.oms.deployment import DeploymentError, deploy

from .test_oms_risk import stack  # noqa: F401  (fixture)


def _deploy(stack, tmp_path, *, strategy_dir, approved, install):  # noqa: F811
    return deploy(
        stack["oms"],
        stack["sessions"],
        deployment="d1",
        strategy="s",
        strategy_dir=strategy_dir,
        entry_file="Algo.cs",
        approved_tree_hash=approved,
        install_dir=install,
        home=tmp_path / "home",
    )


def test_unsupported_files_and_missing_installs_are_refused(stack, tmp_path):  # noqa: F811
    common = {
        "deployment": "d1",
        "strategy": "s",
        "strategy_dir": tmp_path,
        "approved_tree_hash": "x",
        "install_dir": tmp_path / "none",
        "home": tmp_path,
    }
    with pytest.raises(DeploymentError, match=r"C# \(\.cs\) or Python"):
        deploy(stack["oms"], stack["sessions"], entry_file="algo.ipynb", **common)
    with pytest.raises(DeploymentError, match="native-install"):
        deploy(stack["oms"], stack["sessions"], entry_file="Algo.cs", **common)
    install = tmp_path / "lean-native"  # an install from before Python support (no AlgorithmImports.py)
    (install / "bin").mkdir(parents=True)
    (install / "bin" / "QuantConnect.Lean.Launcher.dll").write_bytes(b"")
    (install / "lean-native.json").write_text("{}")
    with pytest.raises(DeploymentError, match="predates Python support"):
        deploy(stack["oms"], stack["sessions"], entry_file="main.py", **{**common, "install_dir": install})


def test_a_running_deployment_name_cannot_start_twice(stack, tmp_path):  # noqa: F811
    from quantlab.core.filelock import exclusive

    install = tmp_path / "lean-native"
    (install / "bin").mkdir(parents=True)
    (install / "bin" / "QuantConnect.Lean.Launcher.dll").write_bytes(b"")
    (install / "lean-native.json").write_text(json.dumps({}))
    run = tmp_path / "home" / "run" / "gateway-d1.json.lock"
    with exclusive(run), pytest.raises(DeploymentError, match="already running"):
        _deploy(stack, tmp_path, strategy_dir=tmp_path, approved="x", install=install)


def test_only_the_approved_files_are_deployed(stack, tmp_path):  # noqa: F811
    """Review m1: the snapshot must match the hash the strategy was promoted with (no unapproved code)."""
    install = tmp_path / "lean-native"
    (install / "bin").mkdir(parents=True)
    (install / "bin" / "QuantConnect.Lean.Launcher.dll").write_bytes(b"")
    (install / "lean-native.json").write_text("{}")
    strategy = tmp_path / "strategy"
    strategy.mkdir()
    (strategy / "Algo.cs").write_text("// approved")
    from quantlab.strategies.snapshot import hash_tree

    approved = hash_tree(strategy).tree_hash
    (strategy / "Algo.cs").write_text("// changed after approval")
    with pytest.raises(DeploymentError, match="not the approved version"):
        _deploy(stack, tmp_path, strategy_dir=strategy, approved=approved, install=install)
    assert not (tmp_path / "home" / "run" / "gateway-d1.json").exists()


def test_a_heartbeat_slightly_in_the_future_still_counts_as_running(tmp_path):
    """Review 5c-5 m4 (display only): small clock skew must not show a running deployment as lost."""
    from datetime import UTC, datetime, timedelta

    from quantlab.oms.deployment import read_deployments

    run = tmp_path / "deployments" / "d1" / "20261108T100000Z"
    run.mkdir(parents=True)
    now = datetime(2026, 11, 8, 10, tzinfo=UTC)
    beat = (now + timedelta(seconds=2)).isoformat()  # the writer's clock is slightly ahead
    (run / "status.json").write_text(
        json.dumps({"deployment": "d1", "state": "running", "heartbeat_at": beat})
    )
    (row,) = read_deployments(tmp_path, now)
    assert row["running"] is True and row["state"] == "running"

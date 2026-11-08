"""`quantlab paper deploy` guards (5c-4) without .NET. The real LEAN deployment is in
tests/integration/test_lean_native.py."""

import json

import pytest

from quantlab.oms.deployment import DeploymentError, deploy

from .test_oms_risk import stack  # noqa: F401  (fixture)


def test_python_strategies_and_missing_installs_are_refused(stack, tmp_path):  # noqa: F811
    common = {
        "deployment": "d1",
        "strategy_dir": tmp_path,
        "install_dir": tmp_path / "none",
        "home": tmp_path,
    }
    with pytest.raises(DeploymentError, match="C# strategies"):
        deploy(stack["oms"], stack["sessions"], entry_file="main.py", **common)
    with pytest.raises(DeploymentError, match="native-install"):
        deploy(stack["oms"], stack["sessions"], entry_file="Algo.cs", **common)


def test_a_running_deployment_name_cannot_start_twice(stack, tmp_path):  # noqa: F811
    from quantlab.core.filelock import exclusive

    install = tmp_path / "lean-native"
    (install / "bin").mkdir(parents=True)
    (install / "bin" / "QuantConnect.Lean.Launcher.dll").write_bytes(b"")
    (install / "lean-native.json").write_text(json.dumps({}))
    run = tmp_path / "home" / "run" / "gateway-d1.json.lock"
    with exclusive(run), pytest.raises(DeploymentError, match="already running"):
        deploy(stack["oms"], stack["sessions"], deployment="d1", strategy_dir=tmp_path, entry_file="Algo.cs",
               install_dir=install, home=tmp_path / "home")  # fmt: skip

"""quantlab_lean helper (ENG-009) against a recording stub of AlgorithmImports — NOT real LEAN."""

import importlib
import json
import sys
from pathlib import Path

import pytest

from quantlab.core.config import ExecutionModelSettings
from quantlab.engine.lean.docker import models_hash, prepare_library

STUB = Path(__file__).parents[2] / "fixtures" / "lean_stub"


@pytest.fixture
def lib(tmp_path, monkeypatch):
    def load(**overrides):
        models = ExecutionModelSettings(**overrides).model_dump()
        target = tmp_path / f"lib{len(list(tmp_path.iterdir()))}"
        sha = prepare_library(target, models)
        monkeypatch.syspath_prepend(str(target))
        monkeypatch.syspath_prepend(str(STUB))
        for name in ("quantlab_lean", "AlgorithmImports"):
            sys.modules.pop(name, None)
        module = importlib.import_module("quantlab_lean")
        stub = importlib.import_module("AlgorithmImports")
        algo = stub.StubAlgorithm()
        module.configure(algo)
        security = stub.StubSecurity()
        algo.initializer.initialize(security)
        return algo, security.events, sha, target

    yield load
    for name in ("quantlab_lean", "AlgorithmImports"):
        sys.modules.pop(name, None)


def test_defaults_keep_lean_brokerage_models(lib):
    algo, events, sha, target = lib()
    assert algo.brokerage_model == ("BrokerageName.DEFAULT", "AccountType.MARGIN")
    assert events == [("brokerage_defaults", algo.brokerage_model)]  # no overrides
    assert algo.logs[0].startswith(f"QUANTLAB_MODELS_APPLIED sha256={sha} ")
    data = json.loads((target / "quantlab_lean" / "models.json").read_text())
    assert data["sha256"] == models_hash(data["models"]) == sha


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"fee": "zero"}, [("fee", "ConstantFeeModel", [0])]),
        ({"fee": "constant", "fee_per_order": 2.0}, [("fee", "ConstantFeeModel", [2.0])]),
        ({"slippage": "none"}, [("slippage", "ConstantSlippageModel", [0])]),
        (
            {"slippage": "constant", "slippage_percent": 0.0005},
            [("slippage", "ConstantSlippageModel", [0.0005])],
        ),
        (
            {"slippage": "volume_share", "volume_limit": 0.05, "price_impact": 0.2},
            [("slippage", "VolumeShareSlippageModel", [0.05, 0.2])],
        ),
        ({"fill": "immediate"}, [("fill", "ImmediateFillModel", [])]),
        ({"settlement": "immediate"}, [("settlement", "ImmediateSettlementModel", [])]),
    ],
)
def test_overrides_map_to_lean_models(lib, overrides, expected):
    _, events, _, _ = lib(**overrides)
    assert [(k, n, list(a)) for k, n, a in events[1:]] == expected


def test_delayed_settlement_and_cash_ib_account(lib):
    from datetime import timedelta

    algo, events, _, _ = lib(
        brokerage="interactive_brokers",
        account_type="cash",
        settlement="delayed",
        settlement_days=2,
        settlement_hour=8,
    )
    assert algo.brokerage_model == ("BrokerageName.INTERACTIVE_BROKERS_BROKERAGE", "AccountType.CASH")
    assert events[1] == ("settlement", "DelayedSettlementModel", (2, timedelta(hours=8)))


def test_library_never_imports_quantlab():
    source = (
        Path(__file__).parents[3] / "src/quantlab/engine/lean/library/quantlab_lean/__init__.py"
    ).read_text()
    assert "import quantlab\n" not in source and "from quantlab" not in source

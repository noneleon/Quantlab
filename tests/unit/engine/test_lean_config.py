import json

import pytest

from quantlab.engine.base import AlgorithmLanguage, AlgorithmSpec, BacktestSpec, EngineError
from quantlab.engine.lean.config import build_backtest_config, write_config


def spec(tmp_path, **kw):
    algo = kw.pop("algorithm", AlgorithmSpec(tmp_path / "strat"))
    return BacktestSpec(run_id="01RUN", algorithm=algo, work_dir=tmp_path / "run", **kw)


def test_python_backtest_config(tmp_path):
    cfg = build_backtest_config(spec(tmp_path, parameters={"slow": "30", "fast": "10"}))
    assert cfg["environment"] == "backtesting"
    assert cfg["algorithm-language"] == "Python"
    assert cfg["algorithm-type-name"] == "main"
    assert cfg["algorithm-location"] == "/LeanCLI/main.py"
    assert cfg["algorithm-id"] == "01RUN"
    assert cfg["data-folder"] == "/Lean/Data"
    assert cfg["results-destination-folder"] == "/Results"
    assert cfg["close-automatically"] is True
    assert list(cfg["parameters"]) == ["fast", "slow"]


def test_no_quantconnect_credentials_ever(tmp_path):
    cfg = build_backtest_config(spec(tmp_path))
    assert cfg["api-access-token"] == "" and cfg["job-user-id"] == "0" and cfg["job-organization-id"] == ""
    assert "ApiDataProvider" not in json.dumps(cfg)  # never downloads/purchases QC data


def test_csharp_not_yet_supported(tmp_path):
    with pytest.raises(EngineError, match="ENG-008"):
        build_backtest_config(
            spec(tmp_path, algorithm=AlgorithmSpec(tmp_path, "Main.cs", AlgorithmLanguage.CSHARP))
        )


@pytest.mark.parametrize("entry", ["main.txt", "sub/main.py", "..\\main.py"])
def test_entry_file_must_be_local_py(tmp_path, entry):
    with pytest.raises(EngineError):
        build_backtest_config(spec(tmp_path, algorithm=AlgorithmSpec(tmp_path, entry)))


@pytest.mark.parametrize("params", [{"bad key": "1"}, {"x": 1}])
def test_parameters_validated(tmp_path, params):
    with pytest.raises(EngineError):
        build_backtest_config(spec(tmp_path, parameters=params))


def test_write_config_is_deterministic(tmp_path):
    cfg = build_backtest_config(spec(tmp_path, parameters={"b": "2", "a": "1"}))
    p1 = write_config(cfg, tmp_path / "a" / "c.json")
    p2 = write_config(dict(reversed(list(cfg.items()))), tmp_path / "b" / "c.json")
    assert p1.read_bytes() == p2.read_bytes()

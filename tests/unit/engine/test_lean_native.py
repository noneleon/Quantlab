"""Native LEAN adapter (ENG-003/008) without .NET: guards, data isolation, config. The real engine run is in
tests/integration/test_lean_native.py (QUANTLAB_TEST_LEAN_NATIVE)."""

import json
import sys
from pathlib import Path

import pytest

from quantlab.engine.base import AlgorithmLanguage, AlgorithmSpec, BacktestSpec, EngineError
from quantlab.engine.lean import native
from quantlab.engine.lean.config import LeanPaths, build_backtest_config
from quantlab.engine.lean.native import MANIFEST, LeanNativeAdapter


@pytest.fixture
def install(tmp_path):
    root = tmp_path / "lean-native"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / native.LAUNCHER).write_bytes(b"")
    for db in native.DATABASES:
        (root / "Data" / db).mkdir(parents=True)
        (root / "Data" / db / "db.txt").write_text(db)
    sample = root / "Data" / "equity" / "usa" / "minute" / "spy"
    sample.mkdir(parents=True)
    (sample / "20131007_trade.zip").write_bytes(b"sample")  # LEAN sample prices
    (root / MANIFEST).write_text(
        json.dumps({"lean_version": native.LEAN_VERSION, "lean_data_commit": "c" * 40})
    )
    return root


def spec(tmp_path, language=AlgorithmLanguage.CSHARP, entry="Algo.cs", **kw):
    d = tmp_path / "strategy"
    d.mkdir(exist_ok=True)
    (d / entry).write_text("// strategy")
    return BacktestSpec(
        run_id="r1", algorithm=AlgorithmSpec(d, entry, language), work_dir=tmp_path / "run", **kw
    )


def test_no_install_is_a_clear_error(tmp_path):
    with pytest.raises(EngineError, match="native-install"):
        LeanNativeAdapter(tmp_path / "missing").start_backtest(spec(tmp_path))


def test_custom_models_are_refused_for_csharp_on_the_native_launcher(tmp_path, install):
    adapter = LeanNativeAdapter(install)
    with pytest.raises(EngineError, match="custom execution models"):
        adapter.start_backtest(spec(tmp_path, models={"fill": "immediate", "slippage_bps": 5.0}))


def test_python_needs_an_install_with_leans_python_modules(tmp_path, install):
    with pytest.raises(EngineError, match="re-run `quantlab engine native-install`"):
        LeanNativeAdapter(install).start_backtest(spec(tmp_path, AlgorithmLanguage.PYTHON, "main.py"))


def test_python_runs_a_copy_with_this_interpreter_and_the_models_library(tmp_path, install, monkeypatch):
    """KI-0078: LEAN's pythonnet loads QuantLab's Python 3.11; the run executes its own copy of the files."""
    (install / "bin" / native.PYTHON_SUPPORT).write_text("# LEAN")
    started = {}

    class Proc:
        def poll(self):
            return 0

    def popen(argv, **kw):
        started.update(argv=argv, **kw)
        return Proc()

    monkeypatch.setattr(native.subprocess, "Popen", popen)
    s = spec(tmp_path, AlgorithmLanguage.PYTHON, "main.py", models={"fill": "immediate", "slippage_bps": 5.0})
    handle = LeanNativeAdapter(install).start_backtest(s)
    cfg = json.loads(handle.config_path.read_text())
    copy = handle.work_dir / "algorithm" / "main.py"
    assert cfg["algorithm-language"] == "Python" and cfg["algorithm-location"] == str(copy)
    assert copy.read_text() == "// strategy"
    (tmp_path / "strategy" / "main.py").write_text("# edited later")
    assert copy.read_text() == "// strategy"  # the run is fixed
    assert cfg["python-additional-paths"] == [str(handle.work_dir / "library")]
    assert (handle.work_dir / "library" / "quantlab_lean" / "models.json").is_file()
    env = started["env"]
    assert Path(env["PYTHONNET_PYDLL"]).is_file() and env["PYTHONHOME"] == sys.base_prefix
    assert env["PYTHONNOUSERSITE"] == "1"
    assert started["argv"][1:] == [native.LAUNCHER, "--config", str(handle.config_path)]


def test_python_runtime_refuses_other_python_versions(monkeypatch):
    monkeypatch.setattr(native, "PYTHON_VERSION", (3, 99))
    with pytest.raises(EngineError, match=r"Python 3\.99"):
        native.python_runtime()


def test_dataset_runs_never_see_lean_sample_prices(tmp_path, install):
    dataset = tmp_path / "export"
    (dataset / "equity" / "usa" / "daily").mkdir(parents=True)
    (dataset / "equity" / "usa" / "daily" / "aapl.zip").write_bytes(b"dataset")
    (dataset / "quantlab-lean-export.json").write_text("{}")
    adapter = LeanNativeAdapter(install)
    s = spec(tmp_path, data_dir=dataset)
    from quantlab.engine.lean.docker import run_layout

    folder = adapter._data_folder(s, run_layout(s))
    files = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
    assert "equity/usa/daily/aapl.zip" in files
    assert not any(f.startswith("equity/usa/minute") for f in files)  # no sample prices
    assert {f"{db}/db.txt" for db in native.DATABASES} <= files
    assert "quantlab-lean-export.json" not in files
    assert adapter._data_folder(spec(tmp_path), run_layout(s)) == install / "Data"  # sample run


def test_csharp_config_uses_the_compiled_assembly_and_class_name(tmp_path):
    s = spec(tmp_path, entry="BuyAndHold.cs")
    cfg = build_backtest_config(
        s, LeanPaths(algorithm_location="/x/QuantLabAlgorithm.dll", data_folder="/d/")
    )
    assert cfg["algorithm-language"] == "CSharp" and cfg["algorithm-type-name"] == "BuyAndHold"
    assert cfg["algorithm-location"] == "/x/QuantLabAlgorithm.dll" and cfg["data-folder"] == "/d/"
    with pytest.raises(EngineError, match="compiled assembly"):
        build_backtest_config(s)  # Docker has no C# compile step
    with pytest.raises(EngineError, match=r"\.cs file"):
        build_backtest_config(spec(tmp_path, entry="main.py"), LeanPaths(algorithm_location="/x.dll"))


def test_compile_errors_are_reported(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "Bad.cs").write_text("class {")

    class Done:
        returncode = 1
        stdout = "Bad.cs(1,7): error CS1001: Identifier expected [x.csproj]\n" * 2
        stderr = ""

    monkeypatch.setattr(native, "_run", lambda argv, **kw: Done())
    with pytest.raises(EngineError, match="does not compile") as exc:
        native.compile_csharp(src, tmp_path / "out")
    assert str(exc.value).count("CS1001") == 1  # duplicates removed
    csproj = (tmp_path / "out" / "project" / "QuantLabAlgorithm.csproj").read_text()
    assert native.LEAN_VERSION in csproj and 'ExcludeAssets="runtime"' in csproj


def test_install_never_overwrites(tmp_path, install):
    with pytest.raises(EngineError, match="already exists"):
        native.install(install, tmp_path)


def test_live_config_only_routes_through_quantlab_and_allowed_data_queues():
    kw = {"algorithm_location": "/a.dll", "type_name": "A", "algorithm_id": "x", "gateway_file": "/g.json",
          "data_folder": "/d/", "results": "/r", "storage": "/s"}  # fmt: skip
    cfg = native.build_live_config(data_queue_handler=next(iter(native.ALLOWED_DATA_QUEUES)), **kw)
    env = cfg["environments"][native.LIVE_ENVIRONMENT]
    assert env["live-mode-brokerage"] == "QuantLabBrokerage" and "live-mode-brokerage" not in cfg
    with pytest.raises(EngineError, match="not allowed"):
        native.build_live_config(data_queue_handler="InteractiveBrokersBrokerage", **kw)

"""BacktestService end to end with the fake docker executable (synthetic results, no real LEAN)."""

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from quantlab.backtest.models import BacktestRecordError, BacktestRun
from quantlab.backtest.service import BacktestError, BacktestService
from quantlab.core.config import load_settings
from quantlab.core.db import session_factory
from quantlab.engine.base import EngineError
from quantlab.engine.lean.docker import LeanDockerAdapter
from quantlab.strategies.registry import StrategyRegistry

IMAGE = "quantconnect/lean@sha256:" + "a" * 64
OTHER_IMAGE = "quantconnect/lean@sha256:" + "c" * 64
FAKE = [sys.executable, str(Path(__file__).parents[2] / "fixtures" / "fake_docker.py")]


@pytest.fixture
def env(ql_home, db_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(tmp_path / "fake"))
    strat = tmp_path / "strategies" / "sma"
    strat.mkdir(parents=True)
    (strat / "main.py").write_text("# algo v1\n")
    (strat / "strategy.toml").write_text('[parameters]\nfast = "10"\nslow = "30"\n')
    sessions = session_factory(db_engine)
    with sessions() as s:
        StrategyRegistry().register(s, "sma", strat)
        s.commit()
    settings = load_settings()

    def service(image=IMAGE):
        return BacktestService(settings, sessions, LeanDockerAdapter(image, docker_command=FAKE))

    return {
        "service": service,
        "sessions": sessions,
        "strategy_dir": strat,
        "settings": settings,
        "engine": db_engine,
    }


def test_successful_run_records_everything(env):
    run = env["service"]().run("sma", {"fast": "5"})
    assert run.status == "succeeded" and run.engine_status == "Completed" and run.error is None
    assert run.json("parameters") == {"fast": "5", "slow": "30"}
    assert run.data_provenance == "sample"
    assert run.finished_at is not None and run.result_hash and run.inputs_hash

    run_dir = Path(run.run_dir)
    assert run_dir.parent == env["settings"].layout.runs_dir
    assert (run_dir / "algorithm" / "main.py").read_text() == "# algo v1\n"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest == run.json("manifest")
    assert manifest["data"]["provenance"] == "sample" and "not suitable" in manifest["data"]["note"]
    assert manifest["engine"]["identity"]["image"] == IMAGE
    assert manifest["inputs"]["parameters"] == {"fast": "5", "slow": "30"}

    metrics = run.json("metrics")
    assert metrics["total_return"] == pytest.approx(99839.57 / 100000.0 - 1)
    checks = {c["metric"]: c["status"] for c in run.json("cross_checks")}
    assert checks["total_return"] == "match"  # synthetic fixture: Net Profit -0.160 %
    assert set(checks) == {"total_return", "cagr", "max_drawdown"}
    assert json.loads((run_dir / "metrics.json").read_text())["data_provenance"] == "sample"
    assert run.json("engine_statistics")["Sharpe Ratio"] == "1.028"


def test_snapshot_isolates_run_from_later_edits(env):
    service = env["service"]()
    first = service.run("sma")
    (env["strategy_dir"] / "main.py").write_text("# algo v2\n")
    second = service.run("sma")
    assert first.strategy_version_id != second.strategy_version_id
    assert first.inputs_hash != second.inputs_hash
    assert (Path(first.run_dir) / "algorithm" / "main.py").read_text() == "# algo v1\n"


def test_rerun_reproduces_from_snapshot_even_after_edits(env):
    service = env["service"]()
    original = service.run("sma")
    (env["strategy_dir"] / "main.py").write_text("# edited after the run\n")
    rerun, report = service.rerun(original.id)
    assert report.reproduced and report.differences == []
    assert rerun.rerun_of == original.id and rerun.id != original.id
    assert rerun.strategy_version_id == original.strategy_version_id
    assert (Path(rerun.run_dir) / "algorithm" / "main.py").read_text() == "# algo v1\n"


def test_rerun_refused_when_engine_changed(env):
    original = env["service"]().run("sma")
    with pytest.raises(BacktestError, match="engine differs"):
        env["service"](OTHER_IMAGE).rerun(original.id)


def test_rerun_detects_tampered_snapshot(env):
    original = env["service"]().run("sma")
    (Path(original.run_dir) / "algorithm" / "main.py").write_text("# tampered\n")
    with pytest.raises(BacktestError, match="no longer matches"):
        env["service"]().rerun(original.id)


def test_compare_reports_differences(env):
    service = env["service"]()
    a = service.run("sma", {"fast": "5"})
    b = service.run("sma", {"fast": "6"})
    report = BacktestService.compare(a, b)
    assert not report.inputs_identical and report.results_identical  # fake engine ignores parameters
    assert "input 'parameters' differs" in report.differences


@pytest.mark.parametrize(("var", "value", "status"), [("FAKE_DOCKER_EXIT", "3", "failed")])
def test_engine_failure_status(env, monkeypatch, var, value, status):
    monkeypatch.setenv(var, value)
    run = env["service"]().run("sma")
    assert run.status == status and "code 3" in run.error and run.result_hash is None


def test_timeout_status(env, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_SLEEP", "30")
    service = BacktestService(
        env["settings"], env["sessions"], LeanDockerAdapter(IMAGE, docker_command=FAKE, stop_timeout_s=1)
    )
    assert service.run("sma", timeout_s=1.5).status == "timed_out"


def test_engine_unavailable_is_recorded_not_hidden(env, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_DAEMON", "0")
    run = env["service"]().run("sma")
    assert run.status == "engine_error" and "daemon not reachable" in run.error


def test_algorithm_runtime_error(env, tmp_path, monkeypatch):
    fixture = json.loads((Path(__file__).parents[2] / "fixtures" / "lean" / "sample_result.json").read_text())
    fixture["state"] = {"Status": "RuntimeError", "RuntimeError": "ZeroDivisionError"}

    class ErrorAdapter(LeanDockerAdapter):
        def collect(self, handle):
            (handle.results_dir / f"{handle.run_id}.json").write_text(json.dumps(fixture))
            return super().collect(handle)

    service = BacktestService(env["settings"], env["sessions"], ErrorAdapter(IMAGE, docker_command=FAKE))
    run = service.run("sma")
    assert run.status == "algorithm_error" and run.runtime_error == "ZeroDivisionError"


def test_unknown_parameters_and_archived_strategy(env):
    service = env["service"]()
    with pytest.raises(ValueError, match="unknown parameter"):
        service.run("sma", {"typo": "1"})
    runs_dir = env["settings"].layout.runs_dir
    assert not runs_dir.exists() or not any(runs_dir.iterdir())  # rejected request leaves no run directory
    with env["sessions"]() as s:
        StrategyRegistry().archive(s, "sma")
        s.commit()
    with pytest.raises(BacktestError, match="archived"):
        service.run("sma")


def test_terminal_runs_are_immutable_and_undeletable(env):
    run = env["service"]().run("sma")
    with env["sessions"]() as s:
        row = s.get(BacktestRun, run.id)
        row.status = "running"
        with pytest.raises(BacktestRecordError, match="immutable"):
            s.flush()
        s.rollback()
        s.delete(s.get(BacktestRun, run.id))
        with pytest.raises(BacktestRecordError, match="never deleted"):
            s.flush()
    for sql in (
        f"UPDATE backtest_runs SET error='x' WHERE id='{run.id}'",
        "DELETE FROM backtest_runs",
        "DELETE FROM strategy_versions",
        "UPDATE strategy_versions SET tree_hash='x'",
        "DELETE FROM strategies",
    ):
        with pytest.raises(DatabaseError), env["engine"].begin() as c:
            c.execute(text(sql))


def test_get_and_list(env):
    service = env["service"]()
    a, b = service.run("sma"), service.run("sma")
    assert [r.id for r in service.list("sma")] == [b.id, a.id]
    assert service.get(a.id).id == a.id
    with pytest.raises(BacktestError, match="unknown backtest"):
        service.get("nope")


def test_engine_error_from_start_is_recorded(env):
    class BrokenStart(LeanDockerAdapter):
        def start_backtest(self, spec):
            raise EngineError("boom")

    service = BacktestService(env["settings"], env["sessions"], BrokenStart(IMAGE, docker_command=FAKE))
    run = service.run("sma")
    assert run.status == "engine_error" and run.error == "boom"


@pytest.fixture
def dataset(env, tmp_path):
    """A synthetic daily SPY dataset in the catalog (provenance=synthetic)."""
    from quantlab.data.catalog import DataCatalog
    from quantlab.data.providers.base import BarRequest, LicenseInfo
    from quantlab.data.providers.csv_file import CsvFileProvider
    from quantlab.data.schema import AssetClass, PriceAdjustment, Provenance, Resolution
    from tests.unit.data.conftest import csv_text, daily_rows

    catalog = DataCatalog(env["settings"].data_root)
    csv = tmp_path / "spy.csv"
    csv.write_text(csv_text(daily_rows()))
    with env["sessions"]() as s:
        catalog.create_dataset(
            s,
            "spy-daily",
            asset_class=AssetClass.EQUITY,
            market="usa",
            resolution=Resolution.DAILY,
            provenance=Provenance.SYNTHETIC,
            price_adjustment=PriceAdjustment.RAW,
            source="fixture",
            license=LicenseInfo("t", False),
        )
        p = CsvFileProvider(csv, calendar="XNYS", symbol="SPY")
        version = catalog.import_bars(
            s, "spy-daily", p, p.fetch_bars(BarRequest((), Resolution.DAILY))
        ).version
        s.commit()
        return {"name": "spy-daily", "hash": version.content_hash, "catalog": catalog}


def test_run_on_dataset_records_provenance_and_mounts_export(env, dataset, tmp_path):
    run = env["service"]().run("sma", dataset="spy-daily")
    assert run.status == "succeeded"
    assert run.data_provenance == "synthetic" and run.data_version == dataset["hash"]
    data = run.json("manifest")["data"]
    assert (
        data["dataset"]["name"] == "spy-daily"
        and data["export_hash"]
        and "not a performance claim" in data["note"]
    )
    assert run.json("manifest")["inputs"]["data_version"] == dataset["hash"]
    seen = json.loads((tmp_path / "fake" / f"{run.id}-dataset.json").read_text())
    assert "equity/usa/daily/spy.zip" in seen and "equity/usa/factor_files/spy.csv" in seen
    sample_run = env["service"]().run("sma")
    assert sample_run.inputs_hash != run.inputs_hash and sample_run.data_provenance == "sample"


def test_dataset_run_reruns_reproducibly_and_detects_tampered_export(env, dataset):
    service = env["service"]()
    original = service.run("sma", dataset="spy-daily", dataset_version=dataset["hash"][:12])
    rerun, report = service.rerun(original.id)
    assert report.reproduced and rerun.data_version == original.data_version
    export_dir = env["settings"].data_root / "lean" / "spy-daily" / dataset["hash"][:20]
    (export_dir / "equity" / "usa" / "daily" / "spy.zip").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="modified"):
        service.rerun(original.id)


def test_dataset_argument_errors(env, dataset):
    service = env["service"]()
    with pytest.raises(BacktestError, match="without dataset"):
        service.run("sma", dataset_version="abc")
    from quantlab.data.catalog import DataError

    with pytest.raises(DataError, match="unknown dataset"):
        service.run("sma", dataset="nope")
    runs_dir = env["settings"].layout.runs_dir
    assert not runs_dir.exists() or not any(runs_dir.iterdir())


def test_manifest_records_normalization_from_export(env, dataset):
    run = env["service"]().run("sma", dataset="spy-daily")
    data = run.json("manifest")["data"]
    assert data["corporate_actions"] == 0 and data["normalization"].startswith("neutral factor files")

"""Research API over the catalog (synthetic fixture data, provenance=synthetic)."""

import json

import pandas as pd
import pytest

from quantlab.bootstrap import initialize
from quantlab.data.providers.base import BarRequest, LicenseInfo
from quantlab.data.providers.csv_file import CsvFileProvider
from quantlab.data.schema import AssetClass, PriceAdjustment, Provenance, Resolution
from quantlab.research import NonRealDataWarning, Research
from quantlab.research.notebooks import STARTER, ResearchError, jupyter_command, launch, prepare_workspace
from tests.unit.data.conftest import SESSIONS, csv_text, daily_rows


@pytest.fixture
def qr(ql_home, memory_keyring, tmp_path):
    initialize()
    r = Research.open()
    for sym, base in (("SPY", 100.0), ("QQQ", 300.0)):
        csv = tmp_path / f"{sym}.csv"
        csv.write_text(csv_text(daily_rows(SESSIONS, base=base)))
        with r._ctx.sessions() as s:
            if sym == "SPY":
                r._ctx.data.create_dataset(
                    s,
                    "etfs",
                    asset_class=AssetClass.EQUITY,
                    market="usa",
                    resolution=Resolution.DAILY,
                    provenance=Provenance.SYNTHETIC,
                    price_adjustment=PriceAdjustment.RAW,
                    source="fixture",
                    license=LicenseInfo("t", False),
                )
            p = CsvFileProvider(csv, calendar="XNYS", symbol=sym)
            r._ctx.data.import_bars(s, "etfs", p, p.fetch_bars(BarRequest((), Resolution.DAILY)))
            s.commit()
    yield r
    r.close()


def test_history_carries_provenance_and_warns(qr):
    with pytest.warns(NonRealDataWarning, match="synthetic"):
        bars = qr.history("etfs", symbols=["spy"])
    assert set(bars["symbol"]) == {"SPY"} and len(bars) == 6
    meta = bars.attrs["quantlab"]
    assert meta["provenance"] == "synthetic" and meta["dataset"] == "etfs" and len(meta["version"]) == 64


def test_closes_wide_and_time_filter(qr):
    with pytest.warns(NonRealDataWarning):
        wide = qr.closes("etfs", start="2024-07-02", end="2024-07-08 23:00")
    assert list(wide.columns) == ["QQQ", "SPY"] and len(wide) == 4
    assert str(wide.index.tz) == "UTC" and wide.attrs["quantlab"]["provenance"] == "synthetic"


def test_analytics_delegation(qr):
    with pytest.warns(NonRealDataWarning):
        wide = qr.closes("etfs")
    d = qr.distribution(wide["SPY"])
    assert d["count"] == 5
    assert qr.correlation(wide, min_periods=3).loc["SPY", "QQQ"] == pytest.approx(1.0, abs=0.01)
    m = qr.metrics(wide["SPY"])
    assert m.total_return == pytest.approx(106.0 / 101.0 - 1)
    assert qr.returns(wide).shape == (5, 2)


def test_datasets_and_backtests_listing(qr):
    ds = qr.datasets()
    assert ds.loc[0, "name"] == "etfs" and ds.loc[0, "versions"] == 2 and ds.loc[0, "rows"] == 12
    assert qr.backtests().empty


def test_workspace_created_once_and_never_overwritten(tmp_path):
    ws = prepare_workspace(tmp_path)
    nb = ws / STARTER
    content = json.loads(nb.read_text())
    assert content["nbformat"] == 4 and any(
        "Research.open()" in "".join(c["source"]) for c in content["cells"]
    )
    nb.write_text("user edits")
    prepare_workspace(tmp_path)
    assert nb.read_text() == "user edits"


def test_jupyter_command_binds_localhost_and_keeps_auth(tmp_path):
    cmd = jupyter_command(tmp_path, 9999, open_browser=False)
    assert "--ServerApp.ip=127.0.0.1" in cmd and "--ServerApp.port=9999" in cmd and "--no-browser" in cmd
    assert not any("token" in part.lower() or "password" in part.lower() for part in cmd)


def test_launch_without_jupyter_fails_clearly(tmp_path, monkeypatch):
    monkeypatch.setattr("quantlab.research.notebooks.jupyter_available", lambda: False)
    with pytest.raises(ResearchError, match="--extra research"):
        launch(tmp_path)


def test_launch_passes_home_and_command(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, env, check):
        seen.update(cmd=cmd, env=env)
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr("quantlab.research.notebooks.jupyter_available", lambda: True)
    monkeypatch.setattr("quantlab.research.notebooks.subprocess.run", fake_run)
    assert launch(tmp_path, port=8890, open_browser=False) == 0
    assert seen["env"]["QUANTLAB_HOME"] == str(tmp_path) and "--ServerApp.port=8890" in seen["cmd"]
    assert (tmp_path / "notebooks" / STARTER).is_file()


def test_cli_research_reports_missing_extra(ql_home, memory_keyring, monkeypatch):
    from typer.testing import CliRunner

    from quantlab.cli.main import app

    monkeypatch.setattr("quantlab.research.notebooks.jupyter_available", lambda: False)
    runner = CliRunner()
    assert runner.invoke(app, ["init"]).exit_code == 0
    r = runner.invoke(app, ["research", "--no-browser"])
    assert r.exit_code == 1 and "uv sync --extra research" in r.output


def test_research_module_is_read_only():
    import inspect

    import quantlab.research.api as api

    source = inspect.getsource(api)
    for forbidden in ("import_bars", "create_dataset", ".run(", ".rerun(", "export(", ".delete("):
        assert forbidden not in source, forbidden
    assert pd  # pandas used by fixtures


def test_warning_points_at_caller(qr):
    with pytest.warns(NonRealDataWarning) as record:
        qr.closes("etfs")
        qr.history("etfs")
    ours = [w.filename for w in record if issubclass(w.category, NonRealDataWarning)]
    assert ours == [__file__, __file__]

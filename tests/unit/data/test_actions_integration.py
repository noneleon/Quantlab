"""Corporate actions through import, versioning, adjusted reads and LEAN export (synthetic data)."""

import io
import json
import zipfile

import pandas as pd
import pytest

from quantlab.data.catalog import DataCatalog, DataError, DataValidationError
from quantlab.data.providers.base import BarRequest, LicenseInfo, ProviderError
from quantlab.data.providers.csv_file import CsvFileProvider, load_actions_csv
from quantlab.data.schema import AssetClass, PriceAdjustment, Provenance, Resolution
from quantlab.data.storage import version_hash
from tests.unit.data.conftest import csv_text

DAILY = BarRequest((), Resolution.DAILY)
# 2024-07-01 .. 07-09 sessions; 2:1 split on 07-05 (prev close 104); $1 dividend on 07-02 (prev close 100)
ROWS = [
    "2024-07-01,100,100,100,100,0,10",
    "2024-07-02,99,99,99,99,0,10",
    "2024-07-03,104,104,104,104,0,10",
    "2024-07-05,52,52,52,52,0,20",
    "2024-07-08,53,53,53,53,0,20",
    "2024-07-09,54,54,54,54,0,20",
]
ACTIONS = "symbol,ex_date,kind,value\nSPY,2024-07-02,dividend,1.0\nSPY,2024-07-05,split,2\n"


@pytest.fixture
def setup(db_session, tmp_path):
    catalog = DataCatalog(tmp_path / "data")

    def create(name="spy", adjustment=PriceAdjustment.RAW):
        catalog.create_dataset(
            db_session,
            name,
            asset_class=AssetClass.EQUITY,
            market="usa",
            resolution=Resolution.DAILY,
            provenance=Provenance.SYNTHETIC,
            price_adjustment=adjustment,
            source="fixture",
            license=LicenseInfo("t", False),
        )
        db_session.commit()

    def load(name="spy", rows=ROWS, actions_text=ACTIONS, **kw):
        bars = tmp_path / "bars.csv"
        bars.write_text(csv_text(rows))
        provider = CsvFileProvider(bars, calendar="XNYS", symbol="SPY")
        raw = provider.fetch_bars(DAILY)
        if actions_text is not None:
            path = tmp_path / "actions.csv"
            path.write_text(actions_text)
            from dataclasses import replace

            raw = replace(raw, actions=load_actions_csv(path, "SPY"))
        result = catalog.import_bars(db_session, name, provider, raw, **kw)
        db_session.commit()
        return result

    return catalog, create, load


def test_import_stores_actions_and_changes_hash(setup, db_session):
    catalog, create, load = setup
    create()
    result = load()
    assert "_corporate_actions.parquet" in result.version.json("files")
    assert any("2 corporate action" in n for n in result.notes)
    actions = catalog.actions(db_session, "spy")
    assert list(actions["kind"]) == ["dividend", "split"]
    create("spy-noact")
    plain = load("spy-noact", actions_text=None)
    assert "_corporate_actions.parquet" not in plain.version.json("files")


def test_versions_without_actions_keep_their_hash():
    meta, symbols = {"name": "x"}, {"SPY": "abc"}
    assert version_hash(meta, symbols) == version_hash(meta, symbols, None)
    assert version_hash(meta, symbols, "h") != version_hash(meta, symbols)


def test_adjusted_reads(setup, db_session):
    catalog, create, load = setup
    create()
    load()
    raw, _ = catalog.read(db_session, "spy")
    split, _ = catalog.read(db_session, "spy", adjust="split")
    total, _ = catalog.read(db_session, "spy", adjust="split_dividend")
    assert raw["close"].tolist() == [100, 99, 104, 52, 53, 54]
    assert split["close"].tolist() == pytest.approx([50, 49.5, 52, 52, 53, 54])
    assert total["close"].tolist() == pytest.approx([50 * 0.99, 49.5, 52, 52, 53, 54])
    window, _ = catalog.read(db_session, "spy", adjust="split", start=pd.Timestamp("2024-07-03"))
    assert window["close"].tolist() == pytest.approx([52, 52, 53, 54])  # factors use the full history


def test_later_import_keeps_actions_and_conflicts_are_rejected(setup, db_session):
    catalog, create, load = setup
    create()
    load()
    later = load(rows=[*ROWS, "2024-07-10,55,55,55,55,0,20"], actions_text=None)
    assert later.created and len(catalog.actions(db_session, "spy")) == 2
    with pytest.raises(DataValidationError, match="conflict"):
        load(actions_text="symbol,ex_date,kind,value\nSPY,2024-07-05,split,3\n")
    db_session.rollback()
    load(actions_text="symbol,ex_date,kind,value\nSPY,2024-07-05,split,3\n", on_conflict="replace")
    assert catalog.actions(db_session, "spy").set_index("kind").loc["split", "value"] == 3


def test_invalid_actions_quarantined(setup, db_session):
    catalog, create, load = setup
    create()
    with pytest.raises(DataValidationError, match="not below the previous close") as exc:
        load(actions_text="symbol,ex_date,kind,value\nSPY,2024-07-02,dividend,500\n")
    db_session.rollback()
    assert (exc.value.quarantine_dir / "report.json").is_file()
    assert catalog.versions(db_session, catalog.get(db_session, "spy")) == []


def test_actions_refused_for_adjusted_datasets(setup, db_session):
    _, create, load = setup
    create("spy-adj", PriceAdjustment.SPLIT_DIVIDEND_ADJUSTED)
    with pytest.raises(DataError, match="applied twice"):
        load("spy-adj")


def test_lean_export_writes_real_factor_files(setup, db_session):
    catalog, create, load = setup
    create()
    load()
    path, _, _, _ = catalog.lean_export(db_session, "spy")
    factor = (path / "equity" / "usa" / "factor_files" / "spy.csv").read_text().splitlines()
    # rows: prev session of the dividend (07-01 = first date, close 100), of the split (07-03, close 104), end
    assert factor == ["20240701,0.99,0.5,100", "20240703,1,0.5,104", "20501231,1,1,0"]
    manifest = json.loads((path / "quantlab-lean-export.json").read_text())
    assert manifest["corporate_actions"] == 2 and "factor files from 2" in manifest["normalization"]
    with zipfile.ZipFile(io.BytesIO((path / "equity/usa/daily/spy.zip").read_bytes())) as z:
        assert (
            z.read("spy.csv").decode().splitlines()[0] == "20240701 00:00,1000000,1000000,1000000,1000000,10"
        )


def test_csv_export_includes_actions(setup, db_session, tmp_path):
    catalog, create, load = setup
    create()
    load()
    files = catalog.export(db_session, "spy", tmp_path / "out", fmt="csv", acknowledge_license=True)
    assert "_corporate_actions.csv" in [f.name for f in files]
    assert json.loads((tmp_path / "out" / "manifest.json").read_text())["corporate_actions"] == 2


def test_load_actions_csv_variants(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("Date,Type,Amount\n2024-07-02,Dividend,0.5\n")
    df = load_actions_csv(p, "spy")
    assert df.iloc[0]["symbol"] == "SPY" and df.iloc[0]["kind"] == "dividend" and df.iloc[0]["value"] == 0.5
    p.write_text("ex_date,kind\n2024-07-02,split\n")
    with pytest.raises(ProviderError, match="needs columns"):
        load_actions_csv(p, "SPY")
    p.write_text("ex_date,kind,value\n2024-07-02,split,2\n")
    with pytest.raises(ProviderError, match="no symbol column"):
        load_actions_csv(p)
    p.write_text("ex_date,kind,value\n2024-07-02,spinoff,2\n")
    with pytest.raises(ProviderError, match="unknown corporate action kinds"):
        load_actions_csv(p, "SPY")

import json

import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from quantlab.core.audit import AuditEntry
from quantlab.data.catalog import DataCatalog, DataError, DataValidationError
from quantlab.data.providers.base import BarRequest, LicenseInfo
from quantlab.data.providers.csv_file import CsvFileProvider
from quantlab.data.schema import AssetClass, PriceAdjustment, Provenance, Resolution
from quantlab.data.storage import symbol_content_hash
from tests.unit.data.conftest import SESSIONS, csv_text, daily_rows

DAILY = BarRequest((), Resolution.DAILY)


@pytest.fixture
def catalog(tmp_path):
    return DataCatalog(tmp_path / "data")


@pytest.fixture
def ds(db_session, catalog):
    catalog.create_dataset(
        db_session,
        "spy-daily",
        asset_class=AssetClass.EQUITY,
        market="usa",
        resolution=Resolution.DAILY,
        provenance=Provenance.SYNTHETIC,
        price_adjustment=PriceAdjustment.RAW,
        source="test fixture",
        license=LicenseInfo("test data", redistribution_allowed=False),
    )
    db_session.commit()
    return "spy-daily"


def load(catalog, db_session, tmp_path, rows, name="f.csv", **kw):
    path = tmp_path / name
    path.write_text(csv_text(rows))
    provider = CsvFileProvider(path, calendar="XNYS", symbol="SPY")
    result = catalog.import_bars(db_session, "spy-daily", provider, provider.fetch_bars(DAILY), **kw)
    db_session.commit()
    return result


def test_create_dataset_validates(db_session, catalog, ds):
    d = catalog.get(db_session, ds)
    assert (d.calendar, d.provenance, d.price_adjustment) == ("XNYS", "synthetic", "raw")
    with pytest.raises(DataError, match="already exists"):
        catalog.create_dataset(
            db_session,
            ds,
            asset_class=AssetClass.EQUITY,
            market="usa",
            resolution=Resolution.DAILY,
            provenance=Provenance.SYNTHETIC,
            price_adjustment=PriceAdjustment.RAW,
            source="x",
            license=LicenseInfo("t", False),
        )
    with pytest.raises(ValueError):
        catalog.create_dataset(
            db_session,
            "Bad Name",
            asset_class=AssetClass.EQUITY,
            market="usa",
            resolution=Resolution.DAILY,
            provenance=Provenance.SYNTHETIC,
            price_adjustment=PriceAdjustment.RAW,
            source="x",
            license=LicenseInfo("t", False),
        )


def test_import_read_and_versions(db_session, catalog, ds, tmp_path):
    first = load(catalog, db_session, tmp_path, daily_rows(SESSIONS[:3]))
    assert first.created and first.version.row_count == 3
    second = load(catalog, db_session, tmp_path, daily_rows(SESSIONS[3:], base=103.0), name="g.csv")
    assert second.created and second.version.parent_id == first.version.id and second.version.row_count == 6
    assert second.version.content_hash != first.version.content_hash

    frame, version = catalog.read(db_session, ds)
    assert version.id == second.version.id and len(frame) == 6 and frame["ts"].is_monotonic_increasing
    old, _ = catalog.read(db_session, ds, version=first.version.content_hash[:12])
    assert len(old) == 3  # earlier version still readable, unchanged
    window, _ = catalog.read(db_session, ds, start=pd.Timestamp("2024-07-03"), end=pd.Timestamp("2024-07-06"))
    assert len(window) == 2
    validation = json.loads(second.version.validation)
    assert validation["ok"] and validation["completeness"] == {"SPY": 1.0}
    assert [e.action for e in db_session.query(AuditEntry)] == [
        "dataset.create",
        "dataset.import",
        "dataset.import",
    ]


def test_identical_import_creates_no_version(db_session, catalog, ds, tmp_path):
    load(catalog, db_session, tmp_path, daily_rows())
    again = load(catalog, db_session, tmp_path, daily_rows(), name="again.csv")
    assert not again.created and "no changes" in again.notes[-1]
    assert len(catalog.versions(db_session, catalog.get(db_session, ds))) == 1


def test_content_hash_is_independent_of_row_order(db_session, catalog, ds, tmp_path):
    rows = daily_rows()
    a = load(catalog, db_session, tmp_path, rows).version.content_hash
    frame, _ = catalog.read(db_session, ds)
    assert symbol_content_hash(frame) == symbol_content_hash(frame.iloc[::-1])
    assert a


def test_conflicting_revision_rejected_then_replaced(db_session, catalog, ds, tmp_path):
    load(catalog, db_session, tmp_path, daily_rows())
    revised = daily_rows()
    revised[0] = revised[0].replace(",101.00,", ",101.25,")
    with pytest.raises(DataValidationError, match="conflict") as exc:
        load(catalog, db_session, tmp_path, revised, name="rev.csv")
    db_session.rollback()
    assert (exc.value.quarantine_dir / "report.json").is_file()
    assert len(catalog.versions(db_session, catalog.get(db_session, ds))) == 1

    replaced = load(catalog, db_session, tmp_path, revised, name="rev2.csv", on_conflict="replace")
    assert replaced.created and replaced.version.conflict_policy == "replace"
    assert any("replaced" in n for n in json.loads(replaced.version.notes))
    frame, _ = catalog.read(db_session, ds)
    assert frame["close"].iloc[0] == 101.25


def test_invalid_batch_quarantined_not_imported(db_session, catalog, ds, tmp_path):
    rows = daily_rows()
    rows[1] = "2024-07-02,100,99,98,101,0,10"
    with pytest.raises(DataValidationError) as exc:
        load(catalog, db_session, tmp_path, rows)
    db_session.rollback()
    q = exc.value.quarantine_dir
    assert (q / "batch.csv").is_file() and json.loads((q / "report.json").read_text())["report"][
        "ok"
    ] is False
    assert catalog.versions(db_session, catalog.get(db_session, ds)) == []


def test_corruption_detected_on_read(db_session, catalog, ds, tmp_path):
    v = load(catalog, db_session, tmp_path, daily_rows()).version
    (tmp_path / "data" / "parquet" / ds / v.content_hash[:20] / "SPY.parquet").write_bytes(b"tampered")
    with pytest.raises(DataError, match="corrupted"):
        catalog.read(db_session, ds)


def test_export_respects_license_and_never_overwrites(db_session, catalog, ds, tmp_path):
    load(catalog, db_session, tmp_path, daily_rows())
    with pytest.raises(DataError, match="licence"):
        catalog.export(db_session, ds, tmp_path / "out", fmt="csv")
    files = catalog.export(db_session, ds, tmp_path / "out", fmt="csv", acknowledge_license=True)
    assert [f.name for f in files] == ["SPY.csv"]
    exported = pd.read_csv(files[0])
    assert len(exported) == 6 and exported["ts"].iloc[0].startswith("2024-07-01T20:00:00")
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert manifest["dataset"]["provenance"] == "synthetic"
    with pytest.raises(DataError, match="not empty"):
        catalog.export(db_session, ds, tmp_path / "out", fmt="parquet", acknowledge_license=True)
    pq_files = catalog.export(db_session, ds, tmp_path / "pq", fmt="parquet", acknowledge_license=True)
    assert pd.read_parquet(pq_files[0])["close"].tolist() == pd.read_csv(files[0])["close"].tolist()


def test_versions_and_datasets_are_permanent(db_session, db_engine, catalog, ds, tmp_path):
    load(catalog, db_session, tmp_path, daily_rows())
    for sql in (
        "UPDATE dataset_versions SET row_count=0",
        "DELETE FROM dataset_versions",
        "DELETE FROM datasets",
    ):
        with pytest.raises(DatabaseError), db_engine.begin() as c:
            c.execute(text(sql))


def test_unknown_dataset_and_version(db_session, catalog, ds):
    with pytest.raises(DataError, match="unknown dataset"):
        catalog.get(db_session, "nope")
    with pytest.raises(DataError, match="no versions"):
        catalog.read(db_session, ds)

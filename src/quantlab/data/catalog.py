"""Dataset catalog (DATA-003, DATA-004, DATA-006, DATA-016).

* ``datasets``: named, typed collections (asset class, market, resolution, calendar, provenance,
  price adjustment, source and licence). Provenance and adjustment are declared once and fixed.
* ``dataset_versions``: immutable, content-addressed snapshots. Every import creates a new version that
  is the previous version merged with the new batch (copy-on-write). Identical content ⇒ no new version.
* Validation errors quarantine the batch under ``data_root/quarantine/<ingest id>/`` with a report.
* Exports refuse licence-restricted data unless the caller acknowledges the licence.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from quantlab.core.audit import AuditLog
from quantlab.core.clock import Clock, SystemClock
from quantlab.core.db import Base, UTCDateTime
from quantlab.core.ids import new_id
from quantlab.data.calendars import check_calendar, default_calendar
from quantlab.data.providers.base import DataProvider, LicenseInfo, RawBars
from quantlab.data.schema import (
    BAR_COLUMNS,
    AssetClass,
    PriceAdjustment,
    Provenance,
    Resolution,
    normalize_symbol,
    validate_dataset_name,
)
from quantlab.data.storage import read_bars, symbol_content_hash, verify_files, version_hash, write_version
from quantlab.data.validation import MAX_EXAMPLES, Issue, ValidationReport, validate_bars

ConflictPolicy = Literal["reject", "replace"]


class DataError(RuntimeError):
    pass


class DataValidationError(DataError):
    def __init__(self, message: str, report: ValidationReport, quarantine_dir: Path) -> None:
        super().__init__(message)
        self.report = report
        self.quarantine_dir = quarantine_dir


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    asset_class: Mapped[str] = mapped_column(String(16), nullable=False)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    resolution: Mapped[str] = mapped_column(String(16), nullable=False)
    calendar: Mapped[str] = mapped_column(String(32), nullable=False)
    provenance: Mapped[str] = mapped_column(String(20), nullable=False)
    price_adjustment: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    license_terms: Mapped[str] = mapped_column(Text, nullable=False)
    redistribution_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    license_url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    def identity(self) -> dict[str, str]:
        """Fields that define what the data *means*; part of every version hash."""
        return {
            "name": self.name,
            "asset_class": self.asset_class,
            "market": self.market,
            "resolution": self.resolution,
            "calendar": self.calendar,
            "provenance": self.provenance,
            "price_adjustment": self.price_adjustment,
        }


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("dataset_id", "content_hash", name="uq_dataset_versions_content"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False, index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("dataset_versions.id"))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ingest_id: Mapped[str] = mapped_column(String(26), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    conflict_policy: Mapped[str] = mapped_column(String(16), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ts: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    end_ts: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    symbols: Mapped[str] = mapped_column(Text, nullable=False)  # JSON {symbol: content hash}
    files: Mapped[str] = mapped_column(Text, nullable=False)  # JSON {file: sha256}
    validation: Mapped[str] = mapped_column(Text, nullable=False)  # JSON ValidationReport of the merged data
    notes: Mapped[str] = mapped_column(Text, nullable=False)  # JSON [str]
    path: Mapped[str] = mapped_column(Text, nullable=False)

    def json(self, column: str) -> Any:
        return json.loads(getattr(self, column))


@dataclass
class ImportResult:
    dataset: str
    version: DatasetVersion | None  # None when the batch changed nothing
    created: bool
    report: ValidationReport
    notes: list[str] = field(default_factory=list)


class DataCatalog:
    def __init__(self, data_root: Path, clock: Clock | None = None, audit: AuditLog | None = None) -> None:
        self.root = data_root
        self.parquet_root = data_root / "parquet"
        self.quarantine_root = data_root / "quarantine"
        self._clock = clock or SystemClock()
        self._audit = audit or AuditLog(self._clock)

    # ---- datasets ------------------------------------------------------------------------------
    def create_dataset(
        self,
        session: Session,
        name: str,
        *,
        asset_class: AssetClass,
        market: str,
        resolution: Resolution,
        provenance: Provenance,
        price_adjustment: PriceAdjustment,
        source: str,
        license: LicenseInfo,
        calendar: str | None = None,
        description: str = "",
        actor: str = "owner",
    ) -> Dataset:
        validate_dataset_name(name)
        if session.execute(select(Dataset).where(Dataset.name == name)).scalar_one_or_none():
            raise DataError(f"dataset {name!r} already exists")
        if not source.strip():
            raise DataError("source must describe where the data came from")
        cal = check_calendar(calendar or default_calendar(asset_class, market))
        dataset = Dataset(
            id=new_id(),
            name=name,
            asset_class=AssetClass(asset_class).value,
            market=market.lower(),
            resolution=Resolution(resolution).value,
            calendar=cal,
            provenance=Provenance(provenance).value,
            price_adjustment=PriceAdjustment(price_adjustment).value,
            source=source,
            license_terms=license.terms,
            redistribution_allowed=license.redistribution_allowed,
            license_url=license.url,
            description=description,
            created_at=self._clock.now(),
        )
        session.add(dataset)
        session.flush()
        self._audit.record(
            session,
            actor_type="user",
            actor=actor,
            action="dataset.create",
            target=name,
            details=dataset.identity(),
        )
        return dataset

    def get(self, session: Session, name: str) -> Dataset:
        dataset = session.execute(select(Dataset).where(Dataset.name == name)).scalar_one_or_none()
        if dataset is None:
            raise DataError(f"unknown dataset {name!r}")
        return dataset

    def list_datasets(self, session: Session) -> list[Dataset]:
        return list(session.execute(select(Dataset).order_by(Dataset.name)).scalars())

    def versions(self, session: Session, dataset: Dataset) -> list[DatasetVersion]:
        query = (
            select(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id).order_by(DatasetVersion.id)
        )
        return list(session.execute(query).scalars())

    def latest(self, session: Session, dataset: Dataset) -> DatasetVersion | None:
        versions = self.versions(session, dataset)
        return versions[-1] if versions else None

    def resolve_version(self, session: Session, dataset: Dataset, version: str | None) -> DatasetVersion:
        """Latest version, or one identified by id or by a content-hash prefix (>= 8 chars)."""
        versions = self.versions(session, dataset)
        if not versions:
            raise DataError(f"dataset {dataset.name!r} has no versions yet")
        if version is None:
            return versions[-1]
        matches = [
            v
            for v in versions
            if v.id == version or (len(version) >= 8 and v.content_hash.startswith(version))
        ]
        if len(matches) != 1:
            raise DataError(f"version {version!r} of {dataset.name!r} not found or ambiguous")
        return matches[0]

    # ---- import --------------------------------------------------------------------------------
    def import_bars(
        self,
        session: Session,
        dataset_name: str,
        provider: DataProvider,
        raw: RawBars,
        *,
        on_conflict: ConflictPolicy = "reject",
        actor: str = "owner",
    ) -> ImportResult:
        """Validate ``raw`` and publish a new version = latest version merged with the batch."""
        dataset = self.get(session, dataset_name)
        ingest_id = new_id()
        batch = raw.frame.copy()
        batch["symbol"] = batch["symbol"].map(normalize_symbol)
        resolution, calendar = Resolution(dataset.resolution), dataset.calendar

        cleaned, batch_report = validate_bars(batch, resolution, calendar)
        if not batch_report.ok:
            self._quarantine(ingest_id, batch, batch_report, dataset, raw)
            raise DataValidationError(
                f"batch rejected: {len(batch_report.errors)} validation error(s); quarantined as {ingest_id}",
                batch_report,
                self.quarantine_root / ingest_id,
            )

        parent = self.latest(session, dataset)
        notes = list(raw.notes)
        merged = cleaned
        if parent is not None:
            previous = read_bars(sorted(Path(parent.path).glob("*.parquet")))
            merged, merge_notes = self._merge(previous, cleaned, on_conflict, ingest_id, batch, dataset, raw)
            notes += merge_notes

        merged, report = validate_bars(merged, resolution, calendar)
        if not report.ok:  # e.g. replacement created an inconsistency; never publish
            self._quarantine(ingest_id, batch, report, dataset, raw)
            raise DataValidationError(
                "merged data failed validation; batch quarantined", report, self.quarantine_root / ingest_id
            )

        symbol_hashes = {str(s): symbol_content_hash(g) for s, g in merged.groupby("symbol", sort=True)}
        content = version_hash(dataset.identity(), symbol_hashes)
        if parent is not None and parent.content_hash == content:
            return ImportResult(
                dataset.name, parent, False, report, [*notes, "no changes: content identical"]
            )

        directory = self.parquet_root / dataset.name / content[:20]
        files = write_version(directory, merged)
        version = DatasetVersion(
            id=new_id(),
            dataset_id=dataset.id,
            parent_id=parent.id if parent else None,
            content_hash=content,
            created_at=self._clock.now(),
            ingest_id=ingest_id,
            source=raw.source,
            conflict_policy=on_conflict,
            row_count=len(merged),
            start_ts=merged["ts"].min().to_pydatetime(),
            end_ts=merged["ts"].max().to_pydatetime(),
            symbols=json.dumps(symbol_hashes, sort_keys=True),
            files=json.dumps(files, sort_keys=True),
            validation=json.dumps(report.to_dict(), sort_keys=True, default=str),
            notes=json.dumps(notes),
            path=str(directory),
        )
        session.add(version)
        session.flush()
        self._audit.record(
            session,
            actor_type="user",
            actor=actor,
            action="dataset.import",
            target=dataset.name,
            details={
                "version": content[:20],
                "rows": len(merged),
                "source": raw.source,
                "on_conflict": on_conflict,
                "provider": provider.name,
            },
        )
        return ImportResult(dataset.name, version, True, report, notes)

    def _merge(
        self,
        previous: pd.DataFrame,
        batch: pd.DataFrame,
        policy: ConflictPolicy,
        ingest_id: str,
        raw_batch: pd.DataFrame,
        dataset: Dataset,
        raw: RawBars,
    ) -> tuple[pd.DataFrame, list[str]]:
        key = ["symbol", "ts"]
        joined = previous.merge(batch, on=key, how="inner", suffixes=("_old", "_new"))
        value_cols = [c for c in BAR_COLUMNS if c not in key]
        differs = pd.Series(False, index=joined.index)
        for col in value_cols:
            old, new = joined[f"{col}_old"], joined[f"{col}_new"]
            differs |= ~((old == new) | (old.isna() & new.isna()))
        conflicts = int(differs.sum())
        notes: list[str] = []
        if conflicts and policy == "reject":
            report = ValidationReport(rows_in=len(batch))
            report.issues.append(_conflict_issue(joined[differs], conflicts))
            self._quarantine(ingest_id, raw_batch, report, dataset, raw)
            raise DataValidationError(
                f"{conflicts} row(s) conflict with the existing version; use on_conflict='replace' to accept "
                f"the new values (recorded in the version); quarantined as {ingest_id}",
                report,
                self.quarantine_root / ingest_id,
            )
        if conflicts:
            notes.append(f"{conflicts} existing row(s) replaced by new values (on_conflict=replace)")
        keep_old = previous.merge(batch[key], on=key, how="left", indicator=True)
        keep_old = keep_old[keep_old["_merge"] == "left_only"].drop(columns="_merge")
        merged = pd.concat([keep_old[list(BAR_COLUMNS)], batch[list(BAR_COLUMNS)]], ignore_index=True)
        return merged.sort_values(key, kind="stable").reset_index(drop=True), notes

    def _quarantine(
        self, ingest_id: str, batch: pd.DataFrame, report: ValidationReport, dataset: Dataset, raw: RawBars
    ) -> None:
        target = self.quarantine_root / ingest_id
        target.mkdir(parents=True, exist_ok=False)
        batch.to_csv(target / "batch.csv", index=False)
        (target / "report.json").write_text(
            json.dumps(
                {
                    "dataset": dataset.name,
                    "source": raw.source,
                    "notes": list(raw.notes),
                    "report": report.to_dict(),
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

    # ---- read / export -------------------------------------------------------------------------
    def read(
        self,
        session: Session,
        dataset_name: str,
        *,
        version: str | None = None,
        symbols: list[str] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        verify: bool = True,
    ) -> tuple[pd.DataFrame, DatasetVersion]:
        dataset = self.get(session, dataset_name)
        resolved = self.resolve_version(session, dataset, version)
        directory = Path(resolved.path)
        if verify:
            problems = verify_files(directory, resolved.json("files"))
            if problems:
                raise DataError(
                    f"version {resolved.content_hash[:20]} of {dataset_name} is corrupted: {problems}"
                )
        wanted = {normalize_symbol(s) for s in symbols} if symbols else None
        paths = [
            directory / f for f in sorted(resolved.json("files")) if wanted is None or Path(f).stem in wanted
        ]
        frame = read_bars(paths, pd.Timestamp(start) if start else None, pd.Timestamp(end) if end else None)
        return frame, resolved

    def export(
        self,
        session: Session,
        dataset_name: str,
        destination: Path,
        *,
        fmt: Literal["csv", "parquet"],
        version: str | None = None,
        acknowledge_license: bool = False,
        actor: str = "owner",
    ) -> list[Path]:
        """Write a version to ``destination`` (one file per symbol + manifest.json). Never overwrites."""
        dataset = self.get(session, dataset_name)
        if not dataset.redistribution_allowed and not acknowledge_license:
            raise DataError(
                f"dataset {dataset_name!r} licence does not allow redistribution "
                f"({dataset.license_terms!r}); "
                "pass acknowledge_license=True to export for your own use"
            )
        frame, resolved = self.read(session, dataset_name, version=version)
        if destination.exists() and any(destination.iterdir()):
            raise DataError(f"export destination is not empty: {destination}")
        destination.mkdir(parents=True, exist_ok=True)
        written = []
        for symbol, group in frame.groupby("symbol", sort=True):
            path = destination / f"{symbol}.{fmt}"
            if fmt == "csv":
                out = group.copy()
                out["ts"] = out["ts"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
                out.to_csv(path, index=False)
            else:
                shutil.copy2(Path(resolved.path) / f"{symbol}.parquet", path)
            written.append(path)
        manifest = {
            "dataset": dataset.identity(),
            "source": dataset.source,
            "license": dataset.license_terms,
            "version": resolved.content_hash,
            "rows": resolved.row_count,
            "format": fmt,
        }
        (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        self._audit.record(
            session,
            actor_type="user",
            actor=actor,
            action="dataset.export",
            target=dataset_name,
            details={
                "version": resolved.content_hash[:20],
                "destination": str(destination),
                "format": fmt,
                "license_acknowledged": acknowledge_license,
            },
        )
        return written


def _conflict_issue(rows: pd.DataFrame, count: int) -> Issue:
    examples = tuple(f"{r.symbol}@{r.ts.isoformat()}" for r in rows.head(MAX_EXAMPLES).itertuples())
    return Issue(
        "error", "revision_conflict", None, count, "values differ from the existing version", examples
    )

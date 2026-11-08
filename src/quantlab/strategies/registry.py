"""Strategy registry and versioning (STR-001, STR-002, STR-003).

* A strategy is a user-owned directory (DEC-0006) with a LEAN entry point and an optional
  ``strategy.toml`` describing its entry file, language and parameter defaults.
* Platform code never contains strategy logic; the registry only records where strategies live
  and which exact versions (tree hashes) have been used.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from quantlab.core.audit import AuditLog
from quantlab.core.clock import Clock, SystemClock
from quantlab.core.db import Base, UTCDateTime
from quantlab.core.ids import new_id
from quantlab.strategies.snapshot import GitInfo, TreeManifest, git_info, hash_tree

MANIFEST_FILE = "strategy.toml"
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_PARAM = re.compile(r"^[A-Za-z0-9_.\-]+$")
LANGUAGES = ("python", "csharp")


class StrategyError(ValueError):
    pass


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    entry_file: Mapped[str] = mapped_column(String(200), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    @property
    def directory(self) -> Path:
        return Path(self.path)


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (UniqueConstraint("strategy_id", "tree_hash", name="uq_strategy_versions_tree"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), nullable=False, index=True)
    tree_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    files: Mapped[str] = mapped_column(Text, nullable=False)  # JSON {relative path: sha256}
    git_commit: Mapped[str | None] = mapped_column(String(64))
    git_dirty: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    @property
    def file_hashes(self) -> dict[str, str]:
        result: dict[str, str] = json.loads(self.files)
        return result


@dataclass(frozen=True)
class StrategyManifest:
    """Contents of an optional ``strategy.toml``."""

    entry_file: str = "main.py"
    language: str = "python"
    description: str = ""
    parameters: dict[str, str] = field(default_factory=dict)
    declared: bool = False  # True if the file exists; then unknown run parameters are rejected


def _param_str(key: str, value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise StrategyError(
            f"parameter {key!r}: default must be a string or number, got {type(value).__name__}"
        )
    return str(value)


def load_manifest(directory: Path) -> StrategyManifest:
    path = directory / MANIFEST_FILE
    if not path.is_file():
        return StrategyManifest()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise StrategyError(f"invalid {MANIFEST_FILE} in {directory}: {exc}") from exc
    unknown = set(data) - {"strategy", "parameters"}
    if unknown:
        raise StrategyError(f"unknown sections in {path}: {sorted(unknown)}")
    meta = data.get("strategy", {})
    unknown_keys = set(meta) - {"entry_file", "language", "description"}
    if unknown_keys:
        raise StrategyError(f"unknown keys in [strategy] of {path}: {sorted(unknown_keys)}")
    params = {}
    for key, value in (data.get("parameters") or {}).items():
        if not _PARAM.match(key):
            raise StrategyError(f"invalid parameter name {key!r} in {path}")
        params[key] = _param_str(key, value)
    language = str(meta.get("language", "python"))
    if language not in LANGUAGES:
        raise StrategyError(f"language must be one of {LANGUAGES}, got {language!r}")
    return StrategyManifest(
        entry_file=str(meta.get("entry_file", "main.py")),
        language=language,
        description=str(meta.get("description", "")),
        parameters=params,
        declared=True,
    )


def resolve_parameters(manifest: StrategyManifest, overrides: dict[str, Any] | None) -> dict[str, str]:
    """Defaults from strategy.toml overlaid with run overrides (STR-003)."""
    resolved = dict(manifest.parameters)
    for key, value in (overrides or {}).items():
        if not _PARAM.match(key):
            raise StrategyError(f"invalid parameter name {key!r}")
        if manifest.declared and key not in manifest.parameters:
            raise StrategyError(f"unknown parameter {key!r}; declared: {sorted(manifest.parameters)}")
        resolved[key] = _param_str(key, value)
    return dict(sorted(resolved.items()))


class StrategyRegistry:
    def __init__(self, clock: Clock | None = None, audit: AuditLog | None = None) -> None:
        self._clock = clock or SystemClock()
        self._audit = audit or AuditLog(self._clock)

    def register(self, session: Session, name: str, path: Path, *, actor: str = "owner") -> Strategy:
        if not _NAME.match(name):
            raise StrategyError(f"invalid strategy name {name!r}: use lowercase letters, digits, '-' or '_'")
        directory = path.expanduser().resolve()
        if not directory.is_dir():
            raise StrategyError(f"strategy directory not found: {directory}")
        manifest = load_manifest(directory)
        if not (directory / manifest.entry_file).is_file():
            raise StrategyError(f"entry file {manifest.entry_file!r} not found in {directory}")
        if session.execute(select(Strategy).where(Strategy.name == name)).scalar_one_or_none():
            raise StrategyError(f"strategy {name!r} is already registered")
        strategy = Strategy(
            id=new_id(),
            name=name,
            path=str(directory),
            entry_file=manifest.entry_file,
            language=manifest.language,
            description=manifest.description,
            archived=False,
            created_at=self._clock.now(),
        )
        session.add(strategy)
        session.flush()
        self._audit.record(
            session,
            actor_type="user",
            actor=actor,
            action="strategy.register",
            target=name,
            details={"path": str(directory)},
        )
        return strategy

    def get(self, session: Session, name: str) -> Strategy:
        strategy = session.execute(select(Strategy).where(Strategy.name == name)).scalar_one_or_none()
        if strategy is None:
            raise StrategyError(f"unknown strategy {name!r}")
        return strategy

    def list(self, session: Session, include_archived: bool = False) -> list[Strategy]:
        query = select(Strategy).order_by(Strategy.name)
        if not include_archived:
            query = query.where(Strategy.archived.is_(False))
        return list(session.execute(query).scalars())

    def archive(self, session: Session, name: str, *, actor: str = "owner") -> Strategy:
        """Hide a strategy from listings. Nothing is deleted; its versions and runs stay intact."""
        strategy = self.get(session, name)
        strategy.archived = True
        self._audit.record(session, actor_type="user", actor=actor, action="strategy.archive", target=name)
        return strategy

    def record_version(
        self, session: Session, strategy: Strategy, manifest: TreeManifest, git: GitInfo | None = None
    ) -> StrategyVersion:
        """Return the version for this tree hash, creating it on first use.

        Safe under concurrency (parallel optimization trials): if another session inserts the same version
        first, the unique constraint fires inside a savepoint and the existing row is returned."""
        query = select(StrategyVersion).where(
            StrategyVersion.strategy_id == strategy.id, StrategyVersion.tree_hash == manifest.tree_hash
        )
        existing = session.execute(query).scalar_one_or_none()
        if existing is not None:
            return existing
        git = git or GitInfo(None, None)
        version = StrategyVersion(
            id=new_id(),
            strategy_id=strategy.id,
            tree_hash=manifest.tree_hash,
            files=json.dumps(manifest.files, sort_keys=True),
            git_commit=git.commit,
            git_dirty=git.dirty,
            created_at=self._clock.now(),
        )
        try:
            with session.begin_nested():
                session.add(version)
        except IntegrityError:
            raced = session.execute(query).scalar_one_or_none()
            if raced is None:
                raise
            return raced
        return version

    def current_version(self, session: Session, strategy: Strategy) -> StrategyVersion:
        """Hash the strategy directory as it is now and record that version."""
        return self.record_version(
            session, strategy, hash_tree(strategy.directory), git_info(strategy.directory)
        )

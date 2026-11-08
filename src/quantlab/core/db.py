"""Metadata database (CORE-004). SQLAlchemy 2 + Alembic; SQLite (WAL) default, PostgreSQL optional (DEC-0009).

Schema changes go through Alembic migrations in the top-level ``quantlab.migrations`` package — never
``create_all`` against a real database. ``migrations/env.py`` imports every module's models; it lives outside
``core`` because core may not import other modules (DEC-0013). The script location is resolved by package name
at runtime, so core keeps no static dependency on it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import DateTime, Engine, MetaData, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from quantlab.core.clock import ensure_utc

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UTCDateTime(TypeDecorator[datetime]):
    """Stores aware datetimes as UTC; always returns aware UTC (SQLite drops tzinfo otherwise)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return ensure_utc(value).replace(tzinfo=None) if dialect.name == "sqlite" else ensure_utc(value)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def create_db_engine(url: str, **kwargs: Any) -> Engine:
    """Create an engine. For file-based SQLite, the parent directory is created and WAL enabled."""
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        database = parsed.database
        if database and database != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, **kwargs)
        event.listen(engine, "connect", _sqlite_pragmas)
        return engine
    return create_engine(url, pool_pre_ping=True, **kwargs)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def _alembic_config(engine: Engine) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(files("quantlab.migrations")))
    # Alembic's Config is ConfigParser-based: '%' starts interpolation, so it must be escaped.
    # (Windows paths render as e.g. sqlite:///C%3A/... and would otherwise fail - CHANGE-0005.)
    url = engine.url.render_as_string(hide_password=False)
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def migrate(engine: Engine, revision: str = "head") -> None:
    """Apply migrations up to revision (idempotent)."""
    cfg = _alembic_config(engine)
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, revision)


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def head_revision(engine: Engine) -> str | None:
    return ScriptDirectory.from_config(_alembic_config(engine)).get_current_head()

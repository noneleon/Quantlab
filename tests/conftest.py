"""Shared fixtures. Every test gets an isolated QUANTLAB_HOME and a clean QUANTLAB_* environment."""

import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith("QUANTLAB_"):
            monkeypatch.delenv(key, raising=False)
    home = tmp_path / "qlhome"
    monkeypatch.setenv("QUANTLAB_HOME", str(home))
    return home


@pytest.fixture
def ql_home(_isolated_env):
    return _isolated_env


@pytest.fixture
def db_engine(ql_home):
    """A migrated SQLite database inside the isolated QUANTLAB_HOME."""
    from quantlab.core.config import load_settings
    from quantlab.core.db import create_db_engine, migrate

    engine = create_db_engine(load_settings().database_url)
    migrate(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    from quantlab.core.db import session_factory

    with session_factory(db_engine)() as session:
        yield session

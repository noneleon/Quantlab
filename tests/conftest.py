"""Shared fixtures. Every test gets an isolated QUANTLAB_HOME and a clean QUANTLAB_* environment."""

import os
from typing import ClassVar

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


class _MemoryKeyring:
    """In-memory keyring so tests never touch the real OS keyring (e.g. Windows Credential Manager)."""

    def __init__(self):
        import keyring.backend

        class Backend(keyring.backend.KeyringBackend):
            priority = 100
            store: ClassVar[dict] = {}

            def get_password(self, service, username):
                return self.store.get((service, username))

            def set_password(self, service, username, password):
                self.store[(service, username)] = password

            def delete_password(self, service, username):
                import keyring.errors

                if (service, username) not in self.store:
                    raise keyring.errors.PasswordDeleteError("not found")
                del self.store[(service, username)]

        Backend.store = {}
        self.backend = Backend()


@pytest.fixture(autouse=True)
def _isolated_keyring():
    import keyring

    previous = keyring.get_keyring()
    memory = _MemoryKeyring().backend
    keyring.set_keyring(memory)
    yield memory
    keyring.set_keyring(previous)


@pytest.fixture
def memory_keyring(_isolated_keyring):
    return _isolated_keyring

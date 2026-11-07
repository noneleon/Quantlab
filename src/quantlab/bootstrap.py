"""Application wiring shared by the CLI and the API (one composition root, no duplicated setup).

``AppContext`` loads settings, opens the metadata DB (migrating it), and builds services on demand.
The engine adapter is created lazily because it requires a pinned LEAN image, which commands such
as ``strategy add`` do not need.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import SecretStr
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from quantlab.backtest.service import BacktestService
from quantlab.core.config import Settings, load_settings
from quantlab.core.db import create_db_engine, current_revision, head_revision, migrate, session_factory
from quantlab.core.logging import configure_from_settings
from quantlab.core.secret_store import SecretError, SecretNotFoundError, SecretStore
from quantlab.data.catalog import DataCatalog
from quantlab.engine import EngineAdapter, create_engine_adapter
from quantlab.strategies.registry import StrategyRegistry
from quantlab.strategies.workspace import StrategyWorkspace

API_TOKEN_REF = "secret://quantlab/api/token"  # noqa: S105 - reference name, not a secret
DEFAULT_CONFIG = """# QuantLab configuration. Reference: docs/CONFIGURATION_REFERENCE.md
# Unknown keys are rejected. Secrets never go in this file (use the OS keyring / secret:// references).

[server]
host = "127.0.0.1"
port = 8765

[engine.lean]
launcher = "docker"
# Pin the LEAN image by digest (required before running backtests):
# image = "quantconnect/lean@sha256:<digest>"

[logging]
level = "INFO"
"""


class BootstrapError(RuntimeError):
    pass


@dataclass
class InitReport:
    home: Path
    created_config: bool
    db_revision: str | None
    token_created: bool
    token_storage: str  # "keyring" | "existing" | "unavailable"
    new_token: SecretStr | None = None  # only set when the keyring was unavailable and the user must store it


@dataclass
class AppContext:
    settings: Settings
    db: Engine
    sessions: sessionmaker[Session]
    secrets: SecretStore = field(default_factory=SecretStore)
    _engine: EngineAdapter | None = None

    @classmethod
    def open(
        cls,
        home: Path | str | None = None,
        *,
        configure_logging: bool = True,
        overrides: dict[str, Any] | None = None,
        secret_store: SecretStore | None = None,
    ) -> AppContext:
        settings = load_settings(home, overrides)
        if not settings.home.is_dir():
            raise BootstrapError(f"QuantLab home {settings.home} does not exist; run `quantlab init` first")
        settings.layout.ensure()
        if configure_logging:
            configure_from_settings(settings)
        db = create_db_engine(settings.database_url)
        if current_revision(db) != head_revision(db):
            migrate(db)
        return cls(settings, db, session_factory(db), secret_store or SecretStore())

    def close(self) -> None:
        self.db.dispose()

    # ---- services ------------------------------------------------------------------------------
    @property
    def registry(self) -> StrategyRegistry:
        return StrategyRegistry()

    @property
    def workspace(self) -> StrategyWorkspace:
        return StrategyWorkspace(self.registry)

    @property
    def data(self) -> DataCatalog:
        return DataCatalog(self.settings.data_root)

    @property
    def engine(self) -> EngineAdapter:
        if self._engine is None:
            self._engine = create_engine_adapter(self.settings)
        return self._engine

    def backtests(self) -> BacktestService:
        return BacktestService(self.settings, self.sessions, self.engine, self.registry)

    def read_only_backtests(self) -> BacktestService:
        """For queries only: works without a configured engine (running raises BacktestError)."""
        return BacktestService(self.settings, self.sessions, None, self.registry)

    # ---- API token (SEC-003) ---------------------------------------------------------------------
    def api_token(self) -> SecretStr | None:
        try:
            return self.secrets.get(API_TOKEN_REF)
        except SecretNotFoundError:
            return None

    def rotate_api_token(self, actor: str = "owner") -> tuple[SecretStr, str]:
        """Create a new token. Returns (token, storage) where storage is 'keyring' or 'unavailable'."""
        token = SecretStr(secrets.token_urlsafe(32))
        with self.sessions() as session:
            try:
                self.secrets.set(API_TOKEN_REF, token, session=session, actor=actor)
            except SecretError:  # keyring unavailable: nothing stored, audit entry rolled back
                session.rollback()
                return token, "unavailable"
            session.commit()
        return token, "keyring"


def initialize(home: Path | str | None = None, *, secret_store: SecretStore | None = None) -> InitReport:
    """Create (idempotently) the QuantLab home, default config, database and API token. Never overwrites."""
    settings = load_settings(home)
    settings.layout.ensure()
    created_config = False
    if not settings.layout.config_file.exists():
        settings.layout.config_file.write_text(DEFAULT_CONFIG, encoding="utf-8")
        created_config = True
    ctx = AppContext.open(settings.home, configure_logging=False, secret_store=secret_store)
    try:
        revision = current_revision(ctx.db)
        if ctx.api_token() is not None:
            return InitReport(settings.home, created_config, revision, False, "existing")
        token, storage = ctx.rotate_api_token()
        return InitReport(
            settings.home,
            created_config,
            revision,
            True,
            storage,
            token if storage == "unavailable" else None,
        )
    finally:
        ctx.close()

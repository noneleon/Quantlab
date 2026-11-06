"""QUANTLAB_HOME resolution and runtime directory layout (CORE-005).

All user-owned runtime state (database, market data, run artifacts, models, logs, backups)
lives under a single home directory outside the Git repository:

* ``QUANTLAB_HOME`` environment variable, if set;
* Windows: ``%LOCALAPPDATA%\\QuantLab``;
* elsewhere: ``$XDG_DATA_HOME/quantlab`` or ``~/.local/share/quantlab``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

HOME_ENV_VAR = "QUANTLAB_HOME"
CONFIG_FILENAME = "quantlab.toml"


def default_home(env: Mapping[str, str] | None = None, platform: str | None = None) -> Path:
    """Return the QuantLab home directory for this machine (not created)."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform

    explicit = env.get(HOME_ENV_VAR)
    if explicit:
        return Path(explicit).expanduser()

    if platform.startswith("win"):
        local_appdata = env.get("LOCALAPPDATA")
        base = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
        return base / "QuantLab"

    xdg = env.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "quantlab"


@dataclass(frozen=True)
class HomeLayout:
    """Canonical subdirectories of QUANTLAB_HOME. See docs/ARCHITECTURE.md §4."""

    root: Path

    @property
    def config_file(self) -> Path:
        return self.root / CONFIG_FILENAME

    @property
    def db_dir(self) -> Path:
        return self.root / "db"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def parquet_dir(self) -> Path:
        return self.data_dir / "parquet"

    @property
    def lean_data_dir(self) -> Path:
        return self.data_dir / "lean"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"

    def directories(self) -> tuple[Path, ...]:
        return (
            self.db_dir,
            self.parquet_dir,
            self.lean_data_dir,
            self.runs_dir,
            self.models_dir,
            self.logs_dir,
            self.backups_dir,
        )

    def ensure(self) -> HomeLayout:
        """Create all directories (idempotent). Never deletes or overwrites anything."""
        for directory in self.directories():
            directory.mkdir(parents=True, exist_ok=True)
        return self

    @classmethod
    def resolve(cls, root: Path | str | None = None) -> HomeLayout:
        return cls(Path(root).expanduser().resolve() if root is not None else default_home().resolve())

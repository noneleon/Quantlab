"""Engine abstraction (ENG-001). See docs/ARCHITECTURE.md §5.1 and DEC-0004.

Platform services depend only on these types, never on LEAN internals, so another engine
could be added later without redesigning backtesting, optimization or reporting.

Live/paper execution is intentionally absent here: it arrives in Phase 5 behind the OMS and
risk gate (DEC-0007), not as a direct engine call.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class EngineError(RuntimeError):
    """Engine unavailable, misconfigured, or failed to start."""


class AlgorithmLanguage(StrEnum):
    PYTHON = "python"
    CSHARP = "csharp"


@dataclass(frozen=True)
class AlgorithmSpec:
    """A strategy directory and its entry point (e.g. ``main.py``)."""

    directory: Path
    entry_file: str = "main.py"
    language: AlgorithmLanguage = AlgorithmLanguage.PYTHON

    @property
    def entry_path(self) -> Path:
        return self.directory / self.entry_file


@dataclass(frozen=True)
class BacktestSpec:
    """Everything an engine needs for one backtest run.

    ``data_dir`` is None to use the engine's bundled sample data (provenance ``sample``);
    otherwise it is an immutable LEAN-format dataset export, mounted read-only.
    """

    run_id: str
    algorithm: AlgorithmSpec
    work_dir: Path
    parameters: Mapping[str, str] = field(default_factory=dict)
    data_dir: Path | None = None
    # Execution models for the engine's helper library (ENG-009); None = engine defaults, no library.
    models: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class EngineInfo:
    """Identifies exactly which engine build ran (recorded in reproducibility manifests, ENG-006)."""

    name: str
    launcher: str
    image: str | None = None
    image_id: str | None = None
    repo_digests: tuple[str, ...] = ()
    created: str | None = None
    engine_runtime_version: str | None = None


@dataclass(frozen=True)
class RunHandle:
    run_id: str
    work_dir: Path
    results_dir: Path
    log_path: Path
    config_path: Path
    container_name: str | None = None


class RunState(StrEnum):
    SUCCEEDED = "succeeded"  # engine exited 0 (the algorithm may still report a runtime error)
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class RunOutcome:
    state: RunState
    exit_code: int | None
    duration_s: float
    detail: str = ""


@dataclass(frozen=True)
class EngineOrder:
    id: int
    time: datetime | None
    symbol: str
    type: str
    direction: str
    quantity: float
    price: float
    status: str
    tag: str = ""


@dataclass(frozen=True)
class EngineResult:
    """Engine-native results, parsed but not reinterpreted (QuantLab metrics live in analytics)."""

    algorithm_id: str
    status: str
    finished: bool
    runtime_error: str
    statistics: Mapping[str, str]
    runtime_statistics: Mapping[str, str]
    orders: tuple[EngineOrder, ...]
    equity: tuple[tuple[datetime, float], ...]
    start: datetime | None
    end: datetime | None
    parameters: Mapping[str, str]
    source_files: tuple[Path, ...]
    # Engine statistics as numbers (percentages as fractions; None if non-numeric), so services need not
    # understand an engine's text formats.
    statistic_values: Mapping[str, float | None] = field(default_factory=dict)
    # True/False: the algorithm confirmed/did not confirm applying the requested models; None: none requested.
    models_applied: bool | None = None

    @property
    def succeeded(self) -> bool:
        return self.finished and self.status == "Completed" and not self.runtime_error


@runtime_checkable
class EngineAdapter(Protocol):
    name: str

    def probe(self) -> EngineInfo:
        """Describe the exact engine build; raises EngineError if unavailable."""
        ...

    def start_backtest(self, spec: BacktestSpec) -> RunHandle:
        """Prepare the run directory and start the engine; returns without waiting."""
        ...

    def wait(self, handle: RunHandle, timeout_s: float | None = None) -> RunOutcome:
        """Block until the run ends or times out (a timed-out run is stopped)."""
        ...

    def cancel(self, handle: RunHandle) -> None: ...

    def collect(self, handle: RunHandle) -> EngineResult:
        """Parse the engine's result files for a finished run."""
        ...

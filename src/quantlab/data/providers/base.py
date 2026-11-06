"""Provider-independent data adapter interface (DATA-001).

Every provider declares its licence terms. QuantLab stores them with each dataset and refuses to
export data whose licence forbids redistribution unless the user explicitly acknowledges it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

import pandas as pd

from quantlab.data.schema import Resolution


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class LicenseInfo:
    terms: str  # short human-readable statement, e.g. "user-supplied file" or vendor ToS summary
    redistribution_allowed: bool
    url: str | None = None


@dataclass(frozen=True)
class BarRequest:
    symbols: tuple[str, ...]
    resolution: Resolution
    start: datetime | None = None
    end: datetime | None = None


@dataclass(frozen=True)
class RawBars:
    """Bars already in the canonical frame (see quantlab.data.schema) plus where they came from."""

    frame: pd.DataFrame
    source: str
    notes: tuple[str, ...] = field(default_factory=tuple)


@runtime_checkable
class DataProvider(Protocol):
    name: str
    license: LicenseInfo

    def fetch_bars(self, request: BarRequest) -> RawBars: ...

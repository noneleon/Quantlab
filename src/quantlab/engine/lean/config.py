"""LEAN ``config.json`` builder (ENG-004).

Keys and container paths follow LEAN's ``Launcher/config.json`` and the layout the Lean CLI uses
for local Docker backtests (verified against upstream sources 2026-11-06), so QuantLab runs the
engine the same way without needing ``lean login`` or a QuantConnect account (DEC-0003).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quantlab.engine.base import AlgorithmLanguage, BacktestSpec, EngineError

# Paths inside the LEAN container.
LEAN_ROOT = "/Lean/Launcher/bin/Debug"  # image WORKDIR; config.json is read from here
CONTAINER_CONFIG = f"{LEAN_ROOT}/config.json"
CONTAINER_DATA = "/Lean/Data"  # also where the image bundles LEAN's sample data
CONTAINER_DATASET = "/QuantLab/dataset"  # read-only mount of a QuantLab LEAN-format export
PRICE_RESOLUTIONS = ("tick", "second", "minute", "hour", "daily")

# Run inside the container when a dataset is supplied: remove *all* bundled sample price data (so a run can
# never silently read sample prices), keep LEAN's databases (market hours, symbol properties, ...), overlay
# the dataset, then start LEAN. The container is ephemeral (--rm); nothing on the host is modified.
DATASET_ENTRYPOINT_SCRIPT = (
    "set -eu; "
    "for market_dir in " + CONTAINER_DATA + "/*/*/; do "
    "for res in " + " ".join(PRICE_RESOLUTIONS) + '; do rm -rf "${market_dir}${res}"; done; '
    'rm -rf "${market_dir}map_files" "${market_dir}factor_files"; done; '
    "cp -R " + CONTAINER_DATASET + "/. " + CONTAINER_DATA + "/; "
    "rm -f " + CONTAINER_DATA + "/quantlab-lean-export.json; "
    "exec dotnet QuantConnect.Lean.Launcher.dll"
)
CONTAINER_RESULTS = "/Results"
CONTAINER_STORAGE = "/Storage"
CONTAINER_ALGORITHM = "/LeanCLI"  # same mount point as the Lean CLI uses for Python projects
CONTAINER_LIBRARY = "/QuantLab/library"  # read-only: quantlab_lean helper + models.json (ENG-009)

_PARAM_KEY = re.compile(r"^[A-Za-z0-9_.\-]+$")


@dataclass(frozen=True)
class LeanPaths:
    """Where LEAN finds things: container paths for Docker (default), host paths for the native launcher."""

    algorithm_location: str | None = None  # None: the entry file under CONTAINER_ALGORITHM
    data_folder: str = CONTAINER_DATA
    results: str = CONTAINER_RESULTS
    storage: str = CONTAINER_STORAGE
    library: str = CONTAINER_LIBRARY


def build_backtest_config(spec: BacktestSpec, paths: LeanPaths | None = None) -> dict[str, Any]:
    """Return the LEAN configuration for a local backtest of ``spec``.

    Python algorithms run from their ``.py`` entry file; C# algorithms (ENG-008) from the assembly the
    launcher compiled (``paths.algorithm_location``), with the entry file's stem as the class name.
    """
    paths = paths or LeanPaths()
    algo = spec.algorithm
    suffix = ".py" if algo.language is AlgorithmLanguage.PYTHON else ".cs"
    if not algo.entry_file.endswith(suffix) or "/" in algo.entry_file or "\\" in algo.entry_file:
        raise EngineError(
            f"entry_file must be a {suffix} file in the strategy directory, got {algo.entry_file!r}"
        )
    if algo.language is AlgorithmLanguage.CSHARP and paths.algorithm_location is None:
        raise EngineError("C# algorithms need a compiled assembly (native launcher, ENG-008)")
    for key, value in spec.parameters.items():
        if not _PARAM_KEY.match(key):
            raise EngineError(f"invalid parameter name {key!r}")
        if not isinstance(value, str):
            raise EngineError(f"parameter {key!r} must be a string (LEAN GetParameter semantics)")

    return {
        "environment": "backtesting",
        "algorithm-type-name": Path(algo.entry_file).stem,
        "algorithm-language": "Python" if algo.language is AlgorithmLanguage.PYTHON else "CSharp",
        "algorithm-location": paths.algorithm_location or f"{CONTAINER_ALGORITHM}/{algo.entry_file}",
        "algorithm-id": spec.run_id,  # LEAN names result files <algorithm-id>.json / -summary.json
        "data-folder": paths.data_folder,
        "results-destination-folder": paths.results,
        "object-store-root": paths.storage,
        "close-automatically": True,
        "debugging": False,
        "debugging-method": "LocalCmdline",
        "composer-dll-directory": ".",
        # Local, account-free operation: no QuantConnect credentials, ever.
        "job-user-id": "0",
        "api-access-token": "",
        "job-organization-id": "",
        "log-handler": "QuantConnect.Logging.CompositeLogHandler",
        "messaging-handler": "QuantConnect.Messaging.Messaging",
        "job-queue-handler": "QuantConnect.Queues.JobQueue",
        "api-handler": "QuantConnect.Api.Api",
        "map-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskMapFileProvider",
        "factor-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskFactorFileProvider",
        "data-provider": "QuantConnect.Lean.Engine.DataFeeds.DefaultDataProvider",
        "show-missing-data-logs": True,
        "parameters": dict(sorted(spec.parameters.items())),
        # LEAN Launcher/config.json key (checked in LEAN source cc98c42): extra folders on the Python path.
        "python-additional-paths": [paths.library] if spec.models is not None else [],
    }


def write_config(config: dict[str, Any], path: Path) -> Path:
    """Write deterministically (sorted keys) so identical runs produce identical config files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path

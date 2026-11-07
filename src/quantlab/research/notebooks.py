"""Local JupyterLab launcher (RES-001).

* Workspace: ``QUANTLAB_HOME/notebooks`` (user-owned, outside Git). A starter notebook is created once and
  never overwritten.
* JupyterLab is an optional dependency (``uv sync --extra research``); it binds to 127.0.0.1 and keeps
  Jupyter's own token authentication enabled — QuantLab never disables it.
* Notebooks run in QuantLab's Python environment, so ``from quantlab.research import Research`` works and
  reads the same catalog as backtests (``QUANTLAB_HOME`` is passed through).
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

STARTER = "getting_started.ipynb"


class ResearchError(RuntimeError):
    pass


def _cell(kind: str, source: str) -> dict[str, object]:
    cell: dict[str, object] = {
        "cell_type": kind,
        "metadata": {},
        "source": source.strip("\n").splitlines(True),
    }
    if kind == "code":
        cell.update({"execution_count": None, "outputs": []})
    return cell


def starter_notebook() -> dict[str, object]:
    cells = [
        _cell(
            "markdown",
            """
# QuantLab research — getting started
Data is loaded from QuantLab's **versioned catalog** (the same versions backtests use). Every frame carries
`frame.attrs["quantlab"]` with dataset, version and provenance; synthetic or sample data triggers a warning.
""",
        ),
        _cell(
            "code",
            """
from quantlab.research import Research

qr = Research.open()
qr.datasets()
""",
        ),
        _cell(
            "code",
            """
# Replace with one of your datasets (quantlab data create / import).
DATASET = "spy-daily"
closes = qr.closes(DATASET)
closes.attrs["quantlab"], closes.tail()
""",
        ),
        _cell(
            "code",
            """
symbol = closes.columns[0]
qr.distribution(closes[symbol])
""",
        ),
        _cell(
            "code",
            """
# Equity-curve metrics use the exact same implementation as backtest reports.
qr.metrics(closes[symbol])
""",
        ),
        _cell(
            "code",
            """
qr.backtests()
""",
        ),
    ]
    return {
        "cells": cells,
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def prepare_workspace(home: Path) -> Path:
    """Create ``<home>/notebooks`` and the starter notebook if missing (never overwrites)."""
    workspace = home / "notebooks"
    workspace.mkdir(parents=True, exist_ok=True)
    starter = workspace / STARTER
    if not starter.exists():
        starter.write_text(json.dumps(starter_notebook(), indent=1) + "\n", encoding="utf-8")
    return workspace


def jupyter_available() -> bool:
    return importlib.util.find_spec("jupyterlab") is not None


def jupyter_command(workspace: Path, port: int, open_browser: bool = True) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "jupyterlab",
        f"--notebook-dir={workspace}",
        "--ServerApp.ip=127.0.0.1",
        f"--ServerApp.port={port}",
    ]
    if not open_browser:
        command.append("--no-browser")
    return command


def launch(home: Path, port: int = 8888, open_browser: bool = True) -> int:
    """Run JupyterLab in the foreground; returns its exit code."""
    if not jupyter_available():
        raise ResearchError("JupyterLab is not installed; run `uv sync --extra research` and try again")
    workspace = prepare_workspace(home)
    env = {**os.environ, "QUANTLAB_HOME": str(home)}
    return subprocess.run(jupyter_command(workspace, port, open_browser), env=env, check=False).returncode

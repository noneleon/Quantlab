"""Shared helpers for M1 flow tests (fake docker engine, synthetic results)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "quantconnect/lean@sha256:" + "a" * 64
FAKE_DOCKER = [sys.executable, str(ROOT / "tests" / "fixtures" / "fake_docker.py")]
EXAMPLE_STRATEGY = ROOT / "strategies" / "examples" / "sma_crossover"


def configure_fake_engine(home: Path) -> None:
    """Append a pinned image + fake docker command to an initialised home's quantlab.toml."""
    config = home / "quantlab.toml"
    text = config.read_text(encoding="utf-8")
    command = ", ".join(f"'{part}'" for part in FAKE_DOCKER)
    text = text.replace(
        'launcher = "docker"', f'launcher = "docker"\nimage = "{IMAGE}"\ndocker_command = [{command}]'
    )
    config.write_text(text, encoding="utf-8")

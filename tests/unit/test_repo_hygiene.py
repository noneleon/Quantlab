"""Repository hygiene guards.

Regression test for CHANGE-0004: the `.gitignore` rule `secrets*` silently excluded
`src/quantlab/core/secrets.py` from Git, so CI ran without the module.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(), reason="needs a git checkout"
)
def test_no_source_or_test_file_is_gitignored():
    candidates = [
        str(p.relative_to(ROOT))
        for base in ("src", "tests", "tools")
        for p in (ROOT / base).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix in {".py", ".mako", ".typed", ".toml"}
    ]
    assert candidates
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        input="\n".join(candidates),
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    ignored = [line for line in result.stdout.splitlines() if line.strip()]
    assert ignored == [], f"source files matched by .gitignore (would not be committed): {ignored}"

"""Strategy directory hashing and snapshotting (STR-002).

A strategy version is identified by a **tree hash**: sha256 over the sorted list of
``<relative posix path>\\0<sha256 of file bytes>`` for every included file. Byte-exact on purpose:
the hash describes exactly what LEAN executes (note: Git ``core.autocrlf`` can change bytes on Windows).

Backtests run on a *copy* of the strategy taken at submission time, so editing the strategy while
a backtest runs (or later) can never change what a recorded run executed.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

EXCLUDED_DIRS = frozenset(
    {"__pycache__", ".git", ".ipynb_checkpoints", ".mypy_cache", ".pytest_cache", ".venv"}
)
EXCLUDED_FILES = frozenset({".DS_Store", "Thumbs.db"})
EXCLUDED_SUFFIXES = (".pyc", ".pyo")
MAX_TOTAL_BYTES = 50 * 1024 * 1024  # strategies are code; data belongs in the data catalog


class SnapshotError(ValueError):
    pass


@dataclass(frozen=True)
class TreeManifest:
    tree_hash: str
    files: dict[str, str]  # relative posix path -> sha256
    total_bytes: int


@dataclass(frozen=True)
class GitInfo:
    commit: str | None
    dirty: bool | None  # None when not in a git repository


def _included(directory: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(directory.rglob("*")):
        rel_parts = path.relative_to(directory).parts
        if any(part in EXCLUDED_DIRS for part in rel_parts):
            continue
        if path.is_symlink():
            raise SnapshotError(f"symlinks are not allowed in strategy directories: {path}")
        if not path.is_file() or path.name in EXCLUDED_FILES or path.name.endswith(EXCLUDED_SUFFIXES):
            continue
        files.append(path)
    return files


def hash_tree(directory: Path) -> TreeManifest:
    directory = directory.resolve()
    if not directory.is_dir():
        raise SnapshotError(f"strategy directory not found: {directory}")
    entries: dict[str, str] = {}
    total = 0
    for path in _included(directory):
        data = path.read_bytes()
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise SnapshotError(
                f"strategy directory exceeds {MAX_TOTAL_BYTES // (1024 * 1024)} MB: {directory}"
            )
        entries[path.relative_to(directory).as_posix()] = hashlib.sha256(data).hexdigest()
    if not entries:
        raise SnapshotError(f"strategy directory contains no files: {directory}")
    digest = hashlib.sha256()
    for rel in sorted(entries):
        digest.update(f"{rel}\0{entries[rel]}\n".encode())
    return TreeManifest(digest.hexdigest(), entries, total)


def copy_snapshot(source: Path, destination: Path) -> TreeManifest:
    """Copy the included files of ``source`` to a new ``destination`` and return the copy's manifest.

    The returned manifest is computed from the *copy*, and verified against the source manifest, so a
    file changing during the copy is detected instead of silently recorded.
    """
    before = hash_tree(source)
    if destination.exists():
        raise SnapshotError(f"snapshot destination already exists: {destination}")
    source = source.resolve()
    for rel in before.files:
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / rel, target)
    after = hash_tree(destination)
    if after.tree_hash != before.tree_hash:
        raise SnapshotError(f"strategy files changed while being copied: {source}")
    return after


def git_info(directory: Path) -> GitInfo:
    """Commit and dirty state of ``directory`` within its Git repository (read-only)."""
    git = shutil.which("git")
    if git is None:
        return GitInfo(None, None)

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([git, "-C", str(directory), *args], capture_output=True, text=True, check=False)

    head = run("rev-parse", "HEAD")
    if head.returncode != 0:
        return GitInfo(None, None)
    status = run("status", "--porcelain", "--untracked-files=all", "--", ".")
    return GitInfo(head.stdout.strip(), bool(status.stdout.strip()) if status.returncode == 0 else None)

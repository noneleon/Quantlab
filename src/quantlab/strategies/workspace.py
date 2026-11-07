"""Strategy source files for the in-app editor (UI-003): list, read, write, create from a template.

Safety rules (enforced here, not by the UI):

* Paths are relative to the strategy directory; absolute paths, ``..``, hidden segments, excluded folders
  (``__pycache__`` …) and symlinks are refused, and the resolved path must stay inside the directory.
* Only text source files with an allowed suffix are readable/writable, at most ``MAX_FILE_BYTES``, UTF-8.
* Writes are optimistic-concurrency checked: the caller sends the sha256 it last read (``None`` for a new
  file); if the file changed meanwhile (another editor, git checkout), the write is refused, not clobbered.
* ``strategy.toml`` is validated before it replaces the old one; Python syntax errors are reported, not
  blocked (work in progress must be savable; a run of broken code fails visibly in LEAN).
* Writes are atomic (temp file + replace) and audited (path and hashes, never contents).
* Archived strategies are read-only. Recorded runs are unaffected by edits: every run executes a snapshot.
"""

from __future__ import annotations

import ast
import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from sqlalchemy.orm import Session

from quantlab.core.audit import AuditLog
from quantlab.strategies.registry import (
    MANIFEST_FILE,
    Strategy,
    StrategyError,
    StrategyRegistry,
    load_manifest,
)
from quantlab.strategies.snapshot import EXCLUDED_DIRS, EXCLUDED_FILES, hash_tree

EDITABLE_SUFFIXES = (".py", ".toml", ".md", ".txt", ".json", ".cs", ".csproj")
MAX_FILE_BYTES = 512 * 1024
MAX_FILES = 500


class WorkspaceError(StrategyError):
    pass


class ConflictError(WorkspaceError):
    """The file changed since the caller read it."""


@dataclass(frozen=True)
class FileInfo:
    path: str
    size: int
    sha256: str
    editable: bool


@dataclass(frozen=True)
class FileContent:
    path: str
    content: str
    sha256: str


@dataclass(frozen=True)
class WriteResult:
    path: str
    sha256: str
    created: bool
    syntax_error: str | None  # Python files only; the file is saved anyway


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _relative(raw: str) -> PurePosixPath:
    if not raw or "\\" in raw or "\x00" in raw:
        raise WorkspaceError(f"invalid file path {raw!r}")
    rel = PurePosixPath(raw)
    if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
        raise WorkspaceError(f"file path must be relative and stay inside the strategy: {raw!r}")
    if any(part.startswith(".") or part in EXCLUDED_DIRS for part in rel.parts) or rel.name in EXCLUDED_FILES:
        raise WorkspaceError(f"hidden or excluded path not allowed: {raw!r}")
    if rel.suffix not in EDITABLE_SUFFIXES:
        raise WorkspaceError(f"only {', '.join(EDITABLE_SUFFIXES)} files can be edited, not {raw!r}")
    return rel


def _resolve(strategy: Strategy, raw: str) -> Path:
    rel = _relative(raw)
    root = strategy.directory.resolve()
    target = root.joinpath(*rel.parts)
    for parent in [target, *target.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            raise WorkspaceError(f"symlinks are not allowed in strategy directories: {raw!r}")
    if not target.resolve().is_relative_to(root):
        raise WorkspaceError(f"file path escapes the strategy directory: {raw!r}")
    return target


def bundled_templates_dir() -> Path | None:
    """``strategies/templates`` of a source checkout (STR-005); None when QuantLab runs from a wheel."""
    candidate = Path(__file__).resolve().parents[3] / "strategies" / "templates"
    return candidate if candidate.is_dir() else None


def list_templates(root: Path | None) -> dict[str, Path]:
    if root is None:
        return {}
    return {p.name: p for p in sorted(root.iterdir()) if (p / MANIFEST_FILE).is_file()}


class StrategyWorkspace:
    def __init__(self, registry: StrategyRegistry, audit: AuditLog | None = None) -> None:
        self._registry = registry
        self._audit = audit or AuditLog()

    def list_files(self, strategy: Strategy) -> list[FileInfo]:
        manifest = hash_tree(strategy.directory)
        out = []
        for rel, sha in sorted(manifest.files.items()):
            path = strategy.directory / rel
            editable = (
                PurePosixPath(rel).suffix in EDITABLE_SUFFIXES and path.stat().st_size <= MAX_FILE_BYTES
            )
            out.append(FileInfo(rel, path.stat().st_size, sha, editable))
        return out

    def read(self, strategy: Strategy, raw_path: str) -> FileContent:
        path = _resolve(strategy, raw_path)
        if not path.is_file():
            raise WorkspaceError(f"file not found: {raw_path!r}")
        data = path.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise WorkspaceError(f"file too large to edit ({len(data)} bytes > {MAX_FILE_BYTES})")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceError(f"{raw_path!r} is not UTF-8 text") from exc
        return FileContent(raw_path, text, _sha(data))

    def write(
        self,
        session: Session,
        strategy: Strategy,
        raw_path: str,
        content: str,
        expected_sha256: str | None,
        *,
        actor: str = "owner",
    ) -> WriteResult:
        if strategy.archived:
            raise WorkspaceError(f"strategy {strategy.name!r} is archived (read-only)")
        path = _resolve(strategy, raw_path)
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise WorkspaceError(f"file too large ({len(data)} bytes > {MAX_FILE_BYTES})")
        exists = path.is_file()
        current = _sha(path.read_bytes()) if exists else None
        if current != expected_sha256:
            raise ConflictError(
                f"{raw_path!r} changed since it was opened (now {current and current[:12]}, "
                f"expected {expected_sha256 and expected_sha256[:12]}); reload before saving"
            )
        if not exists and len(self.list_files(strategy)) >= MAX_FILES:
            raise WorkspaceError(f"strategy already has {MAX_FILES} files")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.quantlab-tmp")
        tmp.write_bytes(data)
        try:
            if path.name == MANIFEST_FILE and path.parent == strategy.directory.resolve():
                self._check_manifest(strategy, tmp)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
        syntax_error = None
        if path.suffix == ".py":
            try:
                ast.parse(content, filename=raw_path)
            except SyntaxError as exc:
                syntax_error = f"line {exc.lineno}: {exc.msg}"
        new_sha = _sha(data)
        self._audit.record(
            session,
            actor_type="user",
            actor=actor,
            action="strategy.file_write",
            target=strategy.name,
            details={"path": raw_path, "previous_sha256": current, "sha256": new_sha},
        )
        return WriteResult(raw_path, new_sha, not exists, syntax_error)

    @staticmethod
    def _check_manifest(strategy: Strategy, candidate: Path) -> None:
        """Validate a new strategy.toml in a scratch copy of the directory layout (entry file must exist)."""
        scratch = candidate.parent / ".quantlab-manifest-check"
        scratch.mkdir(exist_ok=False)
        try:
            shutil.copy2(candidate, scratch / MANIFEST_FILE)
            manifest = load_manifest(scratch)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        if not (strategy.directory / manifest.entry_file).is_file():
            raise WorkspaceError(f"entry_file {manifest.entry_file!r} does not exist in the strategy")
        if manifest.language != strategy.language or manifest.entry_file != strategy.entry_file:
            raise WorkspaceError(
                "changing entry_file or language of a registered strategy is not supported; "
                "register a new strategy"
            )

    def create_from_template(
        self, session: Session, name: str, template: Path, destination_root: Path, *, actor: str = "owner"
    ) -> Strategy:
        """Copy a template into ``destination_root/<name>`` (must not exist) and register it."""
        load_manifest(template)  # a broken template is refused before anything is written
        destination = destination_root / name
        if destination.exists():
            raise WorkspaceError(f"{destination} already exists; choose another name")
        manifest = hash_tree(template)
        destination.mkdir(parents=True)
        for rel in manifest.files:
            target = destination / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(template / rel, target)
        try:
            return self._registry.register(session, name, destination, actor=actor)
        except StrategyError:
            shutil.rmtree(destination)  # only the folder created just now
            raise

"""Strategy file workspace (UI-003): path safety, conflicts, manifest validation, audit, templates."""

import hashlib
import os

import pytest
from sqlalchemy import select

from quantlab.core.audit import AuditEntry
from quantlab.strategies.registry import StrategyError, StrategyRegistry
from quantlab.strategies.workspace import (
    ConflictError,
    StrategyWorkspace,
    WorkspaceError,
    bundled_templates_dir,
    list_templates,
)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def ws(db_session, tmp_path):
    root = tmp_path / "strat"
    root.mkdir()
    # bytes, not write_text: on Windows text mode would write CRLF and change the hashes
    (root / "main.py").write_bytes(b"x = 1\n")
    (root / "strategy.toml").write_bytes(b'[strategy]\nentry_file = "main.py"\n\n[parameters]\nfast = 10\n')
    registry = StrategyRegistry()
    strategy = registry.register(db_session, "s1", root)
    db_session.commit()
    return StrategyWorkspace(registry), strategy, db_session, root


def test_list_and_read(ws):
    w, s, _, _ = ws
    files = {f.path: f for f in w.list_files(s)}
    assert set(files) == {"main.py", "strategy.toml"} and files["main.py"].sha256 == sha("x = 1\n")
    content = w.read(s, "main.py")
    assert content.content == "x = 1\n" and content.sha256 == sha("x = 1\n")


@pytest.mark.parametrize(
    "bad",
    [
        "../evil.py",
        "/etc/passwd.py",
        "a/../../x.py",
        ".hidden.py",
        "__pycache__/x.py",
        "data.csv",
        "x.exe",
        "",
        "a\\b.py",
    ],
)
def test_path_safety(ws, bad):
    w, s, session, _ = ws
    with pytest.raises(WorkspaceError):
        w.write(session, s, bad, "print(1)", None)


def test_symlink_refused(ws, tmp_path):
    w, s, session, root = ws
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, root / "link")
    with pytest.raises(WorkspaceError, match="symlink"):
        w.write(session, s, "link/x.py", "1", None)
    assert not (outside / "x.py").exists()


def test_write_conflict_new_file_and_audit(ws):
    w, s, session, root = ws
    result = w.write(session, s, "main.py", "x = 2\n", sha("x = 1\n"))
    assert result.sha256 == sha("x = 2\n") and not result.created and result.syntax_error is None
    with pytest.raises(ConflictError, match="changed since it was opened"):
        w.write(session, s, "main.py", "x = 3\n", sha("x = 1\n"))  # stale hash
    assert (root / "main.py").read_bytes() == b"x = 2\n"
    with pytest.raises(ConflictError):
        w.write(session, s, "lib/helper.py", "y = 1\n", sha("anything"))  # new file must send None
    created = w.write(session, s, "lib/helper.py", "def f(:\n", None)
    assert created.created and created.syntax_error.startswith("line 1")  # saved anyway, error reported
    assert (root / "lib" / "helper.py").read_bytes() == b"def f(:\n"
    session.commit()
    entries = (
        session.execute(select(AuditEntry).where(AuditEntry.action == "strategy.file_write")).scalars().all()
    )
    assert len(entries) == 2 and "x = 2" not in entries[0].details  # path + hashes, never contents
    assert not list(root.rglob("*.quantlab-tmp"))


def test_manifest_validated_before_replacing(ws):
    w, s, session, root = ws
    original = (root / "strategy.toml").read_bytes().decode()
    for bad in ("[strategy\n", '[strategy]\nentry_file = "missing.py"\n', "[bogus]\nx = 1\n"):
        with pytest.raises(StrategyError):
            w.write(session, s, "strategy.toml", bad, sha(original))
        assert (root / "strategy.toml").read_bytes().decode() == original
    good = '[strategy]\nentry_file = "main.py"\n\n[parameters]\nfast = 12\n'
    assert w.write(session, s, "strategy.toml", good, sha(original)).sha256 == sha(good)
    assert not (root / ".quantlab-manifest-check").exists()


def test_archived_is_read_only_and_size_limit(ws):
    w, s, session, _ = ws
    with pytest.raises(WorkspaceError, match="too large"):
        w.write(session, s, "big.py", "#" * (512 * 1024 + 1), None)
    StrategyRegistry().archive(session, "s1")
    with pytest.raises(WorkspaceError, match="archived"):
        w.write(session, s, "main.py", "x = 5\n", sha("x = 1\n"))


def test_create_from_template(db_session, tmp_path):
    templates = list_templates(bundled_templates_dir())
    assert set(templates) == {"momentum_rotation", "rsi_mean_reversion", "pairs_zscore"}
    w = StrategyWorkspace(StrategyRegistry())
    s = w.create_from_template(db_session, "my-rsi", templates["rsi_mean_reversion"], tmp_path / "home")
    assert s.directory == (tmp_path / "home" / "my-rsi").resolve() and (s.directory / "main.py").is_file()
    with pytest.raises(WorkspaceError, match="already exists"):
        w.create_from_template(db_session, "my-rsi", templates["rsi_mean_reversion"], tmp_path / "home")
    with pytest.raises(Exception, match="invalid strategy name"):
        w.create_from_template(db_session, "Bad Name", templates["pairs_zscore"], tmp_path / "home")
    assert not (tmp_path / "home" / "Bad Name").exists()  # rolled back


def test_crlf_files_round_trip_byte_exact(ws):
    w, s, session, root = ws
    (root / "win.py").write_bytes(b"a = 1\r\nb = 2\r\n")
    content = w.read(s, "win.py")
    assert content.content == "a = 1\r\nb = 2\r\n"  # line endings preserved for the editor
    w.write(session, s, "win.py", content.content.replace("2", "3"), content.sha256)
    assert (root / "win.py").read_bytes() == b"a = 1\r\nb = 3\r\n"

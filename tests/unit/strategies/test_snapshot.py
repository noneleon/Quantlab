import os

import pytest

from quantlab.strategies.snapshot import SnapshotError, copy_snapshot, git_info, hash_tree


@pytest.fixture
def strat(tmp_path):
    d = tmp_path / "s"
    (d / "lib").mkdir(parents=True)
    (d / "main.py").write_text("print(1)\n")
    (d / "lib" / "helper.py").write_text("X = 1\n")
    return d


def test_hash_is_deterministic_and_content_sensitive(strat):
    a = hash_tree(strat)
    assert a == hash_tree(strat)
    assert set(a.files) == {"main.py", "lib/helper.py"}
    (strat / "main.py").write_text("print(2)\n")
    assert hash_tree(strat).tree_hash != a.tree_hash


def test_rename_changes_hash(strat):
    a = hash_tree(strat).tree_hash
    (strat / "lib" / "helper.py").rename(strat / "lib" / "helper2.py")
    assert hash_tree(strat).tree_hash != a


def test_excluded_files_do_not_change_hash(strat):
    a = hash_tree(strat).tree_hash
    (strat / "__pycache__").mkdir()
    (strat / "__pycache__" / "main.cpython-311.pyc").write_bytes(b"x")
    (strat / ".git").mkdir()
    (strat / ".git" / "HEAD").write_text("ref")
    (strat / "x.pyc").write_bytes(b"y")
    (strat / ".DS_Store").write_bytes(b"z")
    assert hash_tree(strat).tree_hash == a


def test_copy_snapshot_matches_and_refuses_existing(strat, tmp_path):
    (strat / "__pycache__").mkdir()
    (strat / "__pycache__" / "junk.pyc").write_bytes(b"x")
    m = copy_snapshot(strat, tmp_path / "copy")
    assert m.tree_hash == hash_tree(strat).tree_hash
    assert not (tmp_path / "copy" / "__pycache__").exists()
    with pytest.raises(SnapshotError, match="already exists"):
        copy_snapshot(strat, tmp_path / "copy")


def test_errors(tmp_path):
    with pytest.raises(SnapshotError, match="not found"):
        hash_tree(tmp_path / "missing")
    (tmp_path / "empty").mkdir()
    with pytest.raises(SnapshotError, match="no files"):
        hash_tree(tmp_path / "empty")


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_symlinks_rejected(strat):
    (strat / "link.py").symlink_to(strat / "main.py")
    with pytest.raises(SnapshotError, match="symlink"):
        hash_tree(strat)


def test_git_info_outside_repo(tmp_path):
    d = tmp_path / "nogit"
    d.mkdir()
    info = git_info(d)
    assert info.commit is None and info.dirty is None


def test_git_info_clean_and_dirty(strat):
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git not installed")

    def git(*a):
        subprocess.run(["git", "-C", str(strat), *a], check=True, capture_output=True)

    git("init", "-q")
    git("-c", "user.email=t@example.com", "-c", "user.name=t", "add", ".")
    git("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "init")
    clean = git_info(strat)
    assert clean.commit and len(clean.commit) == 40 and clean.dirty is False
    (strat / "main.py").write_text("changed\n")
    assert git_info(strat).dirty is True

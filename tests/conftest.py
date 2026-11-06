"""Shared fixtures. Every test gets an isolated QUANTLAB_HOME and a clean QUANTLAB_* environment."""

import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith("QUANTLAB_"):
            monkeypatch.delenv(key, raising=False)
    home = tmp_path / "qlhome"
    monkeypatch.setenv("QUANTLAB_HOME", str(home))
    return home


@pytest.fixture
def ql_home(_isolated_env):
    return _isolated_env

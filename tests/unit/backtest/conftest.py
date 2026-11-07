"""Shared fixtures for backtest tests (fake docker engine, synthetic results)."""

import sys
from pathlib import Path

import pytest

from quantlab.backtest.service import BacktestService
from quantlab.core.config import load_settings
from quantlab.core.db import session_factory
from quantlab.engine.lean.docker import LeanDockerAdapter
from quantlab.strategies.registry import StrategyRegistry

IMAGE = "quantconnect/lean@sha256:" + "a" * 64
OTHER_IMAGE = "quantconnect/lean@sha256:" + "c" * 64
FAKE = [sys.executable, str(Path(__file__).parents[2] / "fixtures" / "fake_docker.py")]


@pytest.fixture
def env(ql_home, db_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(tmp_path / "fake"))
    strat = tmp_path / "strategies" / "sma"
    strat.mkdir(parents=True)
    (strat / "main.py").write_text("# algo v1\n")
    (strat / "strategy.toml").write_text('[parameters]\nfast = "10"\nslow = "30"\n')
    sessions = session_factory(db_engine)
    with sessions() as s:
        StrategyRegistry().register(s, "sma", strat)
        s.commit()
    settings = load_settings()

    def service(image=IMAGE):
        return BacktestService(settings, sessions, LeanDockerAdapter(image, docker_command=FAKE))

    return {
        "service": service,
        "sessions": sessions,
        "strategy_dir": strat,
        "settings": settings,
        "engine": db_engine,
    }

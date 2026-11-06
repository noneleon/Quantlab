import json
import sys
from pathlib import Path

import pytest

from quantlab.core.config import load_settings
from quantlab.engine import create_engine_adapter
from quantlab.engine.base import AlgorithmSpec, BacktestSpec, EngineAdapter, EngineError, RunState
from quantlab.engine.lean.docker import LeanDockerAdapter, build_run_argv, run_layout

IMAGE = "quantconnect/lean@sha256:" + "a" * 64
FAKE = [sys.executable, str(Path(__file__).parents[2] / "fixtures" / "fake_docker.py")]


@pytest.fixture
def strategy(tmp_path):
    d = tmp_path / "strategies" / "s1"
    d.mkdir(parents=True)
    (d / "main.py").write_text("# algorithm\n")
    return d


@pytest.fixture
def fake(monkeypatch, tmp_path):
    state = tmp_path / "fake-state"
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(state))
    return state


def make_spec(tmp_path, strategy, run_id="01HRUN", **kw):
    return BacktestSpec(
        run_id=run_id, algorithm=AlgorithmSpec(strategy), work_dir=tmp_path / "runs" / run_id, **kw
    )


def calls(state):
    return [json.loads(line) for line in (state / "calls.jsonl").read_text().splitlines()]


def test_adapter_satisfies_protocol():
    assert isinstance(LeanDockerAdapter(IMAGE), EngineAdapter)


@pytest.mark.parametrize("image", ["quantconnect/lean:latest", "quantconnect/lean@sha256:abc"])
def test_requires_digest_pinned_image(image):
    with pytest.raises(EngineError, match="digest"):
        LeanDockerAdapter(image)


def test_argv_mounts_readonly_and_no_network(tmp_path, strategy):
    spec = make_spec(tmp_path, strategy, data_dir=tmp_path)
    handle = run_layout(spec)
    argv = build_run_argv(spec, handle, IMAGE)
    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[-1] == IMAGE
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]
    by_target = {dict(p.split("=", 1) for p in m.split(",") if "=" in p)["target"]: m for m in mounts}
    assert by_target["/LeanCLI"].endswith(",readonly")
    assert by_target["/Lean/Data"].endswith(",readonly")
    assert by_target["/Lean/Launcher/bin/Debug/config.json"].endswith(",readonly")
    assert not by_target["/Results"].endswith(",readonly")
    assert handle.container_name == "quantlab-01hrun"


def test_argv_without_data_dir_uses_bundled_sample_data(tmp_path, strategy):
    spec = make_spec(tmp_path, strategy)
    argv = build_run_argv(spec, run_layout(spec), IMAGE)
    assert not any("target=/Lean/Data" in a for a in argv)


def test_mount_paths_with_commas_rejected(tmp_path):
    d = tmp_path / "a,b"
    d.mkdir()
    (d / "main.py").write_text("")
    spec = make_spec(tmp_path, d)
    with pytest.raises(EngineError, match="--mount"):
        build_run_argv(spec, run_layout(spec), IMAGE)


def test_invalid_network_rejected(tmp_path, strategy):
    with pytest.raises(EngineError):
        LeanDockerAdapter(IMAGE, network="host")


def test_full_run_with_fake_docker(tmp_path, strategy, fake):
    adapter = LeanDockerAdapter(IMAGE, docker_command=FAKE)
    info = adapter.probe()
    assert info.image == IMAGE and info.image_id == "sha256:" + "b" * 64 and info.repo_digests == (IMAGE,)
    assert info.engine_runtime_version == "docker 29.9.2"

    handle = adapter.start_backtest(make_spec(tmp_path, strategy, parameters={"fast": "10"}))
    outcome = adapter.wait(handle, timeout_s=60)
    assert outcome.state is RunState.SUCCEEDED and outcome.exit_code == 0
    cfg = json.loads(handle.config_path.read_text())
    assert cfg["algorithm-id"] == "01HRUN" and cfg["parameters"] == {"fast": "10"}
    assert "LEAN ALGORITHMIC TRADING ENGINE" in handle.log_path.read_text()
    assert json.loads((handle.work_dir / "docker-command.json").read_text())[-1] == IMAGE
    result = adapter.collect(handle)
    assert result.succeeded and result.algorithm_id == "01HRUN" and len(result.orders) == 3


def test_engine_failure_reported(tmp_path, strategy, fake, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_EXIT", "3")
    adapter = LeanDockerAdapter(IMAGE, docker_command=FAKE)
    outcome = adapter.wait(adapter.start_backtest(make_spec(tmp_path, strategy)), timeout_s=60)
    assert outcome.state is RunState.FAILED and outcome.exit_code == 3


def test_timeout_stops_container(tmp_path, strategy, fake, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_SLEEP", "30")
    adapter = LeanDockerAdapter(IMAGE, docker_command=FAKE, stop_timeout_s=1)
    handle = adapter.start_backtest(make_spec(tmp_path, strategy))
    outcome = adapter.wait(handle, timeout_s=1.5)
    assert outcome.state is RunState.TIMED_OUT
    assert ["stop", "-t", "1", "quantlab-01hrun"] in calls(fake)


def test_default_timeout_applies_when_none_given(tmp_path, strategy, fake, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_SLEEP", "30")
    adapter = LeanDockerAdapter(IMAGE, docker_command=FAKE, stop_timeout_s=1, default_timeout_s=1.5)
    assert adapter.wait(adapter.start_backtest(make_spec(tmp_path, strategy))).state is RunState.TIMED_OUT


def test_cancel(tmp_path, strategy, fake, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_SLEEP", "30")
    adapter = LeanDockerAdapter(IMAGE, docker_command=FAKE, stop_timeout_s=1)
    handle = adapter.start_backtest(make_spec(tmp_path, strategy))
    import time

    deadline = time.monotonic() + 20
    while not (fake / "quantlab-01hrun.pid").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    adapter.cancel(handle)
    assert adapter.wait(handle, timeout_s=30).state is RunState.CANCELLED


def test_run_directory_never_reused(tmp_path, strategy, fake):
    adapter = LeanDockerAdapter(IMAGE, docker_command=FAKE)
    spec = make_spec(tmp_path, strategy)
    adapter.wait(adapter.start_backtest(spec), timeout_s=60)
    with pytest.raises(EngineError, match="already exists"):
        adapter.start_backtest(spec)


def test_missing_entry_file_and_data_dir(tmp_path, strategy, fake):
    adapter = LeanDockerAdapter(IMAGE, docker_command=FAKE)
    (strategy / "main.py").unlink()
    with pytest.raises(EngineError, match="entry file"):
        adapter.start_backtest(make_spec(tmp_path, strategy))
    (strategy / "main.py").write_text("")
    with pytest.raises(EngineError, match="data directory"):
        adapter.start_backtest(make_spec(tmp_path, strategy, data_dir=tmp_path / "nope"))


@pytest.mark.parametrize(
    ("var", "match"), [("FAKE_DOCKER_DAEMON", "daemon not reachable"), ("FAKE_DOCKER_IMAGE", "docker pull")]
)
def test_probe_errors(fake, monkeypatch, var, match):
    monkeypatch.setenv(var, "0")
    with pytest.raises(EngineError, match=match):
        LeanDockerAdapter(IMAGE, docker_command=FAKE).probe()


def test_missing_docker_executable(tmp_path):
    with pytest.raises(EngineError, match="not found"):
        LeanDockerAdapter(IMAGE, docker_command=["definitely-not-docker-xyz"]).probe()


def test_unknown_handle(tmp_path, strategy):
    adapter = LeanDockerAdapter(IMAGE)
    with pytest.raises(EngineError, match="unknown run"):
        adapter.wait(run_layout(make_spec(tmp_path, strategy)))


def test_factory_from_settings():
    with pytest.raises(EngineError, match=r"engine\.lean\.image is not set"):
        create_engine_adapter(load_settings())
    with pytest.raises(EngineError, match="ENG-003"):
        create_engine_adapter(load_settings(overrides={"engine": {"lean": {"launcher": "native"}}}))
    adapter = create_engine_adapter(
        load_settings(overrides={"engine": {"lean": {"image": IMAGE, "docker_command": ["podman"]}}})
    )
    assert isinstance(adapter, LeanDockerAdapter) and adapter.docker_command == ("podman",)
    assert adapter.default_timeout_s == 3600

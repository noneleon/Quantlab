import pytest

from quantlab.core.config import ConfigError, load_settings

DIGEST = "quantconnect/lean@sha256:" + "a" * 64


def write_toml(home, text):
    home.mkdir(parents=True, exist_ok=True)
    (home / "quantlab.toml").write_text(text, encoding="utf-8")


def test_defaults_are_safe(ql_home):
    s = load_settings()
    assert s.home == ql_home.resolve()
    assert s.trading.mode == "paper"
    assert s.server.host == "127.0.0.1"
    assert s.server.remote_access is False
    assert s.ai.provider == "none"
    assert s.database_url.startswith("sqlite:///") and s.database_url.endswith("db/quantlab.sqlite")
    assert s.data_root == ql_home.resolve() / "data"


def test_load_does_not_create_files(ql_home):
    load_settings()
    assert not ql_home.exists()


def test_precedence_defaults_toml_env_overrides(ql_home, monkeypatch):
    write_toml(ql_home, "[server]\nport = 9001\n[logging]\nlevel = 'DEBUG'\n")
    assert load_settings().server.port == 9001
    monkeypatch.setenv("QUANTLAB_SERVER__PORT", "9002")
    s = load_settings()
    assert s.server.port == 9002
    assert s.logging.level == "DEBUG"  # toml value kept where env does not override
    assert load_settings(overrides={"server": {"port": 9003}}).server.port == 9003


def test_explicit_home_argument(tmp_path):
    other = tmp_path / "other"
    write_toml(other, "[server]\nport = 7000\n")
    assert load_settings(other).server.port == 7000


def test_unknown_keys_rejected(ql_home):
    write_toml(ql_home, "[brokers.ibkr]\npassword = 'hunter2'\n")
    with pytest.raises(ConfigError):
        load_settings()


def test_malformed_toml_is_a_config_error(ql_home):
    write_toml(ql_home, "[server\nport=1")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_settings()


@pytest.mark.parametrize("source", ["toml", "env", "override"])
def test_live_mode_cannot_be_enabled_by_config(ql_home, monkeypatch, source):
    overrides = None
    if source == "toml":
        write_toml(ql_home, "[trading]\nmode = 'live'\n")
    elif source == "env":
        monkeypatch.setenv("QUANTLAB_TRADING__MODE", "live")
    else:
        overrides = {"trading": {"mode": "live"}}
    with pytest.raises(ConfigError, match="LIVE-003"):
        load_settings(overrides=overrides)


def test_non_loopback_bind_requires_remote_access():
    with pytest.raises(ConfigError, match="remote_access"):
        load_settings(overrides={"server": {"host": "0.0.0.0"}})
    s = load_settings(overrides={"server": {"host": "0.0.0.0", "remote_access": True}})
    assert s.server.host == "0.0.0.0"
    assert load_settings(overrides={"server": {"host": "::1"}}).server.host == "::1"
    assert load_settings(overrides={"server": {"host": "localhost"}}).server.host == "localhost"


@pytest.mark.parametrize(
    "image",
    ["quantconnect/lean:latest", "quantconnect/lean", "quantconnect/lean@sha256:abc", "x@sha256:" + "G" * 64],
)
def test_lean_image_must_be_digest_pinned(image):
    with pytest.raises(ConfigError, match="digest"):
        load_settings(overrides={"engine": {"lean": {"image": image}}})


def test_lean_image_digest_accepted():
    assert load_settings(overrides={"engine": {"lean": {"image": DIGEST}}}).engine.lean.image == DIGEST


def test_ai_key_must_be_secret_reference():
    with pytest.raises(ConfigError, match="secret://"):
        load_settings(overrides={"ai": {"provider": "anthropic", "api_key_ref": "sk-ant-123"}})
    s = load_settings(
        overrides={"ai": {"provider": "anthropic", "api_key_ref": "secret://quantlab/anthropic"}}
    )
    assert s.ai.api_key_ref == "secret://quantlab/anthropic"


def test_settings_are_immutable():
    s = load_settings()
    with pytest.raises(Exception):  # noqa: B017 - pydantic frozen instance error
        s.server.port = 1


def test_lean_runtime_defaults_and_validation():
    lean = load_settings().engine.lean
    assert lean.docker_command == ("docker",) and lean.network == "none" and lean.timeout_s == 3600
    for bad in ({"network": "host"}, {"docker_command": []}, {"docker_command": [" "]}, {"timeout_s": 0}):
        with pytest.raises(ConfigError):
            load_settings(overrides={"engine": {"lean": bad}})


def test_lean_docker_command_from_env(monkeypatch):
    monkeypatch.setenv("QUANTLAB_ENGINE__LEAN__DOCKER_COMMAND", '["podman"]')
    assert load_settings().engine.lean.docker_command == ("podman",)

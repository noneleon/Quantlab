import pytest
from pydantic import SecretStr

from quantlab.bootstrap import API_TOKEN_REF, AppContext, BootstrapError, initialize
from quantlab.core.audit import AuditEntry
from quantlab.core.db import current_revision, head_revision
from quantlab.core.secret_store import SecretStore


class NoKeyring:
    def get_password(self, *a):
        import keyring.errors

        raise keyring.errors.NoKeyringError("none")

    set_password = delete_password = get_password


def test_open_requires_init(ql_home):
    with pytest.raises(BootstrapError, match="quantlab init"):
        AppContext.open()


def test_initialize_is_idempotent_and_never_overwrites(ql_home, memory_keyring):
    first = initialize()
    assert first.created_config and first.token_created and first.token_storage == "keyring"
    assert first.new_token is None  # stored in keyring, never echoed
    config = ql_home / "quantlab.toml"
    config.write_text(config.read_text() + "\n# user edit\n")
    second = initialize()
    assert not second.created_config and not second.token_created and second.token_storage == "existing"
    assert config.read_text().endswith("# user edit\n")
    ctx = AppContext.open(configure_logging=False)
    try:
        assert current_revision(ctx.db) == head_revision(ctx.db)
        assert ctx.api_token() is not None and len(ctx.api_token().get_secret_value()) >= 40
        with ctx.sessions() as s:
            assert [e.action for e in s.query(AuditEntry)] == ["secret.set"]
    finally:
        ctx.close()


def test_initialize_without_keyring_returns_token_once(ql_home):
    report = initialize(secret_store=SecretStore(backend=NoKeyring(), env={}))
    assert report.token_storage == "unavailable" and isinstance(report.new_token, SecretStr)
    ctx = AppContext.open(configure_logging=False, secret_store=SecretStore(backend=NoKeyring(), env={}))
    try:
        with ctx.sessions() as s:
            assert s.query(AuditEntry).count() == 0  # nothing claimed as stored
        env_store = SecretStore(backend=NoKeyring(), env={"QUANTLAB_SECRET__API__TOKEN": "abc"})
        ctx.secrets = env_store
        assert ctx.api_token().get_secret_value() == "abc"
    finally:
        ctx.close()


def test_rotate_changes_token(ql_home, memory_keyring):
    initialize()
    ctx = AppContext.open(configure_logging=False)
    try:
        old = ctx.api_token().get_secret_value()
        token, storage = ctx.rotate_api_token()
        assert storage == "keyring" and token.get_secret_value() != old
        assert ctx.secrets.get(API_TOKEN_REF).get_secret_value() == token.get_secret_value()
    finally:
        ctx.close()


def test_engine_requires_pinned_image(ql_home):
    initialize()
    ctx = AppContext.open(configure_logging=False)
    try:
        from quantlab.engine import EngineError

        with pytest.raises(EngineError, match="not set"):
            _ = ctx.engine
        assert ctx.read_only_backtests().list() == []  # queries work without an engine
    finally:
        ctx.close()


def test_initialize_with_dropping_keyring_does_not_claim_storage(ql_home):
    """Regression (CHANGE-0008): found by the `quantlab serve` smoke test with keyring's null backend."""
    import keyring.backends.null

    report = initialize(secret_store=SecretStore(backend=keyring.backends.null.Keyring(), env={}))
    assert report.token_storage == "unavailable" and report.new_token is not None

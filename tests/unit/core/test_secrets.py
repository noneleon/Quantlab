import keyring.errors
import pytest

from quantlab.core.audit import AuditEntry
from quantlab.core.secret_store import SecretError, SecretNotFoundError, SecretRef, SecretStore


class MemoryKeyring:
    def __init__(self):
        self.data = {}

    def get_password(self, service, user):
        return self.data.get((service, user))

    def set_password(self, service, user, pw):
        self.data[(service, user)] = pw

    def delete_password(self, service, user):
        if (service, user) not in self.data:
            raise keyring.errors.PasswordDeleteError("missing")
        del self.data[(service, user)]


class BrokenKeyring:
    def get_password(self, *a):
        raise keyring.errors.NoKeyringError("no backend")

    set_password = delete_password = get_password


REF = "secret://quantlab/ibkr/paper/password"


def test_ref_parsing():
    r = SecretRef.parse(REF)
    assert (r.service, r.key) == ("quantlab", "ibkr/paper/password")
    assert r.uri == REF
    assert r.env_var == "QUANTLAB_SECRET__IBKR__PAPER__PASSWORD"
    assert SecretRef.parse("secret://quantlab/ai-key").env_var == "QUANTLAB_SECRET__AI_KEY"


@pytest.mark.parametrize(
    "bad", ["quantlab/x", "secret://quantlab", "secret://Quantlab/x", "secret://q/../x", "secret://q/a b"]
)
def test_invalid_refs(bad):
    with pytest.raises(SecretError):
        SecretRef.parse(bad)


def test_keyring_first_then_env():
    kr = MemoryKeyring()
    env = {"QUANTLAB_SECRET__IBKR__PAPER__PASSWORD": "from-env"}
    store = SecretStore(backend=kr, env=env)
    assert store.get(REF).get_secret_value() == "from-env"
    kr.data[("quantlab", "ibkr/paper/password")] = "from-keyring"
    assert store.get(REF).get_secret_value() == "from-keyring"


def test_value_not_exposed_in_repr():
    store = SecretStore(backend=MemoryKeyring(), env={"QUANTLAB_SECRET__IBKR__PAPER__PASSWORD": "hunter2"})
    v = store.get(REF)
    assert "hunter2" not in repr(v) and "hunter2" not in str(v)


def test_missing_secret_message_has_no_value():
    store = SecretStore(backend=MemoryKeyring(), env={})
    with pytest.raises(SecretNotFoundError, match="QUANTLAB_SECRET__IBKR__PAPER__PASSWORD"):
        store.get(REF)
    assert not store.exists(REF)


def test_broken_keyring_falls_back_to_env():
    store = SecretStore(backend=BrokenKeyring(), env={"QUANTLAB_SECRET__IBKR__PAPER__PASSWORD": "x"})
    assert store.get(REF).get_secret_value() == "x"


def test_set_and_delete_are_audited_without_value(db_session):
    kr = MemoryKeyring()
    store = SecretStore(backend=kr, env={})
    store.set(REF, "hunter2", session=db_session, actor="owner")
    db_session.commit()
    assert store.get(REF).get_secret_value() == "hunter2"
    store.delete(REF, session=db_session, actor="owner")
    db_session.commit()
    entries = db_session.query(AuditEntry).order_by(AuditEntry.seq).all()
    assert [e.action for e in entries] == ["secret.set", "secret.delete"]
    assert all(e.target == REF and e.actor_type == "user" for e in entries)
    assert all("hunter2" not in (e.details + (e.target or "")) for e in entries)
    with pytest.raises(SecretNotFoundError):
        store.delete(REF, session=db_session, actor="owner")


def test_set_fails_cleanly_without_keyring(db_session):
    store = SecretStore(backend=BrokenKeyring(), env={})
    with pytest.raises(SecretError, match="keyring unavailable"):
        store.set(REF, "x", session=db_session, actor="owner")
    db_session.rollback()
    assert db_session.query(AuditEntry).count() == 0


def test_empty_secret_rejected(db_session):
    with pytest.raises(SecretError):
        SecretStore(backend=MemoryKeyring(), env={}).set(REF, "", session=db_session, actor="o")

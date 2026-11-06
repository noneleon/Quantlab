"""Secret store (SEC-001). See docs/SECURITY_POLICY.md.

Secrets are addressed by reference, never stored in config or the database:

    secret://quantlab/ibkr/paper/password
             └service┘└──────key────────┘

Lookup order: OS keyring (Windows Credential Manager / macOS Keychain / Linux Secret Service),
then an environment variable fallback for headless servers:

    secret://quantlab/ibkr/paper/password  ->  QUANTLAB_SECRET__IBKR__PAPER__PASSWORD

Values are returned as ``pydantic.SecretStr`` so they do not leak through repr/logging.
Writes and deletes go to the keyring only, are recorded as user actions in the audit log (without
the value), and must be called inside a transaction the caller commits only on success.
AI agents have no path to this module (DEC-0011).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import keyring
import keyring.errors
from pydantic import SecretStr
from sqlalchemy.orm import Session

from quantlab.core.audit import AuditLog
from quantlab.core.config import SECRET_REF_PREFIX

_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class SecretError(RuntimeError):
    pass


class SecretNotFoundError(SecretError, KeyError):
    pass


class KeyringBackend(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None: ...
    def set_password(self, service_name: str, username: str, password: str) -> None: ...
    def delete_password(self, service_name: str, username: str) -> None: ...


@dataclass(frozen=True)
class SecretRef:
    service: str
    key: str

    @classmethod
    def parse(cls, ref: str) -> SecretRef:
        if not ref.startswith(SECRET_REF_PREFIX):
            raise SecretError(f"not a secret reference (must start with {SECRET_REF_PREFIX}): {ref!r}")
        parts = ref[len(SECRET_REF_PREFIX) :].split("/")
        if len(parts) < 2 or not all(_SEGMENT.match(p) for p in parts):
            raise SecretError(
                f"invalid secret reference {ref!r}: expected secret://<service>/<key>[/...] with "
                "lowercase segments [a-z0-9_-]"
            )
        return cls(service=parts[0], key="/".join(parts[1:]))

    @property
    def uri(self) -> str:
        return f"{SECRET_REF_PREFIX}{self.service}/{self.key}"

    @property
    def env_var(self) -> str:
        return "QUANTLAB_SECRET__" + "__".join(p.upper().replace("-", "_") for p in self.key.split("/"))


class SecretStore:
    def __init__(
        self,
        backend: KeyringBackend | Any | None = None,
        env: Mapping[str, str] | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self._backend: Any = backend if backend is not None else keyring
        self._env = env if env is not None else os.environ
        self._audit = audit or AuditLog()

    def _keyring_get(self, ref: SecretRef) -> str | None:
        try:
            value: str | None = self._backend.get_password(ref.service, ref.key)
        except keyring.errors.KeyringError:
            return None  # no usable keyring backend on this machine -> fall back to env
        return value

    def get(self, ref: str) -> SecretStr:
        parsed = SecretRef.parse(ref)
        value = self._keyring_get(parsed)
        if value is None:
            value = self._env.get(parsed.env_var)
        if value is None or value == "":
            raise SecretNotFoundError(
                f"secret {parsed.uri} not found in keyring or environment variable {parsed.env_var}"
            )
        return SecretStr(value)

    def exists(self, ref: str) -> bool:
        try:
            self.get(ref)
        except SecretNotFoundError:
            return False
        return True

    def set(self, ref: str, value: str | SecretStr, *, session: Session, actor: str) -> None:
        """Store a secret in the OS keyring. Human-only operation; audited without the value."""
        parsed = SecretRef.parse(ref)
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not raw:
            raise SecretError("refusing to store an empty secret")
        # Audit first: if the keyring write then fails, the caller rolls back and nothing is recorded
        # as changed; a keyring change can never happen without an audit entry in the same transaction.
        self._audit.record(session, actor_type="user", actor=actor, action="secret.set", target=parsed.uri)
        try:
            self._backend.set_password(parsed.service, parsed.key, raw)
        except keyring.errors.KeyringError as exc:
            raise SecretError(
                f"keyring unavailable, cannot store {parsed.uri}: {type(exc).__name__}"
            ) from exc

    def delete(self, ref: str, *, session: Session, actor: str) -> None:
        parsed = SecretRef.parse(ref)
        self._audit.record(session, actor_type="user", actor=actor, action="secret.delete", target=parsed.uri)
        try:
            self._backend.delete_password(parsed.service, parsed.key)
        except keyring.errors.PasswordDeleteError as exc:
            raise SecretNotFoundError(f"secret {parsed.uri} not in keyring") from exc
        except keyring.errors.KeyringError as exc:
            raise SecretError(
                f"keyring unavailable, cannot delete {parsed.uri}: {type(exc).__name__}"
            ) from exc

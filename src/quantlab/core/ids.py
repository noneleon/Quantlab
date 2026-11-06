"""ULID identifiers (CORE-006).

26-character Crockford base32 strings: 48-bit millisecond timestamp + 80 random bits.
Lexicographic order == creation order; ids generated within the same millisecond by one
process are strictly increasing (monotonic ULID).
"""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {c: i for i, c in enumerate(_ALPHABET)}
_RANDOM_BITS = 80
_MAX_RANDOM = (1 << _RANDOM_BITS) - 1


def _encode(value: int) -> str:
    chars = []
    for _ in range(26):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


class UlidGenerator:
    """Thread-safe monotonic ULID generator."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_ms = -1
        self._last_rand = 0

    def __call__(self, now_ms: int | None = None) -> str:
        ms = int(time.time() * 1000) if now_ms is None else now_ms
        with self._lock:
            if ms <= self._last_ms:
                # Same (or earlier, e.g. clock stepped back) millisecond: increment to stay ordered.
                ms = self._last_ms
                if self._last_rand == _MAX_RANDOM:
                    ms += 1
                    rand = int.from_bytes(os.urandom(10), "big")
                else:
                    rand = self._last_rand + 1
            else:
                rand = int.from_bytes(os.urandom(10), "big")
            self._last_ms, self._last_rand = ms, rand
        return _encode((ms << _RANDOM_BITS) | rand)


_default_generator = UlidGenerator()


def new_id(now_ms: int | None = None) -> str:
    """Return a new monotonic ULID from the process-wide generator."""
    return _default_generator(now_ms)


def is_valid(value: str) -> bool:
    return len(value) == 26 and value[0] in "01234567" and all(c in _DECODE for c in value)


def timestamp_of(value: str) -> datetime:
    """Creation time encoded in a ULID."""
    if not is_valid(value):
        raise ValueError(f"not a ULID: {value!r}")
    n = 0
    for c in value:
        n = (n << 5) | _DECODE[c]
    return datetime.fromtimestamp((n >> _RANDOM_BITS) / 1000, tz=UTC)

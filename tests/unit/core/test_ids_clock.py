from datetime import UTC, datetime, timedelta, timezone

import pytest

from quantlab.core.clock import ManualClock, SystemClock, ensure_utc
from quantlab.core.ids import UlidGenerator, is_valid, new_id, timestamp_of


def test_ulid_format_and_timestamp():
    ms = 1_700_000_000_123
    uid = UlidGenerator()(now_ms=ms)
    assert len(uid) == 26 and is_valid(uid)
    assert timestamp_of(uid) == datetime.fromtimestamp(ms / 1000, tz=UTC)


def test_ulids_are_unique_and_monotonic():
    ids = [new_id() for _ in range(5000)]
    assert len(set(ids)) == len(ids)
    assert ids == sorted(ids)


def test_random_overflow_rolls_to_next_millisecond(monkeypatch):
    gen = UlidGenerator()
    monkeypatch.setattr("quantlab.core.ids.os.urandom", lambda n: b"\xff" * n)
    a = gen(now_ms=1_000)
    b = gen(now_ms=1_000)
    assert b > a
    assert timestamp_of(b) == datetime.fromtimestamp(1.001, tz=UTC)


def test_monotonic_even_if_clock_steps_back():
    gen = UlidGenerator()
    a = gen(now_ms=2_000_000_000_000)
    b = gen(now_ms=1_000_000_000_000)
    assert b > a


@pytest.mark.parametrize("bad", ["", "x" * 26, "8" + "0" * 25, "0" * 25, "0000000000000000000000000I"])
def test_invalid_ulids(bad):
    assert not is_valid(bad)
    with pytest.raises(ValueError):
        timestamp_of(bad)


def test_system_clock_is_utc_aware():
    assert SystemClock().now().tzinfo is UTC


def test_manual_clock():
    c = ManualClock(datetime(2024, 1, 2, 9, 30, tzinfo=timezone(timedelta(hours=-5))))
    assert c.now() == datetime(2024, 1, 2, 14, 30, tzinfo=UTC)
    c.advance(timedelta(minutes=1))
    assert c.now().minute == 31
    with pytest.raises(ValueError):
        c.advance(timedelta(seconds=-1))
    with pytest.raises(ValueError):
        ManualClock(datetime(2024, 1, 1))  # noqa: DTZ001


def test_ensure_utc_rejects_naive():
    with pytest.raises(ValueError):
        ensure_utc(datetime(2024, 1, 1))  # noqa: DTZ001

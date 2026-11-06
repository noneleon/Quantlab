from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from quantlab.core.audit import GENESIS_HASH, AuditEntry, AuditLog, AuditLogError
from quantlab.core.clock import ManualClock
from quantlab.core.db import current_revision, head_revision, migrate


def test_migration_creates_schema_and_is_idempotent(db_engine, ql_home):
    assert (ql_home / "db" / "quantlab.sqlite").is_file()
    assert current_revision(db_engine) == head_revision(db_engine) == "0001"
    migrate(db_engine)  # second run is a no-op
    with db_engine.connect() as c:
        assert c.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1


def _record(session, log, n=3):
    for i in range(n):
        log.record(
            session, actor_type="user", actor="owner", action="test.action", target=f"t{i}", details={"i": i}
        )
    session.commit()


def test_record_builds_valid_chain(db_session):
    clock = ManualClock(datetime(2026, 1, 1, tzinfo=UTC))
    log = AuditLog(clock)
    _record(db_session, log)
    entries = db_session.query(AuditEntry).order_by(AuditEntry.seq).all()
    assert entries[0].prev_hash == GENESIS_HASH
    assert entries[1].prev_hash == entries[0].hash
    assert entries[0].ts == datetime(2026, 1, 1, tzinfo=UTC)  # tz-aware round trip on SQLite
    check = AuditLog.verify_chain(db_session)
    assert check.ok and check.entries == 3


def test_details_are_redacted(db_session):
    AuditLog().record(
        db_session,
        actor_type="system",
        actor="svc",
        action="broker.connect",
        details={"password": "hunter2", "port": 4002},
    )
    db_session.commit()
    stored = db_session.query(AuditEntry).one().details
    assert "hunter2" not in stored and "4002" in stored


def test_orm_guard_rejects_update_and_delete(db_session):
    _record(db_session, AuditLog(), 1)
    entry = db_session.query(AuditEntry).one()
    entry.action = "tampered"
    with pytest.raises(AuditLogError):
        db_session.flush()
    db_session.rollback()
    db_session.delete(db_session.query(AuditEntry).one())
    with pytest.raises(AuditLogError):
        db_session.flush()


@pytest.mark.parametrize("sql", ["UPDATE audit_log SET action='x'", "DELETE FROM audit_log"])
def test_database_triggers_reject_raw_sql_mutation(db_engine, db_session, sql):
    _record(db_session, AuditLog(), 1)
    with pytest.raises(DatabaseError, match="append-only"), db_engine.begin() as c:
        c.execute(text(sql))


def test_verify_chain_detects_tampering(db_engine, db_session):
    _record(db_session, AuditLog(), 3)
    # Simulate an attacker with raw file access who drops the trigger and edits a row.
    with db_engine.begin() as c:
        c.execute(text("DROP TRIGGER audit_log_no_update"))
        c.execute(text("UPDATE audit_log SET target='evil' WHERE seq=2"))
    db_session.expire_all()
    check = AuditLog.verify_chain(db_session)
    assert not check.ok and check.entries == 2 and check.reason == "content hash mismatch"


def test_record_validates_input(db_session):
    log = AuditLog()
    with pytest.raises(ValueError):
        log.record(db_session, actor_type="robot", actor="x", action="y")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        log.record(db_session, actor_type="user", actor="", action="y")


def test_timestamps_monotonic_with_manual_clock(db_session):
    clock = ManualClock(datetime(2026, 1, 1, tzinfo=UTC))
    log = AuditLog(clock)
    a = log.record(db_session, actor_type="user", actor="o", action="a")
    clock.advance(timedelta(seconds=1))
    b = log.record(db_session, actor_type="user", actor="o", action="b")
    assert b.ts > a.ts and b.seq > a.seq

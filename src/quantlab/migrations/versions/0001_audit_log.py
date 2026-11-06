"""audit_log: append-only, hash-chained (CORE-010)

Revision ID: 0001
Revises:
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

from quantlab.core.db import UTCDateTime

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_APPEND_ONLY_MSG = "audit_log is append-only"


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("ts", UTCDateTime(), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("action", sa.String(200), nullable=False),
        sa.Column("target", sa.String(500), nullable=True),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("seq", name="pk_audit_log"),
        sa.UniqueConstraint("id", name="uq_audit_log_id"),
        sa.UniqueConstraint("hash", name="uq_audit_log_hash"),
        sa.CheckConstraint("actor_type IN ('user','ai','system')", name="ck_audit_log_actor_type"),
    )
    op.create_index("ix_audit_log_action", "audit_log", ["action"])

    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for verb in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER audit_log_no_{verb.lower()} BEFORE {verb} ON audit_log "
                f"BEGIN SELECT RAISE(ABORT, '{_APPEND_ONLY_MSG}'); END;"
            )
    elif dialect == "postgresql":
        op.execute(
            "CREATE FUNCTION audit_log_append_only() RETURNS trigger AS $$ "
            f"BEGIN RAISE EXCEPTION '{_APPEND_ONLY_MSG}'; END; $$ LANGUAGE plpgsql;"
        )
        op.execute(
            "CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log "
            "FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();"
        )
    else:
        raise NotImplementedError(f"append-only trigger not implemented for dialect {dialect!r}")


def downgrade() -> None:
    raise NotImplementedError("audit_log is permanent; downgrading would destroy audit history")

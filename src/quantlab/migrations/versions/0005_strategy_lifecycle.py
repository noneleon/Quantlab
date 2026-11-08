"""strategy_lifecycle_events (STR-004)

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-07

Append-only (enforced by triggers): the lifecycle history of a strategy is never rewritten.
"""

import sqlalchemy as sa
from alembic import op

from quantlab.core.db import UTCDateTime

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_lifecycle_events",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("strategy_id", sa.String(26), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("from_stage", sa.String(20), nullable=False),
        sa.Column("to_stage", sa.String(20), nullable=False),
        sa.Column("tree_hash", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_id"], ["strategies.id"], name="fk_strategy_lifecycle_events_strategy_id_strategies"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_strategy_lifecycle_events"),
        sa.UniqueConstraint("strategy_id", "seq", name="uq_strategy_lifecycle_events_seq"),
    )
    op.create_index("ix_strategy_lifecycle_events_strategy_id", "strategy_lifecycle_events", ["strategy_id"])
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        statements = [
            "CREATE TRIGGER strategy_lifecycle_no_update BEFORE UPDATE ON strategy_lifecycle_events "
            "BEGIN SELECT RAISE(ABORT, 'strategy_lifecycle_events is append-only'); END;",
            "CREATE TRIGGER strategy_lifecycle_no_delete BEFORE DELETE ON strategy_lifecycle_events "
            "BEGIN SELECT RAISE(ABORT, 'strategy_lifecycle_events is append-only'); END;",
        ]
    elif dialect == "postgresql":
        statements = [
            "CREATE TRIGGER strategy_lifecycle_append_only BEFORE UPDATE OR DELETE "
            "ON strategy_lifecycle_events FOR EACH ROW EXECUTE FUNCTION quantlab_reject();",
        ]
    else:
        raise NotImplementedError(f"integrity triggers not implemented for dialect {dialect!r}")
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("lifecycle history is permanent; downgrade would destroy it")

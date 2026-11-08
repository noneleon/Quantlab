"""order_amendments + trailing stops: modify working orders (OMS-006), trailing stop orders (OMS-001)

Revision ID: 0008 Revises: 0007 Create Date: 2026-11-08

Orders stay append-only: a modification is a new ``order_amendments`` row (requested, then applied /
refused / rejected / unknown), and an order's current terms are its original terms overlaid by its last
*applied* amendment. Trailing stops add two nullable columns to ``orders`` (existing rows keep NULL = not a
trailing stop).
"""

import sqlalchemy as sa
from alembic import op

from quantlab.core.db import UTCDateTime

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

TABLE = "order_amendments"


def upgrade() -> None:
    with op.batch_alter_table("orders", recreate="never") as batch:
        batch.add_column(sa.Column("trail_amount", sa.Float(), nullable=True))
        batch.add_column(sa.Column("trail_percent", sa.Boolean(), nullable=True))
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("order_id", sa.String(26), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("amendment", sa.String(26), nullable=False),  # groups the rows of one modification
        sa.Column("status", sa.String(12), nullable=False),  # requested | applied | rejected | unknown
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("limit_price", sa.Float(), nullable=True),
        sa.Column("stop_price", sa.Float(), nullable=True),
        sa.Column("trail_amount", sa.Float(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False),  # JSON: reasons, actor, previous terms
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=f"pk_{TABLE}"),
    )
    op.create_index(f"ix_{TABLE}_order_id", TABLE, ["order_id"])
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            f"CREATE TRIGGER {TABLE}_no_update BEFORE UPDATE ON {TABLE} "
            f"BEGIN SELECT RAISE(ABORT, '{TABLE} is append-only'); END;"
        )
        op.execute(
            f"CREATE TRIGGER {TABLE}_no_delete BEFORE DELETE ON {TABLE} "
            f"BEGIN SELECT RAISE(ABORT, '{TABLE} is append-only'); END;"
        )
    elif dialect == "postgresql":
        op.execute(
            f"CREATE TRIGGER {TABLE}_append_only BEFORE UPDATE OR DELETE ON {TABLE} "
            "FOR EACH ROW EXECUTE FUNCTION quantlab_reject();"
        )
    else:
        raise NotImplementedError(f"integrity triggers not implemented for dialect {dialect!r}")


def downgrade() -> None:
    raise NotImplementedError("trading records are permanent; downgrade would destroy them")

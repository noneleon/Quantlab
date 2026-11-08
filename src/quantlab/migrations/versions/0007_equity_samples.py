"""oms_equity_samples: intraday equity samples recorded by the trading supervisor (LIVE-001, RISK-002)

Revision ID: 0007 Revises: 0006 Create Date: 2026-11-07

Append-only (triggers), like every trading record. The drawdown limit's high-water mark uses these samples
together with the start-of-day equity rows, so intraday peaks are no longer missed (KI-0073).
"""

import sqlalchemy as sa
from alembic import op

from quantlab.core.db import UTCDateTime

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TABLE = "oms_equity_samples"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("account", sa.String(64), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=f"pk_{TABLE}"),
    )
    op.create_index(f"ix_{TABLE}_account", TABLE, ["account"])
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

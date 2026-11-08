"""orders, order_events, fills, oms_daily_equity, risk_decisions, kill_switch_events (OMS-*, RISK-*)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-07

Every table here is append-only (triggers): trading records are never updated or deleted.
"""

import sqlalchemy as sa
from alembic import op

from quantlab.core.db import UTCDateTime

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TABLES = (
    "orders",
    "order_events",
    "fills",
    "oms_daily_equity",
    "oms_accounts",
    "risk_decisions",
    "kill_switch_events",
)


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("client_order_key", sa.String(128), nullable=False),
        sa.Column("account", sa.String(64), nullable=False),
        sa.Column("broker", sa.String(32), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("order_type", sa.String(12), nullable=False),
        sa.Column("limit_price", sa.Float(), nullable=True),
        sa.Column("stop_price", sa.Float(), nullable=True),
        sa.Column("time_in_force", sa.String(4), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_orders"),
        sa.UniqueConstraint("client_order_key", name="uq_orders_client_order_key"),
    )
    op.create_index("ix_orders_symbol", "orders", ["symbol"])
    op.create_table(
        "order_events",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("order_id", sa.String(26), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], name="fk_order_events_order_id_orders"),
        sa.PrimaryKeyConstraint("id", name="pk_order_events"),
        sa.UniqueConstraint("order_id", "seq", name="uq_order_events_seq"),
    )
    op.create_index("ix_order_events_order_id", "order_events", ["order_id"])
    op.create_table(
        "fills",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("order_id", sa.String(26), nullable=False),
        sa.Column("broker", sa.String(32), nullable=False),
        sa.Column("account", sa.String(64), nullable=False),
        sa.Column("exec_id", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("commission", sa.Float(), nullable=False),
        sa.Column("executed_at", UTCDateTime(), nullable=False),
        sa.Column("recorded_at", UTCDateTime(), nullable=False),
        sa.Column("price_state", sa.String(12), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], name="fk_fills_order_id_orders"),
        sa.PrimaryKeyConstraint("id", name="pk_fills"),
        sa.UniqueConstraint("broker", "account", "exec_id", name="uq_fills_broker_account_exec"),
    )
    op.create_table(
        "oms_accounts",
        sa.Column("account", sa.String(64), nullable=False),
        sa.Column("broker", sa.String(32), nullable=False),
        sa.Column("starting_cash", sa.Float(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("account", name="pk_oms_accounts"),
    )
    op.create_index("ix_fills_order_id", "fills", ["order_id"])
    op.create_table(
        "oms_daily_equity",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("account", sa.String(64), nullable=False),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_oms_daily_equity"),
        sa.UniqueConstraint("account", "day", name="uq_oms_daily_equity_day"),
    )
    op.create_table(
        "risk_decisions",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("order_id", sa.String(26), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("checks", sa.Text(), nullable=False),
        sa.Column("limits_sha256", sa.String(64), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_risk_decisions"),
    )
    op.create_index("ix_risk_decisions_order_id", "risk_decisions", ["order_id"])
    op.create_table(
        "kill_switch_events",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("engaged", sa.Boolean(), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_kill_switch_events"),
        sa.UniqueConstraint("seq", name="uq_kill_switch_events_seq"),
    )
    dialect = op.get_bind().dialect.name
    statements = []
    for table in TABLES:
        if dialect == "sqlite":
            statements += [
                f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;",
                f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;",
            ]
        elif dialect == "postgresql":
            statements.append(
                f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION quantlab_reject();"
            )
        else:
            raise NotImplementedError(f"integrity triggers not implemented for dialect {dialect!r}")
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("trading records are permanent; downgrade would destroy them")

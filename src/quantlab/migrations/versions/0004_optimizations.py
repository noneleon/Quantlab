"""optimizations, optimization_trials (OPT-001/003)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-07

optimization_trials are append-only; optimizations are never deleted (both enforced by triggers).
"""

import sqlalchemy as sa
from alembic import op

from quantlab.core.db import UTCDateTime

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "optimizations",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("strategy_name", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("finished_at", UTCDateTime(), nullable=True),
        sa.Column("spec", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_optimizations"),
    )
    op.create_index("ix_optimizations_strategy_name", "optimizations", ["strategy_name"])
    op.create_table(
        "optimization_trials",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("optimization_id", sa.String(26), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("window", sa.Integer(), nullable=True),
        sa.Column("segment", sa.String(8), nullable=False),
        sa.Column("parameters", sa.Text(), nullable=False),
        sa.Column("run_id", sa.String(26), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("objective", sa.Float(), nullable=True),
        sa.Column("metrics", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["optimization_id"],
            ["optimizations.id"],
            name="fk_optimization_trials_optimization_id_optimizations",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["backtest_runs.id"], name="fk_optimization_trials_run_id_backtest_runs"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_optimization_trials"),
    )
    op.create_index("ix_optimization_trials_optimization_id", "optimization_trials", ["optimization_id"])

    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        statements = [
            "CREATE TRIGGER optimization_trials_no_update BEFORE UPDATE ON optimization_trials "
            "BEGIN SELECT RAISE(ABORT, 'optimization_trials is append-only'); END;",
            "CREATE TRIGGER optimization_trials_no_delete BEFORE DELETE ON optimization_trials "
            "BEGIN SELECT RAISE(ABORT, 'optimization_trials is append-only'); END;",
            "CREATE TRIGGER optimizations_no_delete BEFORE DELETE ON optimizations "
            "BEGIN SELECT RAISE(ABORT, 'optimizations are never deleted'); END;",
        ]
    elif dialect == "postgresql":
        statements = [  # quantlab_reject() is created in migration 0002
            "CREATE TRIGGER optimization_trials_append_only BEFORE UPDATE OR DELETE ON optimization_trials "
            "FOR EACH ROW EXECUTE FUNCTION quantlab_reject();",
            "CREATE TRIGGER optimizations_no_delete BEFORE DELETE ON optimizations "
            "FOR EACH ROW EXECUTE FUNCTION quantlab_reject();",
        ]
    else:
        raise NotImplementedError(f"integrity triggers not implemented for dialect {dialect!r}")
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("optimization history is permanent; downgrade would destroy it")

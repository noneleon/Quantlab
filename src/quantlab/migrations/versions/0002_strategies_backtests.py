"""strategies, strategy_versions, backtest_runs (STR-001/002, BT-001..003)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-06

Integrity rules enforced in the database (in addition to ORM guards):
* strategy_versions: append-only (no UPDATE / DELETE).
* strategies: never deleted (archive instead).
* backtest_runs: never deleted; no UPDATE once status is terminal.
"""

import sqlalchemy as sa
from alembic import op

from quantlab.core.db import UTCDateTime

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TERMINAL = "('succeeded','algorithm_error','failed','timed_out','cancelled','engine_error')"


def upgrade() -> None:
    op.create_table(
        "strategies",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("entry_file", sa.String(200), nullable=False),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_strategies"),
        sa.UniqueConstraint("name", name="uq_strategies_name"),
    )
    op.create_table(
        "strategy_versions",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("strategy_id", sa.String(26), nullable=False),
        sa.Column("tree_hash", sa.String(64), nullable=False),
        sa.Column("files", sa.Text(), nullable=False),
        sa.Column("git_commit", sa.String(64), nullable=True),
        sa.Column("git_dirty", sa.Boolean(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_id"], ["strategies.id"], name="fk_strategy_versions_strategy_id_strategies"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_strategy_versions"),
        sa.UniqueConstraint("strategy_id", "tree_hash", name="uq_strategy_versions_tree"),
    )
    op.create_index("ix_strategy_versions_strategy_id", "strategy_versions", ["strategy_id"])
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("strategy_id", sa.String(26), nullable=False),
        sa.Column("strategy_version_id", sa.String(26), nullable=False),
        sa.Column("rerun_of", sa.String(26), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("finished_at", UTCDateTime(), nullable=True),
        sa.Column("parameters", sa.Text(), nullable=False),
        sa.Column("data_provenance", sa.String(20), nullable=False),
        sa.Column("data_version", sa.String(64), nullable=True),
        sa.Column("engine_info", sa.Text(), nullable=False),
        sa.Column("inputs_hash", sa.String(64), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=True),
        sa.Column("engine_status", sa.String(40), nullable=True),
        sa.Column("runtime_error", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("engine_statistics", sa.Text(), nullable=True),
        sa.Column("metrics", sa.Text(), nullable=True),
        sa.Column("cross_checks", sa.Text(), nullable=True),
        sa.Column("manifest", sa.Text(), nullable=False),
        sa.Column("run_dir", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_id"], ["strategies.id"], name="fk_backtest_runs_strategy_id_strategies"
        ),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"],
            ["strategy_versions.id"],
            name="fk_backtest_runs_strategy_version_id_strategy_versions",
        ),
        sa.ForeignKeyConstraint(
            ["rerun_of"], ["backtest_runs.id"], name="fk_backtest_runs_rerun_of_backtest_runs"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_backtest_runs"),
    )
    op.create_index("ix_backtest_runs_strategy_id", "backtest_runs", ["strategy_id"])
    op.create_index("ix_backtest_runs_status", "backtest_runs", ["status"])
    op.create_index("ix_backtest_runs_inputs_hash", "backtest_runs", ["inputs_hash"])

    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        statements = [
            "CREATE TRIGGER strategy_versions_no_update BEFORE UPDATE ON strategy_versions "
            "BEGIN SELECT RAISE(ABORT, 'strategy_versions is append-only'); END;",
            "CREATE TRIGGER strategy_versions_no_delete BEFORE DELETE ON strategy_versions "
            "BEGIN SELECT RAISE(ABORT, 'strategy_versions is append-only'); END;",
            "CREATE TRIGGER strategies_no_delete BEFORE DELETE ON strategies "
            "BEGIN SELECT RAISE(ABORT, 'strategies are archived, never deleted'); END;",
            "CREATE TRIGGER backtest_runs_no_delete BEFORE DELETE ON backtest_runs "
            "BEGIN SELECT RAISE(ABORT, 'backtest runs are never deleted'); END;",
            f"CREATE TRIGGER backtest_runs_terminal_immutable BEFORE UPDATE ON backtest_runs "
            f"WHEN OLD.status IN {TERMINAL} "
            "BEGIN SELECT RAISE(ABORT, 'terminal backtest runs are immutable'); END;",
        ]
    elif dialect == "postgresql":
        statements = [
            "CREATE FUNCTION quantlab_reject() RETURNS trigger AS $$ "
            "BEGIN RAISE EXCEPTION '% on % is not allowed', TG_OP, TG_TABLE_NAME; END; $$ LANGUAGE plpgsql;",
            "CREATE TRIGGER strategy_versions_append_only BEFORE UPDATE OR DELETE ON strategy_versions "
            "FOR EACH ROW EXECUTE FUNCTION quantlab_reject();",
            "CREATE TRIGGER strategies_no_delete BEFORE DELETE ON strategies "
            "FOR EACH ROW EXECUTE FUNCTION quantlab_reject();",
            "CREATE TRIGGER backtest_runs_no_delete BEFORE DELETE ON backtest_runs "
            "FOR EACH ROW EXECUTE FUNCTION quantlab_reject();",
            f"CREATE TRIGGER backtest_runs_terminal_immutable BEFORE UPDATE ON backtest_runs "
            f"FOR EACH ROW WHEN (OLD.status IN {TERMINAL}) EXECUTE FUNCTION quantlab_reject();",
        ]
    else:
        raise NotImplementedError(f"integrity triggers not implemented for dialect {dialect!r}")
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("strategy and backtest history is permanent; downgrade would destroy it")

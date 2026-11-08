"""datasets, dataset_versions (DATA-003/004/006)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-06

dataset_versions are append-only; datasets are never deleted (both enforced by triggers).
"""

import sqlalchemy as sa
from alembic import op

from quantlab.core.db import UTCDateTime

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("asset_class", sa.String(16), nullable=False),
        sa.Column("market", sa.String(32), nullable=False),
        sa.Column("resolution", sa.String(16), nullable=False),
        sa.Column("calendar", sa.String(32), nullable=False),
        sa.Column("provenance", sa.String(20), nullable=False),
        sa.Column("price_adjustment", sa.String(32), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("license_terms", sa.Text(), nullable=False),
        sa.Column("redistribution_allowed", sa.Boolean(), nullable=False),
        sa.Column("license_url", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_datasets"),
        sa.UniqueConstraint("name", name="uq_datasets_name"),
    )
    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("dataset_id", sa.String(26), nullable=False),
        sa.Column("parent_id", sa.String(26), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("ingest_id", sa.String(26), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("conflict_policy", sa.String(16), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("start_ts", UTCDateTime(), nullable=False),
        sa.Column("end_ts", UTCDateTime(), nullable=False),
        sa.Column("symbols", sa.Text(), nullable=False),
        sa.Column("files", sa.Text(), nullable=False),
        sa.Column("validation", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_id"], ["datasets.id"], name="fk_dataset_versions_dataset_id_datasets"
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["dataset_versions.id"], name="fk_dataset_versions_parent_id_dataset_versions"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dataset_versions"),
        sa.UniqueConstraint("dataset_id", "content_hash", name="uq_dataset_versions_content"),
    )
    op.create_index("ix_dataset_versions_dataset_id", "dataset_versions", ["dataset_id"])

    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        statements = [
            "CREATE TRIGGER dataset_versions_no_update BEFORE UPDATE ON dataset_versions "
            "BEGIN SELECT RAISE(ABORT, 'dataset_versions is append-only'); END;",
            "CREATE TRIGGER dataset_versions_no_delete BEFORE DELETE ON dataset_versions "
            "BEGIN SELECT RAISE(ABORT, 'dataset_versions is append-only'); END;",
            "CREATE TRIGGER datasets_no_delete BEFORE DELETE ON datasets "
            "BEGIN SELECT RAISE(ABORT, 'datasets are never deleted'); END;",
        ]
    elif dialect == "postgresql":
        statements = [  # quantlab_reject() is created in migration 0002
            "CREATE TRIGGER dataset_versions_append_only BEFORE UPDATE OR DELETE ON dataset_versions "
            "FOR EACH ROW EXECUTE FUNCTION quantlab_reject();",
            "CREATE TRIGGER datasets_no_delete BEFORE DELETE ON datasets "
            "FOR EACH ROW EXECUTE FUNCTION quantlab_reject();",
        ]
    else:
        raise NotImplementedError(f"integrity triggers not implemented for dialect {dialect!r}")
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("dataset history is permanent; downgrade would destroy it")

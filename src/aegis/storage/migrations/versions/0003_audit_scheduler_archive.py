"""audit fixes: scheduler lateness/misses, research archive manifest

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-26
"""
import sqlalchemy as sa
from alembic import op

import aegis.storage.schema

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scheduler_queue", schema=None) as b:
        b.add_column(sa.Column("last_observed_at", aegis.storage.schema.UtcDateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("last_lateness_seconds", sa.Integer(), nullable=True))
        b.add_column(sa.Column("consecutive_misses", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "archive_batches",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("table_name", sa.String(length=64), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("min_id", sa.BigInteger(), nullable=False),
        sa.Column("max_id", sa.BigInteger(), nullable=False),
        sa.Column("min_ts", aegis.storage.schema.UtcDateTime(timezone=True), nullable=True),
        sa.Column("max_ts", aegis.storage.schema.UtcDateTime(timezone=True), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", aegis.storage.schema.UtcDateTime(timezone=True), nullable=False),
        sa.Column("verified_at", aegis.storage.schema.UtcDateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_archive_batches")),
        sa.UniqueConstraint("table_name", "min_id", "max_id", name=op.f("uq_archive_batches_table_name_min_id_max_id")),
    )
    with op.batch_alter_table("archive_batches", schema=None) as b:
        b.create_index("ix_archive_table_maxid", ["table_name", "max_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("archive_batches", schema=None) as b:
        b.drop_index("ix_archive_table_maxid")
    op.drop_table("archive_batches")
    with op.batch_alter_table("scheduler_queue", schema=None) as b:
        b.drop_column("consecutive_misses")
        b.drop_column("last_lateness_seconds")
        b.drop_column("last_observed_at")

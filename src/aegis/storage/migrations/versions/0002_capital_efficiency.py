"""capital efficiency: capital availability, recovery time, prediction intervals

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-26
"""
import sqlalchemy as sa
from alembic import op

import aegis.storage.schema

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("executions", schema=None) as b:
        b.add_column(sa.Column("capital_available_at", aegis.storage.schema.UtcDateTime(timezone=True), nullable=True))
    with op.batch_alter_table("positions", schema=None) as b:
        b.add_column(sa.Column("capital_recovered_at", aegis.storage.schema.UtcDateTime(timezone=True), nullable=True))
    with op.batch_alter_table("predictions", schema=None) as b:
        b.add_column(sa.Column("unit", sa.String(length=16), nullable=True))
        b.add_column(sa.Column("lower", aegis.storage.schema.Dec(), nullable=True))
        b.add_column(sa.Column("upper", aegis.storage.schema.Dec(), nullable=True))
        b.add_column(sa.Column("interval_level", aegis.storage.schema.Dec(), nullable=True))
        b.add_column(sa.Column("status", sa.String(length=24), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("predictions", schema=None) as b:
        for c in ("status", "interval_level", "upper", "lower", "unit"):
            b.drop_column(c)
    with op.batch_alter_table("positions", schema=None) as b:
        b.drop_column("capital_recovered_at")
    with op.batch_alter_table("executions", schema=None) as b:
        b.drop_column("capital_available_at")

"""audit fixes: per-line invoicing progress, live timestamp defaults

Revision ID: a7c3d9e1f2b4
Revises: f2a78aa74f70
Create Date: 2026-10-01 10:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a7c3d9e1f2b4'
down_revision: str | None = 'f2a78aa74f70'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Columns whose server default was the STRING 'now()': Postgres evaluated it
# once at CREATE TABLE time, so every row got the migration's timestamp.
FROZEN_TIMESTAMPS = (
    ('core', 'audit_logs', 'created_at'),
    ('core', 'notifications', 'created_at'),
    ('inventory', 'stock_moves', 'moved_at'),
)

# UUIDv7 ids carry their creation time (ms since epoch) in the first 48 bits.
UUID7_TIME = (
    "to_timestamp(('x' || substr(replace(id::text, '-', ''), 1, 12))::bit(48)::bigint"
    " / 1000.0)"
)


def upgrade() -> None:
    # Invoice lines copied from an order remember their order line, so
    # invoicing progress is tracked per line (duplicate products and
    # free-text lines are billed exactly once).
    op.add_column(
        'invoice_lines',
        sa.Column('source_line_id', sa.Uuid(), nullable=True),
        schema='invoicing',
    )
    for schema, table, column in FROZEN_TIMESTAMPS:
        op.execute(f'ALTER TABLE {schema}.{table} ALTER COLUMN {column} SET DEFAULT now()')
        # Recover each row's real creation time from its UUIDv7 id.
        op.execute(f'UPDATE {schema}.{table} SET {column} = {UUID7_TIME}')


def downgrade() -> None:
    for schema, table, column in FROZEN_TIMESTAMPS:
        op.execute(f'ALTER TABLE {schema}.{table} ALTER COLUMN {column} SET DEFAULT now()')
    op.drop_column('invoice_lines', 'source_line_id', schema='invoicing')

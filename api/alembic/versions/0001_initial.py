"""initial — pgvector extension + module schemas

Revision ID: 0001
Revises:
Create Date: 2026-09-26
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MODULE_SCHEMAS = (
    "core",
    "hr",
    "crm",
    "catalog",
    "sales",
    "purchasing",
    "inventory",
    "invoicing",
    "accounting",
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    for schema in MODULE_SCHEMAS:
        op.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')


def downgrade() -> None:
    for schema in MODULE_SCHEMAS:
        op.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

"""drop legacy public.transactions (old langgraph table; user chose to start fresh)

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.transactions")


def downgrade() -> None:
    # Data is intentionally not recoverable; the legacy table is not recreated.
    pass

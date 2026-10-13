"""transactions.note: the user's own note on any row (description stays what the source said)

Revision ID: 0019
Revises: 0018
"""
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ledger.transactions ADD COLUMN note text")


def downgrade() -> None:
    op.execute("ALTER TABLE ledger.transactions DROP COLUMN note")

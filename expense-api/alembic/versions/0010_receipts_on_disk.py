"""receipt images live on disk (RECEIPTS_DIR); the DB keeps only their path

Revision ID: 0010
Revises: 0009
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No receipt had been stored when this was written, so there is nothing to move out of the DB.
    op.execute("DELETE FROM ledger.receipts WHERE storage_uri IS NULL")
    op.execute("ALTER TABLE ledger.receipts DROP CONSTRAINT IF EXISTS receipts_check")
    op.execute("ALTER TABLE ledger.receipts DROP COLUMN content")
    op.execute("ALTER TABLE ledger.receipts ALTER COLUMN storage_uri SET NOT NULL")
    op.execute("COMMENT ON COLUMN ledger.receipts.storage_uri IS "
               "'path relative to RECEIPTS_DIR, e.g. 2026/10/<sha256>.jpg'")


def downgrade() -> None:
    op.execute("ALTER TABLE ledger.receipts ALTER COLUMN storage_uri DROP NOT NULL")
    op.execute("ALTER TABLE ledger.receipts ADD COLUMN content bytea")
    op.execute("ALTER TABLE ledger.receipts ADD CONSTRAINT receipts_check "
               "CHECK (content IS NOT NULL OR storage_uri IS NOT NULL)")

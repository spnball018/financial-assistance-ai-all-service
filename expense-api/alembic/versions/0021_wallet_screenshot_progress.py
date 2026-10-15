"""wallet_screenshots: reading runs in the background; ocr_progress says where it is (queued, reading attempt
n of N, waiting to retry, saving) and ocr_progress_at when it last moved, so a reading that stopped moving
(e.g. the server restarted mid-read) shows as stalled instead of pending forever

Revision ID: 0021
Revises: 0020
"""
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ledger.wallet_screenshots ADD COLUMN ocr_progress jsonb, "
               "ADD COLUMN ocr_progress_at timestamptz")


def downgrade() -> None:
    op.execute("ALTER TABLE ledger.wallet_screenshots DROP COLUMN ocr_progress, DROP COLUMN ocr_progress_at")

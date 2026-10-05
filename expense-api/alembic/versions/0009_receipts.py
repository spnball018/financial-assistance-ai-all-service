"""receipts (T6.5): photo of a receipt attached to a payment, OCR proposal, items as children

Revision ID: 0009
Revises: 0008
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The image lives in Postgres (one backup for data + images). storage_uri is reserved for moving
    # images to object storage later without changing the shape.
    op.execute("""
    CREATE TABLE ledger.receipts (
        id              bigserial PRIMARY KEY,
        transaction_id  bigint REFERENCES ledger.transactions(id) ON DELETE SET NULL,
        content         bytea,
        storage_uri     text,
        mime_type       text NOT NULL,
        sha256          text NOT NULL UNIQUE,
        uploaded_at     timestamptz NOT NULL DEFAULT now(),
        ocr_status      text NOT NULL DEFAULT 'pending' CHECK (ocr_status IN ('pending','done','failed')),
        ocr_result      jsonb,
        ocr_model       text,
        ocr_error       text,
        applied_at      timestamptz,
        CHECK (content IS NOT NULL OR storage_uri IS NOT NULL)
    )""")
    op.execute("CREATE INDEX ix_receipts_transaction ON ledger.receipts(transaction_id)")
    op.execute("ALTER TABLE ledger.transactions "
               "ADD COLUMN receipt_id bigint REFERENCES ledger.receipts(id) ON DELETE SET NULL")
    # v_reportable_expenses selects t.*, so recreate it to include the new column
    op.execute("DROP VIEW ledger.v_reportable_expenses")
    op.execute("""
    CREATE VIEW ledger.v_reportable_expenses AS
    SELECT t.*
    FROM ledger.transactions t
    WHERE t.status = 'confirmed'
      AND t.kind = 'expense'
      AND NOT EXISTS (SELECT 1 FROM ledger.transactions c WHERE c.parent_id = t.id)
    """)


def downgrade() -> None:
    op.execute("DROP VIEW ledger.v_reportable_expenses")
    op.execute("DELETE FROM ledger.transactions WHERE receipt_id IS NOT NULL")
    op.execute("ALTER TABLE ledger.transactions DROP COLUMN receipt_id")
    op.execute("""
    CREATE VIEW ledger.v_reportable_expenses AS
    SELECT t.*
    FROM ledger.transactions t
    WHERE t.status = 'confirmed'
      AND t.kind = 'expense'
      AND NOT EXISTS (SELECT 1 FROM ledger.transactions c WHERE c.parent_id = t.id)
    """)
    op.execute("DROP TABLE ledger.receipts")

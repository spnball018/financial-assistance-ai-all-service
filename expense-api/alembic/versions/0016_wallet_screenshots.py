"""wallet_screenshots: screenshots of an e-wallet app's history (TrueMoney, ...), read by a vision model
into rows on that wallet account (source 'wallet_screenshot'), like a card statement

Revision ID: 0016
Revises: 0015
"""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

_SOURCES = "'kbank_email','ktc_pdf','shopee_email','lazada_email','manual'"


def upgrade() -> None:
    op.execute("""
    CREATE TABLE ledger.wallet_screenshots (
        id           bigserial PRIMARY KEY,
        user_id      integer NOT NULL REFERENCES ledger.users(id),
        account_id   integer NOT NULL,
        storage_uri  text NOT NULL,
        mime_type    text NOT NULL,
        sha256       text NOT NULL,
        uploaded_at  timestamptz NOT NULL DEFAULT now(),
        ocr_status   text NOT NULL DEFAULT 'pending' CHECK (ocr_status IN ('pending','done','failed')),
        ocr_result   jsonb,
        ocr_model    text,
        ocr_error    text,
        applied_at   timestamptz,
        UNIQUE (user_id, sha256),
        FOREIGN KEY (account_id, user_id) REFERENCES ledger.accounts (id, user_id)
    )""")
    op.execute("ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_source_check")
    op.execute(f"ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_source_check "
               f"CHECK (source IN ({_SOURCES},'wallet_screenshot'))")


def downgrade() -> None:
    op.execute("DELETE FROM ledger.transactions WHERE source = 'wallet_screenshot'")
    op.execute("ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_source_check")
    op.execute(f"ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_source_check CHECK (source IN ({_SOURCES}))")
    op.execute("DROP TABLE ledger.wallet_screenshots")

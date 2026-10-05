"""raw email storage and per-source Gmail label config

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Which Gmail label each source is read from. Editable from Settings, no redeploy.
    op.execute("""
    CREATE TABLE ledger.email_sources (
        id             serial PRIMARY KEY,
        source         text NOT NULL UNIQUE CHECK (source IN
                         ('kbank_email','ktc_pdf','shopee_email','lazada_email')),
        gmail_label    text,
        sender_filter  text,
        account_id     integer REFERENCES ledger.accounts(id),
        is_active      boolean NOT NULL DEFAULT false,
        updated_at     timestamptz NOT NULL DEFAULT now(),
        CHECK (NOT is_active OR gmail_label IS NOT NULL)
    )""")

    # Original emails, kept so parsers can be re-run without calling Gmail again.
    op.execute("""
    CREATE TABLE ledger.raw_emails (
        id                bigserial PRIMARY KEY,
        gmail_message_id  text NOT NULL UNIQUE,
        source            text NOT NULL,
        gmail_label       text,
        subject           text,
        sender            text,
        received_at       timestamptz,
        text_body         text,
        html_body         text,
        attachments       jsonb NOT NULL DEFAULT '[]'::jsonb,
        parse_status      text NOT NULL DEFAULT 'pending'
                            CHECK (parse_status IN ('pending','parsed','skipped','failed')),
        parser_version    text,
        parse_error       text,
        parsed_at         timestamptz,
        fetched_at        timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("CREATE INDEX ix_raw_emails_source_status ON ledger.raw_emails(source, parse_status)")

    # Attachments kept exactly as received (KTC PDFs stay password-protected).
    op.execute("""
    CREATE TABLE ledger.raw_attachments (
        id             bigserial PRIMARY KEY,
        raw_email_id   bigint NOT NULL REFERENCES ledger.raw_emails(id) ON DELETE CASCADE,
        attachment_id  text NOT NULL,
        filename       text,
        mime_type      text,
        content        bytea NOT NULL,
        UNIQUE (raw_email_id, attachment_id)
    )""")

    op.execute("ALTER TABLE ledger.transactions "
               "ADD COLUMN raw_email_id bigint REFERENCES ledger.raw_emails(id) ON DELETE SET NULL")
    op.execute("ALTER TABLE ledger.ecommerce_orders "
               "ADD COLUMN raw_email_id bigint REFERENCES ledger.raw_emails(id) ON DELETE SET NULL")
    op.execute("CREATE INDEX ix_transactions_raw_email ON ledger.transactions(raw_email_id)")

    # v_reportable_expenses uses t.*, so recreate it to keep the view definition current.
    op.execute("DROP VIEW ledger.v_reportable_expenses")
    op.execute("""
    CREATE VIEW ledger.v_reportable_expenses AS
    SELECT t.*
    FROM ledger.transactions t
    WHERE t.status = 'confirmed'
      AND t.kind = 'expense'
      AND NOT EXISTS (SELECT 1 FROM ledger.transactions c WHERE c.parent_id = t.id)
    """)

    # KTC / Shopee / Lazada labels are not known yet: seeded inactive, set them in Settings.
    op.execute("""
    INSERT INTO ledger.email_sources (source, gmail_label, sender_filter, account_id, is_active)
    SELECT 'kbank_email', 'financial-kasikorn', 'KPLUS@kasikornbank.com', a.id, true
    FROM ledger.accounts a WHERE a.name = 'KBank ออมทรัพย์'
    """)
    op.execute("""
    INSERT INTO ledger.email_sources (source, account_id, is_active)
    SELECT 'ktc_pdf', a.id, false FROM ledger.accounts a WHERE a.name = 'บัตรเครดิต KTC'
    """)
    op.execute("INSERT INTO ledger.email_sources (source, is_active) VALUES "
               "('shopee_email', false), ('lazada_email', false)")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS ledger.v_reportable_expenses")
    op.execute("ALTER TABLE ledger.ecommerce_orders DROP COLUMN raw_email_id")
    op.execute("ALTER TABLE ledger.transactions DROP COLUMN raw_email_id")
    op.execute("""
    CREATE VIEW ledger.v_reportable_expenses AS
    SELECT t.*
    FROM ledger.transactions t
    WHERE t.status = 'confirmed'
      AND t.kind = 'expense'
      AND NOT EXISTS (SELECT 1 FROM ledger.transactions c WHERE c.parent_id = t.id)
    """)
    for t in ("raw_attachments", "raw_emails", "email_sources"):
        op.execute(f"DROP TABLE IF EXISTS ledger.{t}")

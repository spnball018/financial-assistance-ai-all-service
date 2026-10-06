"""per-user data (P7.2): every data table gets user_id; existing rows belong to user 1

Unique keys become per user (account name, dedup_key, merchant rule, email source, settings key, ...).
Foreign keys between data rows include user_id, so the database itself refuses a row that points at
another user's account, transaction, email or receipt, even if a query forgets to check.
categories stay shared (read-only reference data).

Revision ID: 0012
Revises: 0011
"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

TABLES = ["accounts", "transactions", "merchant_memory", "email_sources", "raw_emails", "receipts",
          "sync_runs", "settings", "ecommerce_orders"]

VIEWS_UP = [
    "DROP VIEW ledger.v_reportable_expenses",
    """CREATE VIEW ledger.v_reportable_expenses AS
       SELECT t.* FROM ledger.transactions t
       WHERE t.status = 'confirmed' AND t.kind = 'expense'
         AND NOT EXISTS (SELECT 1 FROM ledger.transactions c WHERE c.parent_id = t.id)""",
    "DROP VIEW ledger.v_account_postings",
    """CREATE VIEW ledger.v_account_postings AS
       SELECT t.id AS transaction_id, t.user_id, t.account_id, t.status, t.occurred_at, t.kind,
              CASE WHEN t.kind IN ('expense', 'transfer') THEN -t.amount ELSE t.amount END AS amount
       FROM ledger.transactions t WHERE t.parent_id IS NULL
       UNION ALL
       SELECT t.id, t.user_id, t.counter_account_id, t.status, t.occurred_at, t.kind, t.amount
       FROM ledger.transactions t WHERE t.kind = 'transfer'""",
]


def upgrade() -> None:
    for t in TABLES:
        op.execute(f"ALTER TABLE ledger.{t} ADD COLUMN user_id integer NOT NULL DEFAULT 1 "
                   f"REFERENCES ledger.users(id)")
        # no default from now on: code that forgets user_id fails instead of writing into user 1
        op.execute(f"ALTER TABLE ledger.{t} ALTER COLUMN user_id DROP DEFAULT")

    # targets of composite foreign keys
    for t in ("accounts", "transactions", "raw_emails", "receipts"):
        op.execute(f"ALTER TABLE ledger.{t} ADD CONSTRAINT {t}_id_user_key UNIQUE (id, user_id)")

    op.execute("""
        ALTER TABLE ledger.accounts DROP CONSTRAINT accounts_name_key;
        ALTER TABLE ledger.accounts ADD CONSTRAINT accounts_user_name_key UNIQUE (user_id, name);

        ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_dedup_key_key;
        ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_user_dedup_key UNIQUE (user_id, dedup_key);
        ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_account_id_fkey;
        ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_account_fkey
            FOREIGN KEY (account_id, user_id) REFERENCES ledger.accounts (id, user_id);
        ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_counter_account_id_fkey;
        ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_counter_account_fkey
            FOREIGN KEY (counter_account_id, user_id) REFERENCES ledger.accounts (id, user_id);
        ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_parent_id_fkey;
        ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_parent_fkey
            FOREIGN KEY (parent_id, user_id) REFERENCES ledger.transactions (id, user_id) ON DELETE CASCADE;
        ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_ref_transaction_id_fkey;
        ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_ref_fkey
            FOREIGN KEY (ref_transaction_id, user_id) REFERENCES ledger.transactions (id, user_id);
        ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_raw_email_id_fkey;
        ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_raw_email_fkey
            FOREIGN KEY (raw_email_id, user_id) REFERENCES ledger.raw_emails (id, user_id) ON DELETE SET NULL (raw_email_id);
        ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_receipt_id_fkey;
        ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_receipt_fkey
            FOREIGN KEY (receipt_id, user_id) REFERENCES ledger.receipts (id, user_id) ON DELETE SET NULL (receipt_id);

        ALTER TABLE ledger.merchant_memory DROP CONSTRAINT merchant_memory_merchant_key_key;
        ALTER TABLE ledger.merchant_memory ADD CONSTRAINT merchant_memory_user_key UNIQUE (user_id, merchant_key);
        ALTER TABLE ledger.merchant_memory DROP CONSTRAINT merchant_memory_counter_account_id_fkey;
        ALTER TABLE ledger.merchant_memory ADD CONSTRAINT merchant_memory_counter_account_fkey
            FOREIGN KEY (counter_account_id, user_id) REFERENCES ledger.accounts (id, user_id);

        ALTER TABLE ledger.email_sources DROP CONSTRAINT email_sources_source_key;
        ALTER TABLE ledger.email_sources ADD CONSTRAINT email_sources_user_source_key UNIQUE (user_id, source);
        ALTER TABLE ledger.email_sources DROP CONSTRAINT email_sources_account_id_fkey;
        ALTER TABLE ledger.email_sources ADD CONSTRAINT email_sources_account_fkey
            FOREIGN KEY (account_id, user_id) REFERENCES ledger.accounts (id, user_id);

        ALTER TABLE ledger.raw_emails DROP CONSTRAINT raw_emails_gmail_message_id_key;
        ALTER TABLE ledger.raw_emails ADD CONSTRAINT raw_emails_user_message_key UNIQUE (user_id, gmail_message_id);

        ALTER TABLE ledger.receipts DROP CONSTRAINT receipts_sha256_key;
        ALTER TABLE ledger.receipts ADD CONSTRAINT receipts_user_sha256_key UNIQUE (user_id, sha256);
        ALTER TABLE ledger.receipts DROP CONSTRAINT receipts_transaction_id_fkey;
        ALTER TABLE ledger.receipts ADD CONSTRAINT receipts_transaction_fkey
            FOREIGN KEY (transaction_id, user_id) REFERENCES ledger.transactions (id, user_id) ON DELETE SET NULL (transaction_id);

        ALTER TABLE ledger.settings DROP CONSTRAINT settings_pkey;
        ALTER TABLE ledger.settings ADD PRIMARY KEY (user_id, key);

        ALTER TABLE ledger.ecommerce_orders DROP CONSTRAINT ecommerce_orders_platform_order_id_key;
        ALTER TABLE ledger.ecommerce_orders ADD CONSTRAINT ecommerce_orders_user_order_key UNIQUE (user_id, platform, order_id);
        ALTER TABLE ledger.ecommerce_orders DROP CONSTRAINT ecommerce_orders_matched_txn_id_fkey;
        ALTER TABLE ledger.ecommerce_orders ADD CONSTRAINT ecommerce_orders_matched_txn_fkey
            FOREIGN KEY (matched_txn_id, user_id) REFERENCES ledger.transactions (id, user_id) ON DELETE SET NULL (matched_txn_id);
        ALTER TABLE ledger.ecommerce_orders DROP CONSTRAINT ecommerce_orders_raw_email_id_fkey;
        ALTER TABLE ledger.ecommerce_orders ADD CONSTRAINT ecommerce_orders_raw_email_fkey
            FOREIGN KEY (raw_email_id, user_id) REFERENCES ledger.raw_emails (id, user_id) ON DELETE SET NULL (raw_email_id);

        CREATE INDEX ix_transactions_user_status ON ledger.transactions (user_id, status);
        CREATE INDEX ix_sync_runs_user ON ledger.sync_runs (user_id, id);
    """)
    for sql in VIEWS_UP:
        op.execute(sql)


def downgrade() -> None:
    # only possible while user 1 is the only owner of data
    for t in TABLES:
        op.execute(f"DELETE FROM ledger.{t} WHERE user_id <> 1")
    op.execute("""
        DROP INDEX ledger.ix_sync_runs_user;
        DROP INDEX ledger.ix_transactions_user_status;
        DROP VIEW ledger.v_reportable_expenses;
        DROP VIEW ledger.v_account_postings;

        ALTER TABLE ledger.ecommerce_orders DROP CONSTRAINT ecommerce_orders_raw_email_fkey;
        ALTER TABLE ledger.ecommerce_orders ADD CONSTRAINT ecommerce_orders_raw_email_id_fkey
            FOREIGN KEY (raw_email_id) REFERENCES ledger.raw_emails (id) ON DELETE SET NULL;
        ALTER TABLE ledger.ecommerce_orders DROP CONSTRAINT ecommerce_orders_matched_txn_fkey;
        ALTER TABLE ledger.ecommerce_orders ADD CONSTRAINT ecommerce_orders_matched_txn_id_fkey
            FOREIGN KEY (matched_txn_id) REFERENCES ledger.transactions (id) ON DELETE SET NULL;
        ALTER TABLE ledger.ecommerce_orders DROP CONSTRAINT ecommerce_orders_user_order_key;
        ALTER TABLE ledger.ecommerce_orders ADD CONSTRAINT ecommerce_orders_platform_order_id_key UNIQUE (platform, order_id);

        ALTER TABLE ledger.settings DROP CONSTRAINT settings_pkey;
        ALTER TABLE ledger.settings ADD PRIMARY KEY (key);

        ALTER TABLE ledger.receipts DROP CONSTRAINT receipts_transaction_fkey;
        ALTER TABLE ledger.receipts ADD CONSTRAINT receipts_transaction_id_fkey
            FOREIGN KEY (transaction_id) REFERENCES ledger.transactions (id) ON DELETE SET NULL;
        ALTER TABLE ledger.receipts DROP CONSTRAINT receipts_user_sha256_key;
        ALTER TABLE ledger.receipts ADD CONSTRAINT receipts_sha256_key UNIQUE (sha256);

        ALTER TABLE ledger.raw_emails DROP CONSTRAINT raw_emails_user_message_key;
        ALTER TABLE ledger.raw_emails ADD CONSTRAINT raw_emails_gmail_message_id_key UNIQUE (gmail_message_id);

        ALTER TABLE ledger.email_sources DROP CONSTRAINT email_sources_account_fkey;
        ALTER TABLE ledger.email_sources ADD CONSTRAINT email_sources_account_id_fkey
            FOREIGN KEY (account_id) REFERENCES ledger.accounts (id);
        ALTER TABLE ledger.email_sources DROP CONSTRAINT email_sources_user_source_key;
        ALTER TABLE ledger.email_sources ADD CONSTRAINT email_sources_source_key UNIQUE (source);

        ALTER TABLE ledger.merchant_memory DROP CONSTRAINT merchant_memory_counter_account_fkey;
        ALTER TABLE ledger.merchant_memory ADD CONSTRAINT merchant_memory_counter_account_id_fkey
            FOREIGN KEY (counter_account_id) REFERENCES ledger.accounts (id);
        ALTER TABLE ledger.merchant_memory DROP CONSTRAINT merchant_memory_user_key;
        ALTER TABLE ledger.merchant_memory ADD CONSTRAINT merchant_memory_merchant_key_key UNIQUE (merchant_key);

        ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_receipt_fkey;
        ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_receipt_id_fkey
            FOREIGN KEY (receipt_id) REFERENCES ledger.receipts (id) ON DELETE SET NULL;
        ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_raw_email_fkey;
        ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_raw_email_id_fkey
            FOREIGN KEY (raw_email_id) REFERENCES ledger.raw_emails (id) ON DELETE SET NULL;
        ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_ref_fkey;
        ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_ref_transaction_id_fkey
            FOREIGN KEY (ref_transaction_id) REFERENCES ledger.transactions (id);
        ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_parent_fkey;
        ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_parent_id_fkey
            FOREIGN KEY (parent_id) REFERENCES ledger.transactions (id) ON DELETE CASCADE;
        ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_counter_account_fkey;
        ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_counter_account_id_fkey
            FOREIGN KEY (counter_account_id) REFERENCES ledger.accounts (id);
        ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_account_fkey;
        ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_account_id_fkey
            FOREIGN KEY (account_id) REFERENCES ledger.accounts (id);
        ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_user_dedup_key;
        ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_dedup_key_key UNIQUE (dedup_key);

        ALTER TABLE ledger.accounts DROP CONSTRAINT accounts_user_name_key;
        ALTER TABLE ledger.accounts ADD CONSTRAINT accounts_name_key UNIQUE (name);
    """)
    for t in ("accounts", "transactions", "raw_emails", "receipts"):
        op.execute(f"ALTER TABLE ledger.{t} DROP CONSTRAINT {t}_id_user_key")
    for t in TABLES:
        op.execute(f"ALTER TABLE ledger.{t} DROP COLUMN user_id")
    op.execute("""CREATE VIEW ledger.v_reportable_expenses AS
       SELECT t.* FROM ledger.transactions t
       WHERE t.status = 'confirmed' AND t.kind = 'expense'
         AND NOT EXISTS (SELECT 1 FROM ledger.transactions c WHERE c.parent_id = t.id)""")
    op.execute("""CREATE VIEW ledger.v_account_postings AS
       SELECT t.id AS transaction_id, t.account_id, t.status, t.occurred_at, t.kind,
              CASE WHEN t.kind IN ('expense', 'transfer') THEN -t.amount ELSE t.amount END AS amount
       FROM ledger.transactions t WHERE t.parent_id IS NULL
       UNION ALL
       SELECT t.id, t.counter_account_id, t.status, t.occurred_at, t.kind, t.amount
       FROM ledger.transactions t WHERE t.kind = 'transfer'""")

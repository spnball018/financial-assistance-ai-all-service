"""opening balances + account postings view (T6.4); receipt_item node type for T6.5

Revision ID: 0007
Revises: 0006
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_NODE_TYPES = "'payment','statement','statement_line','order','order_item','adjustment','manual'"


def upgrade() -> None:
    op.execute("ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_node_type_check")
    op.execute("ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_node_type_check "
               f"CHECK (node_type IN ({_NODE_TYPES},'opening_balance','receipt_item'))")
    op.execute("ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_kind_check")
    op.execute("ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_kind_check "
               "CHECK (kind IN ('expense','income','transfer','opening'))")
    # Money movements per account, so a balance is a plain SUM.
    # - The account leg comes from root rows only: a payment leaves the account once, whatever its
    #   children (order items, receipt items) say. expense/transfer = out, income/opening = in.
    # - Every transfer row, root or leaf, also lands on its counter account.
    # Each transfer therefore nets to zero across the user's accounts (the "two legs" of the entry).
    op.execute("""
    CREATE VIEW ledger.v_account_postings AS
    SELECT t.id AS transaction_id, t.account_id, t.status, t.occurred_at, t.kind,
           CASE WHEN t.kind IN ('expense','transfer') THEN -t.amount ELSE t.amount END AS amount
    FROM ledger.transactions t
    WHERE t.parent_id IS NULL
    UNION ALL
    SELECT t.id, t.counter_account_id, t.status, t.occurred_at, t.kind, t.amount
    FROM ledger.transactions t
    WHERE t.kind = 'transfer'
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS ledger.v_account_postings")
    op.execute("DELETE FROM ledger.transactions WHERE kind = 'opening' OR node_type IN ('opening_balance','receipt_item')")
    op.execute("ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_kind_check")
    op.execute("ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_kind_check "
               "CHECK (kind IN ('expense','income','transfer'))")
    op.execute("ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_node_type_check")
    op.execute("ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_node_type_check "
               f"CHECK (node_type IN ({_NODE_TYPES}))")

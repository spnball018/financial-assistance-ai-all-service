"""merchant rules (T6.2): memory can say category | transfer to own account | ask (don't remember)

Revision ID: 0006
Revises: 0005
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ledger.merchant_memory "
               "ADD COLUMN action text NOT NULL DEFAULT 'category' CHECK (action IN ('category','transfer','ask'))")
    op.execute("ALTER TABLE ledger.merchant_memory "
               "ADD COLUMN counter_account_id integer REFERENCES ledger.accounts(id)")
    op.execute("ALTER TABLE ledger.merchant_memory ALTER COLUMN category_id DROP NOT NULL")
    op.execute("ALTER TABLE ledger.merchant_memory ADD CONSTRAINT merchant_memory_action_target CHECK ("
               "(action = 'category' AND category_id IS NOT NULL AND counter_account_id IS NULL) OR "
               "(action = 'transfer' AND counter_account_id IS NOT NULL AND category_id IS NULL) OR "
               "(action = 'ask' AND category_id IS NULL AND counter_account_id IS NULL))")
    # 'account' = recognised as a transfer from the destination number matching accounts.masked_ref
    op.execute("ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_category_source_check")
    op.execute("ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_category_source_check "
               "CHECK (category_source IN ('memory','ai','user','manual','none','account'))")


def downgrade() -> None:
    op.execute("UPDATE ledger.transactions SET category_source = 'none' WHERE category_source = 'account'")
    op.execute("ALTER TABLE ledger.transactions DROP CONSTRAINT transactions_category_source_check")
    op.execute("ALTER TABLE ledger.transactions ADD CONSTRAINT transactions_category_source_check "
               "CHECK (category_source IN ('memory','ai','user','manual','none'))")
    op.execute("DELETE FROM ledger.merchant_memory WHERE action <> 'category'")
    op.execute("ALTER TABLE ledger.merchant_memory DROP CONSTRAINT merchant_memory_action_target")
    op.execute("ALTER TABLE ledger.merchant_memory ALTER COLUMN category_id SET NOT NULL")
    op.execute("ALTER TABLE ledger.merchant_memory DROP COLUMN counter_account_id")
    op.execute("ALTER TABLE ledger.merchant_memory DROP COLUMN action")

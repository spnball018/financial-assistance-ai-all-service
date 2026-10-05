"""expense category: donations & merit-making (monthly temple/charity giving)

Revision ID: 0004
Revises: 0003
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # keep Uncategorized last
    op.execute("UPDATE ledger.categories SET sort_order = 8 "
               "WHERE parent_id IS NULL AND name_en = 'Uncategorized'")
    op.execute("INSERT INTO ledger.categories (name_th, name_en, kind, sort_order) "
               "VALUES ('บริจาค/ทำบุญ', 'Donations & Merit-making', 'expense', 7)")


def downgrade() -> None:
    op.execute("DELETE FROM ledger.merchant_memory WHERE category_id = (SELECT id FROM ledger.categories "
               "WHERE parent_id IS NULL AND name_en = 'Donations & Merit-making')")
    op.execute("UPDATE ledger.transactions SET category_id = NULL, category_source = 'none' "
               "WHERE category_id = (SELECT id FROM ledger.categories "
               "WHERE parent_id IS NULL AND name_en = 'Donations & Merit-making')")
    op.execute("DELETE FROM ledger.categories WHERE parent_id IS NULL AND name_en = 'Donations & Merit-making'")
    op.execute("UPDATE ledger.categories SET sort_order = 7 "
               "WHERE parent_id IS NULL AND name_en = 'Uncategorized'")

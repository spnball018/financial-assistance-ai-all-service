"""expense category: Family (shared household money sent to the family), after Education

Revision ID: 0018
Revises: 0017
"""
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

_PARENT = "(SELECT id FROM ledger.categories WHERE parent_id IS NULL AND name_en = 'Family')"
_ALL = f"(SELECT id FROM ledger.categories WHERE id = {_PARENT} OR parent_id = {_PARENT})"
# top-level expense categories that move down one to make room, keeping Uncategorized last
_AFTER = "('Home & Woodworking', 'Donations & Merit-making', 'Uncategorized')"


def upgrade() -> None:
    op.execute(f"UPDATE ledger.categories SET sort_order = sort_order + 1 "
               f"WHERE parent_id IS NULL AND kind = 'expense' AND name_en IN {_AFTER}")
    op.execute("INSERT INTO ledger.categories (name_th, name_en, kind, sort_order) "
               "VALUES ('ครอบครัว', 'Family', 'expense', 8)")
    op.execute(f"INSERT INTO ledger.categories (parent_id, name_th, name_en, kind, sort_order) "
               f"VALUES ({_PARENT}, 'ค่าใช้จ่ายส่วนกลางครอบครัว', 'Family Shared Expenses', 'expense', 1)")


def downgrade() -> None:
    op.execute(f"DELETE FROM ledger.merchant_memory WHERE category_id IN {_ALL}")
    op.execute(f"UPDATE ledger.transactions SET category_id = NULL, category_source = 'none' "
               f"WHERE category_id IN {_ALL}")
    op.execute(f"DELETE FROM ledger.categories WHERE parent_id = {_PARENT}")
    op.execute("DELETE FROM ledger.categories WHERE parent_id IS NULL AND name_en = 'Family'")
    op.execute(f"UPDATE ledger.categories SET sort_order = sort_order - 1 "
               f"WHERE parent_id IS NULL AND kind = 'expense' AND name_en IN {_AFTER}")

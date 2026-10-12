"""expense category: hotels & lodging under transport

Revision ID: 0015
Revises: 0014
"""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

_HOTEL = ("(SELECT c.id FROM ledger.categories c JOIN ledger.categories p ON p.id = c.parent_id "
          "WHERE p.name_en = 'Transport & Travel' AND c.name_en = 'Hotels & Lodging')")


def upgrade() -> None:
    op.execute("INSERT INTO ledger.categories (parent_id, name_th, name_en, kind, sort_order) "
               "SELECT id, 'โรงแรม/ที่พัก', 'Hotels & Lodging', 'expense', 6 FROM ledger.categories "
               "WHERE parent_id IS NULL AND name_en = 'Transport & Travel'")


def downgrade() -> None:
    op.execute(f"DELETE FROM ledger.merchant_memory WHERE category_id = {_HOTEL}")
    op.execute(f"UPDATE ledger.transactions SET category_id = NULL, category_source = 'none' "
               f"WHERE category_id = {_HOTEL}")
    op.execute(f"DELETE FROM ledger.categories WHERE id = {_HOTEL}")

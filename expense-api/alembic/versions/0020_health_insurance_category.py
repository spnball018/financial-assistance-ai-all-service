"""expense category: health & life insurance under Health & Personal Care, after dental care
(car insurance stays under Transport & Travel > Maintenance & Insurance)

Revision ID: 0020
Revises: 0019
"""
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

_HEALTH = "(SELECT id FROM ledger.categories WHERE parent_id IS NULL AND name_en = 'Health & Personal Care')"
_INSURANCE = ("(SELECT c.id FROM ledger.categories c JOIN ledger.categories p ON p.id = c.parent_id "
              "WHERE p.name_en = 'Health & Personal Care' AND c.name_en = 'Health & Life Insurance')")


def upgrade() -> None:
    # right after dental; fitness and cosmetics move down one
    op.execute(f"UPDATE ledger.categories SET sort_order = sort_order + 1 WHERE parent_id = {_HEALTH} AND sort_order >= 3")
    op.execute(f"INSERT INTO ledger.categories (parent_id, name_th, name_en, kind, sort_order) "
               f"VALUES ({_HEALTH}, 'ประกันสุขภาพ/ประกันชีวิต', 'Health & Life Insurance', 'expense', 3)")


def downgrade() -> None:
    op.execute(f"DELETE FROM ledger.merchant_memory WHERE category_id = {_INSURANCE}")
    op.execute(f"UPDATE ledger.transactions SET category_id = NULL, category_source = 'none' "
               f"WHERE category_id = {_INSURANCE}")
    op.execute(f"DELETE FROM ledger.categories WHERE id = {_INSURANCE}")
    op.execute(f"UPDATE ledger.categories SET sort_order = sort_order - 1 WHERE parent_id = {_HEALTH} AND sort_order >= 4")

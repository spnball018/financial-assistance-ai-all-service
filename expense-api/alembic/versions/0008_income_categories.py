"""income categories (T6.6)

Revision ID: 0008
Revises: 0007
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_INCOME = [
    ("ค่าเช่า", "Rental Income"),
    ("งาน freelance", "Freelance"),
    ("เงินเดือน", "Salary"),
    ("ดอกเบี้ย/เงินปันผล", "Interest & Dividends"),
    ("รายรับอื่นๆ", "Other Income"),
]


def upgrade() -> None:
    for i, (th, en) in enumerate(_INCOME, start=1):
        op.execute("INSERT INTO ledger.categories (name_th, name_en, kind, sort_order) "
                   f"VALUES ('{th}', '{en}', 'income', {i})")


def downgrade() -> None:
    names = ", ".join(f"'{en}'" for _, en in _INCOME)
    op.execute(f"UPDATE ledger.transactions SET category_id = NULL, category_source = 'none' WHERE category_id IN "
               f"(SELECT id FROM ledger.categories WHERE kind = 'income' AND name_en IN ({names}))")
    op.execute(f"DELETE FROM ledger.categories WHERE kind = 'income' AND name_en IN ({names})")

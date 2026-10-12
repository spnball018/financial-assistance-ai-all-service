"""expense categories: home & woodworking (repairs, furniture, wood, tools); taxi & ride-hailing under transport;
dental care under health, and Medicine & Clinic renamed so medical bills are easy to find

Revision ID: 0014
Revises: 0013
"""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

_PARENT = "(SELECT id FROM ledger.categories WHERE parent_id IS NULL AND name_en = 'Home & Woodworking')"
_ALL = f"(SELECT id FROM ledger.categories WHERE id = {_PARENT} OR parent_id = {_PARENT})"
_TAXI = ("(SELECT c.id FROM ledger.categories c JOIN ledger.categories p ON p.id = c.parent_id "
         "WHERE p.name_en = 'Transport & Travel' AND c.name_en = 'Taxi & Ride-hailing')")
_DENTAL = ("(SELECT c.id FROM ledger.categories c JOIN ledger.categories p ON p.id = c.parent_id "
           "WHERE p.name_en = 'Health & Personal Care' AND c.name_en = 'Dental Care')")
_HEALTH = "(SELECT id FROM ledger.categories WHERE parent_id IS NULL AND name_en = 'Health & Personal Care')"


def upgrade() -> None:
    # after Donations, keep Uncategorized last
    op.execute("UPDATE ledger.categories SET sort_order = 9 "
               "WHERE parent_id IS NULL AND name_en = 'Uncategorized'")
    op.execute("UPDATE ledger.categories SET sort_order = 8 "
               "WHERE parent_id IS NULL AND name_en = 'Donations & Merit-making'")
    op.execute("INSERT INTO ledger.categories (name_th, name_en, kind, sort_order) "
               "VALUES ('บ้านและงานไม้', 'Home & Woodworking', 'expense', 7)")
    op.execute(f"""
        INSERT INTO ledger.categories (parent_id, name_th, name_en, kind, sort_order) VALUES
          ({_PARENT}, 'ซ่อมแซม/ต่อเติมบ้าน', 'Home Repair & Renovation', 'expense', 1),
          ({_PARENT}, 'เฟอร์นิเจอร์/ของแต่งบ้าน', 'Furniture & Decor', 'expense', 2),
          ({_PARENT}, 'ไม้และวัสดุ', 'Wood & Materials', 'expense', 3),
          ({_PARENT}, 'เครื่องมือช่าง', 'Tools', 'expense', 4)""")
    op.execute("INSERT INTO ledger.categories (parent_id, name_th, name_en, kind, sort_order) "
               "SELECT id, 'แท็กซี่/เรียกรถ', 'Taxi & Ride-hailing', 'expense', 5 FROM ledger.categories "
               "WHERE parent_id IS NULL AND name_en = 'Transport & Travel'")
    op.execute(f"UPDATE ledger.categories SET name_th = 'ค่ารักษาพยาบาล/ยา', name_en = 'Medical & Medicine' "
               f"WHERE parent_id = {_HEALTH} AND name_en = 'Medicine & Clinic'")
    # right after medical; fitness and cosmetics move down one
    op.execute(f"UPDATE ledger.categories SET sort_order = sort_order + 1 WHERE parent_id = {_HEALTH} AND sort_order >= 2")
    op.execute(f"INSERT INTO ledger.categories (parent_id, name_th, name_en, kind, sort_order) "
               f"VALUES ({_HEALTH}, 'ทำฟัน/ทันตกรรม', 'Dental Care', 'expense', 2)")


def downgrade() -> None:
    op.execute(f"DELETE FROM ledger.merchant_memory WHERE category_id = {_DENTAL}")
    op.execute(f"UPDATE ledger.transactions SET category_id = NULL, category_source = 'none' "
               f"WHERE category_id = {_DENTAL}")
    op.execute(f"DELETE FROM ledger.categories WHERE id = {_DENTAL}")
    op.execute(f"UPDATE ledger.categories SET sort_order = sort_order - 1 WHERE parent_id = {_HEALTH} AND sort_order >= 3")
    op.execute(f"UPDATE ledger.categories SET name_th = 'ค่ายา/คลินิก', name_en = 'Medicine & Clinic' "
               f"WHERE parent_id = {_HEALTH} AND name_en = 'Medical & Medicine'")
    op.execute(f"DELETE FROM ledger.merchant_memory WHERE category_id = {_TAXI}")
    op.execute(f"UPDATE ledger.transactions SET category_id = NULL, category_source = 'none' "
               f"WHERE category_id = {_TAXI}")
    op.execute(f"DELETE FROM ledger.categories WHERE id = {_TAXI}")
    op.execute(f"DELETE FROM ledger.merchant_memory WHERE category_id IN {_ALL}")
    op.execute(f"UPDATE ledger.transactions SET category_id = NULL, category_source = 'none' "
               f"WHERE category_id IN {_ALL}")
    op.execute(f"DELETE FROM ledger.categories WHERE parent_id = {_PARENT}")
    op.execute("DELETE FROM ledger.categories WHERE parent_id IS NULL AND name_en = 'Home & Woodworking'")
    op.execute("UPDATE ledger.categories SET sort_order = 7 "
               "WHERE parent_id IS NULL AND name_en = 'Donations & Merit-making'")
    op.execute("UPDATE ledger.categories SET sort_order = 8 "
               "WHERE parent_id IS NULL AND name_en = 'Uncategorized'")

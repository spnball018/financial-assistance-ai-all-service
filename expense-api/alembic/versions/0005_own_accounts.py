"""own accounts (T6.1): purpose/sort columns, investment type, seed the user's accounts

Revision ID: 0005
Revises: 0004
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# (name, type, institution, purpose). masked_ref (last digits / wallet id) is filled in Settings.
_ACCOUNTS = [
    ("KBank ฉุกเฉิน", "bank", "KBank", "ออมทรัพย์สำรองฉุกเฉิน ใช้ต่อเมื่อบัญชีรายรับหมด"),
    ("SCB ค่าเช่า", "bank", "SCB", "รับค่าเช่าบ้าน ตอนนี้ใช้เป็นรายได้"),
    ("SCB รายรับ", "bank", "SCB", "เงินเดือนและงาน freelance"),
    ("Bangkok Bank ลงทุน", "bank", "Bangkok Bank", "เงินพักสำหรับลงทุน"),
    ("BAY ออมทรัพย์", "bank", "Krungsri", "ออมทรัพย์"),
    ("Lazada", "ewallet", "Lazada", "ยอดเงินใน Lazada Wallet"),
    ("Shopee", "ewallet", "Shopee", "ยอดเงินใน ShopeePay"),
    ("TrueMoney", "ewallet", "TrueMoney", "บันทึกรายการเอง"),
    ("เป๋าตัง (KTB G-Wallet)", "ewallet", "Krungthai", "ผู้รับในอีเมลเป็นชื่อเจ้าของบัญชี"),
    ("Marketplace อื่นๆ", "ewallet", None, "wallet ของ marketplace อื่น"),
]


def _q(s):
    return "NULL" if s is None else "'" + s.replace("'", "''") + "'"


def upgrade() -> None:
    op.execute("ALTER TABLE ledger.accounts ADD COLUMN purpose text")
    op.execute("ALTER TABLE ledger.accounts ADD COLUMN sort_order integer NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE ledger.accounts DROP CONSTRAINT accounts_type_check")
    op.execute("ALTER TABLE ledger.accounts ADD CONSTRAINT accounts_type_check "
               "CHECK (type IN ('bank','credit_card','ewallet','cash','investment'))")
    # keep ids 1 and 2: email_sources and existing code refer to them
    op.execute("UPDATE ledger.accounts SET name = 'KBank ใช้จ่าย', sort_order = 1, "
               "purpose = 'บัญชีจ่ายประจำ ได้อีเมลแจ้งรายการจาก K PLUS' WHERE id = 1")
    op.execute("UPDATE ledger.accounts SET sort_order = 2 WHERE id = 2")
    for i, (name, typ, inst, purpose) in enumerate(_ACCOUNTS, start=3):
        op.execute("INSERT INTO ledger.accounts (name, type, institution, purpose, sort_order) "
                   f"VALUES ({_q(name)}, {_q(typ)}, {_q(inst)}, {_q(purpose)}, {i})")


def downgrade() -> None:
    names = ", ".join(_q(n) for n, *_ in _ACCOUNTS)
    op.execute(f"DELETE FROM ledger.accounts WHERE name IN ({names})")
    op.execute("UPDATE ledger.accounts SET name = 'KBank ออมทรัพย์' WHERE id = 1")
    op.execute("ALTER TABLE ledger.accounts DROP CONSTRAINT accounts_type_check")
    op.execute("ALTER TABLE ledger.accounts ADD CONSTRAINT accounts_type_check "
               "CHECK (type IN ('bank','credit_card','ewallet','cash'))")
    op.execute("ALTER TABLE ledger.accounts DROP COLUMN sort_order")
    op.execute("ALTER TABLE ledger.accounts DROP COLUMN purpose")

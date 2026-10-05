"""ledger schema: accounts, categories, transactions tree, memory, sync, settings

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# (name_th, name_en, [sub-categories (name_th, name_en)])
_EXPENSE_CATEGORIES = [
    ("อาหารและเครื่องดื่ม", "Food & Dining", [
        ("ตลาดสด/ของสด", "Fresh Market"),
        ("อาหารตามสั่ง/สตรีทฟู้ด", "Street Food"),
        ("คาเฟ่/เบเกอรี่", "Cafe & Bakery"),
        ("ร้านอาหาร/บุฟเฟต์", "Restaurant & Buffet"),
    ]),
    ("ของใช้ในบ้าน", "Groceries & Home Supplies", [
        ("ของใช้ทั่วไป", "General Supplies"),
        ("ผลิตภัณฑ์ทำความสะอาด", "Cleaning Products"),
        ("ซูเปอร์มาร์เก็ต", "Supermarket"),
    ]),
    ("ช้อปปิ้งและไลฟ์สไตล์", "Shopping & Lifestyle", [
        ("เสื้อผ้า/แฟชั่น", "Fashion"),
        ("อุปกรณ์ไอที/แกดเจ็ต", "IT & Gadgets"),
        ("งานอดิเรก/เครื่องเขียน", "Hobbies & Stationery"),
    ]),
    ("การเดินทางและยานพาหนะ", "Transport & Travel", [
        ("ค่าน้ำมัน", "Fuel"),
        ("ค่าทางด่วน", "Tolls"),
        ("รถไฟฟ้า/รถสาธารณะ", "Public Transport"),
        ("ซ่อมบำรุง/ประกัน", "Maintenance & Insurance"),
    ]),
    ("บิลและสาธารณูปโภค", "Bills & Utilities", [
        ("ค่าน้ำ", "Water"),
        ("ค่าไฟ", "Electricity"),
        ("ค่าเน็ต/โทรศัพท์", "Internet & Phone"),
        ("Subscription บริการดิจิทัล", "Digital Subscriptions"),
    ]),
    ("สุขภาพและความงาม", "Health & Personal Care", [
        ("ค่ายา/คลินิก", "Medicine & Clinic"),
        ("ฟิตเนส/กีฬา", "Fitness & Sports"),
        ("เครื่องสำอาง/สกินแคร์", "Cosmetics & Skincare"),
    ]),
    ("อื่นๆ / ยังไม่ระบุ", "Uncategorized", []),
]


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def upgrade() -> None:
    op.execute("""
    CREATE TABLE ledger.accounts (
        id            serial PRIMARY KEY,
        name          text NOT NULL UNIQUE,
        type          text NOT NULL CHECK (type IN ('bank','credit_card','ewallet','cash')),
        institution   text,
        masked_ref    text,
        currency      char(3) NOT NULL DEFAULT 'THB',
        is_active     boolean NOT NULL DEFAULT true,
        created_at    timestamptz NOT NULL DEFAULT now()
    )""")

    op.execute("""
    CREATE TABLE ledger.categories (
        id          serial PRIMARY KEY,
        parent_id   integer REFERENCES ledger.categories(id),
        name_th     text NOT NULL,
        name_en     text NOT NULL,
        kind        text NOT NULL DEFAULT 'expense' CHECK (kind IN ('expense','income')),
        sort_order  integer NOT NULL DEFAULT 0,
        UNIQUE (parent_id, name_th)
    )""")

    # transactions form a tree: payment/statement -> order/statement_line -> order_item/adjustment.
    # Reports read only leaf rows (see v_reportable_expenses).
    op.execute("""
    CREATE TABLE ledger.transactions (
        id                  bigserial PRIMARY KEY,
        account_id          integer NOT NULL REFERENCES ledger.accounts(id),
        parent_id           bigint REFERENCES ledger.transactions(id) ON DELETE CASCADE,
        node_type           text NOT NULL CHECK (node_type IN
                              ('payment','statement','statement_line','order','order_item','adjustment','manual')),
        kind                text NOT NULL CHECK (kind IN ('expense','income','transfer')),
        counter_account_id  integer REFERENCES ledger.accounts(id),
        ref_transaction_id  bigint REFERENCES ledger.transactions(id),
        status              text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','confirmed','ignored')),
        needs_review        boolean NOT NULL DEFAULT false,
        amount              numeric(15,2) NOT NULL,
        currency            char(3) NOT NULL DEFAULT 'THB',
        occurred_at         timestamptz NOT NULL,
        has_time            boolean NOT NULL DEFAULT true,
        source              text NOT NULL CHECK (source IN
                              ('kbank_email','ktc_pdf','shopee_email','lazada_email','manual')),
        source_ref          text,
        dedup_key           text NOT NULL UNIQUE,
        gmail_message_id    text,
        merchant_raw        text,
        merchant_key        text,
        description         text,
        category_id         integer REFERENCES ledger.categories(id),
        category_source     text NOT NULL DEFAULT 'none'
                              CHECK (category_source IN ('memory','ai','user','manual','none')),
        ai_confidence       numeric(4,3),
        ai_reason           text,
        raw                 jsonb,
        confirmed_at        timestamptz,
        created_at          timestamptz NOT NULL DEFAULT now(),
        updated_at          timestamptz NOT NULL DEFAULT now(),
        CHECK (kind <> 'transfer' OR counter_account_id IS NOT NULL)
    )""")
    op.execute("CREATE INDEX ix_transactions_parent ON ledger.transactions(parent_id)")
    op.execute("CREATE INDEX ix_transactions_status ON ledger.transactions(status)")
    op.execute("CREATE INDEX ix_transactions_occurred ON ledger.transactions(account_id, occurred_at)")
    op.execute("CREATE INDEX ix_transactions_merchant ON ledger.transactions(merchant_key)")

    # Parsed e-commerce orders waiting to be matched to a payment / statement line.
    op.execute("""
    CREATE TABLE ledger.ecommerce_orders (
        id                bigserial PRIMARY KEY,
        platform          text NOT NULL CHECK (platform IN ('shopee','lazada')),
        order_id          text NOT NULL,
        ordered_at        timestamptz NOT NULL,
        total_amount      numeric(15,2) NOT NULL,
        currency          char(3) NOT NULL DEFAULT 'THB',
        gmail_message_id  text,
        raw               jsonb,
        matched_txn_id    bigint REFERENCES ledger.transactions(id) ON DELETE SET NULL,
        created_at        timestamptz NOT NULL DEFAULT now(),
        UNIQUE (platform, order_id)
    )""")
    # kind: item | shipping | discount | coin | other  (non-item kinds become 'adjustment' rows)
    op.execute("""
    CREATE TABLE ledger.ecommerce_order_items (
        id         bigserial PRIMARY KEY,
        order_pk   bigint NOT NULL REFERENCES ledger.ecommerce_orders(id) ON DELETE CASCADE,
        kind       text NOT NULL DEFAULT 'item' CHECK (kind IN ('item','shipping','discount','coin','other')),
        name       text NOT NULL,
        quantity   integer NOT NULL DEFAULT 1,
        amount     numeric(15,2) NOT NULL
    )""")

    op.execute("""
    CREATE TABLE ledger.merchant_memory (
        id                 serial PRIMARY KEY,
        merchant_key       text NOT NULL UNIQUE,
        category_id        integer NOT NULL REFERENCES ledger.categories(id),
        hit_count          integer NOT NULL DEFAULT 1,
        last_confirmed_at  timestamptz NOT NULL DEFAULT now()
    )""")

    op.execute("""
    CREATE TABLE ledger.sync_runs (
        id          serial PRIMARY KEY,
        started_at  timestamptz NOT NULL DEFAULT now(),
        finished_at timestamptz,
        status      text NOT NULL DEFAULT 'running' CHECK (status IN ('running','success','failed')),
        range_from  timestamptz,
        range_to    timestamptz,
        counts      jsonb,
        error       text
    )""")

    op.execute("""
    CREATE TABLE ledger.settings (
        key              text PRIMARY KEY,
        value_encrypted  text NOT NULL,
        updated_at       timestamptz NOT NULL DEFAULT now()
    )""")

    # Single source for reports: confirmed expense rows with no children.
    op.execute("""
    CREATE VIEW ledger.v_reportable_expenses AS
    SELECT t.*
    FROM ledger.transactions t
    WHERE t.status = 'confirmed'
      AND t.kind = 'expense'
      AND NOT EXISTS (SELECT 1 FROM ledger.transactions c WHERE c.parent_id = t.id)
    """)

    # ── seeds ──
    op.execute("""
    INSERT INTO ledger.accounts (name, type, institution) VALUES
        ('KBank ออมทรัพย์', 'bank', 'KBank'),
        ('บัตรเครดิต KTC', 'credit_card', 'KTC')
    """)

    for i, (th, en, subs) in enumerate(_EXPENSE_CATEGORIES, start=1):
        op.execute(
            "INSERT INTO ledger.categories (name_th, name_en, kind, sort_order) "
            f"VALUES ({_q(th)}, {_q(en)}, 'expense', {i})"
        )
        for j, (sth, sen) in enumerate(subs, start=1):
            op.execute(
                "INSERT INTO ledger.categories (parent_id, name_th, name_en, kind, sort_order) "
                f"SELECT id, {_q(sth)}, {_q(sen)}, 'expense', {j} "
                f"FROM ledger.categories WHERE parent_id IS NULL AND name_th = {_q(th)}"
            )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS ledger.v_reportable_expenses")
    for t in ("settings", "sync_runs", "merchant_memory", "ecommerce_order_items",
              "ecommerce_orders", "transactions", "categories", "accounts"):
        op.execute(f"DROP TABLE IF EXISTS ledger.{t}")

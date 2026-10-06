# ER diagram: schema `ledger`

สร้างจาก DB จริง (migration 0011, 2026-10-06) ถ้าเพิ่ม migration ให้อัปเดตไฟล์นี้ด้วย ส่วนตาราง `alembic_version` ไม่ได้แสดง

ภาพ PNG: [er-diagram.png](er-diagram.png) (render จาก Mermaid ข้างล่าง ใช้ดูใน editor ที่ไม่รองรับ Mermaid ถ้าแก้ diagram ให้ render ใหม่ด้วย `npx @mermaid-js/mermaid-cli -i <ไฟล์ .mmd> -o docs/er-diagram.png -w 2400 -b white`)

```mermaid
erDiagram
    accounts {
        int id PK
        text name UK
        text type "bank | credit_card | ewallet | cash | investment"
        text institution
        text masked_ref "เลขท้าย >= 4 ตัว ใช้จับคู่การโอน"
        char currency
        bool is_active
        text purpose
        int sort_order
        timestamptz created_at
    }

    categories {
        int id PK
        int parent_id FK "หมวดหลัก (NULL = หมวดหลัก)"
        text name_th "UK ร่วมกับ parent_id"
        text name_en
        text kind "expense | income"
        int sort_order
    }

    transactions {
        bigint id PK
        int account_id FK
        bigint parent_id FK "ต้นไม้ แม่ -> ลูก"
        text node_type "payment | statement | statement_line | order | order_item | adjustment | manual | opening_balance | receipt_item"
        text kind "expense | income | transfer | opening"
        int counter_account_id FK "บัญชีปลายทาง (บังคับเมื่อ transfer)"
        bigint ref_transaction_id FK "อ้างรายการที่เกี่ยวข้อง เช่น บิล KTC"
        text status "pending | confirmed | ignored"
        bool needs_review "ผลรวมลูก != แม่"
        numeric amount
        char currency
        timestamptz occurred_at
        bool has_time
        text source "kbank_email | ktc_pdf | shopee_email | lazada_email | manual"
        text source_ref
        text dedup_key UK
        text gmail_message_id
        text merchant_raw
        text merchant_key "ใช้ค้น merchant_memory"
        text description
        int category_id FK
        text category_source "none | memory | ai | user | manual | account"
        numeric ai_confidence
        text ai_reason
        jsonb raw
        bigint raw_email_id FK
        bigint receipt_id FK "ลูกที่มาจากใบเสร็จ"
        timestamptz confirmed_at
        timestamptz created_at
        timestamptz updated_at
    }

    merchant_memory {
        int id PK
        text merchant_key UK "ผู้รับ หรือ ผู้รับ|เลขท้ายปลายทาง (กฎ transfer)"
        text action "category | transfer | ask"
        int category_id FK "เมื่อ action = category"
        int counter_account_id FK "เมื่อ action = transfer"
        int hit_count
        timestamptz last_confirmed_at
    }

    email_sources {
        int id PK
        text source UK "kbank_email | ktc_pdf | shopee_email | lazada_email"
        text gmail_label
        text sender_filter
        int account_id FK
        bool is_active
        timestamptz updated_at
    }

    raw_emails {
        bigint id PK
        text gmail_message_id UK
        text source "ตรงกับ email_sources.source (ไม่มี FK)"
        text gmail_label
        text subject
        text sender
        timestamptz received_at
        text text_body
        text html_body
        jsonb attachments "รายการไฟล์แนบ (metadata)"
        text parse_status "pending | parsed | skipped | failed"
        text parser_version
        text parse_error
        timestamptz parsed_at
        timestamptz fetched_at
    }

    raw_attachments {
        bigint id PK
        bigint raw_email_id FK "UK ร่วมกับ attachment_id"
        text attachment_id
        text filename
        text mime_type
        bytea content "ไฟล์ต้นฉบับ (PDF KTC ยังติดรหัส)"
    }

    ecommerce_orders {
        bigint id PK
        text platform "shopee | lazada (UK ร่วมกับ order_id)"
        text order_id
        timestamptz ordered_at
        numeric total_amount
        char currency
        text gmail_message_id
        jsonb raw
        bigint matched_txn_id FK "รายการจ่ายที่จับคู่ได้"
        bigint raw_email_id FK
        timestamptz created_at
    }

    ecommerce_order_items {
        bigint id PK
        bigint order_pk FK
        text kind "item | shipping | discount | coin | other"
        text name
        int quantity
        numeric amount
    }

    receipts {
        bigint id PK
        bigint transaction_id FK "รายการจ่ายที่แนบ"
        text storage_uri "path ของไฟล์ใน RECEIPTS_DIR (ไม่เก็บรูปใน DB)"
        text mime_type
        text sha256 UK
        timestamptz uploaded_at
        text ocr_status "pending | done | failed"
        jsonb ocr_result "รายการที่อ่านได้ + หมวดที่เสนอ"
        text ocr_model
        text ocr_error
        timestamptz applied_at
    }

    sync_runs {
        int id PK
        timestamptz started_at
        timestamptz finished_at
        text status "running | success | failed"
        timestamptz range_from
        timestamptz range_to
        jsonb counts
        text error
    }

    settings {
        text key PK "เช่น ktc_pdf_password"
        text value_encrypted "Fernet"
        timestamptz updated_at
    }

    users {
        int id PK "1 = เจ้าของข้อมูลเดิม"
        text email UK "ตัวเล็กเสมอ"
        text name "จาก Google"
        text role "admin | member"
        bool is_active
        timestamptz created_at
        timestamptz last_login_at
    }

    auth_sessions {
        bigint id PK
        int user_id FK
        text token_hash UK "sha256 ของ cookie"
        text user_agent
        timestamptz created_at
        timestamptz last_seen_at
        timestamptz expires_at
    }

    api_tokens {
        int id PK
        int user_id FK
        text name
        text token_prefix "8 ตัวแรก ไว้แสดง"
        text token_hash UK "sha256 ของ token"
        timestamptz created_at
        timestamptz expires_at "NULL = ไม่หมดอายุ"
        timestamptz last_used_at
        timestamptz revoked_at
    }

    accounts ||--o{ transactions : "account_id"
    accounts |o--o{ transactions : "counter_account_id"
    accounts |o--o{ email_sources : "account_id"
    categories |o--o{ categories : "parent_id"
    categories |o--o{ transactions : "category_id"
    categories |o--o{ merchant_memory : "category_id"
    accounts |o--o{ merchant_memory : "counter_account_id"
    transactions |o--o{ transactions : "parent_id"
    transactions |o--o{ transactions : "ref_transaction_id"
    transactions |o--o{ ecommerce_orders : "matched_txn_id"
    raw_emails |o--o{ transactions : "raw_email_id"
    raw_emails |o--o{ ecommerce_orders : "raw_email_id"
    raw_emails ||--o{ raw_attachments : "raw_email_id"
    ecommerce_orders ||--o{ ecommerce_order_items : "order_pk"
    transactions |o--o{ receipts : "transaction_id"
    receipts |o--o{ transactions : "receipt_id"
    email_sources ||..o{ raw_emails : "source (ตรรกะ ไม่มี FK)"
    users ||--o{ auth_sessions : "user_id"
    users ||--o{ api_tokens : "user_id"
```

## กลุ่มของตาราง

| กลุ่ม | ตาราง | หน้าที่ |
|---|---|---|
| ข้อมูลหลัก (master) | `accounts`, `categories` | บัญชีของผู้ใช้ และหมวดหมู่ 2 ระดับ |
| ledger | `transactions` | ทุกรายการ เก็บเป็นต้นไม้ในตารางเดียว |
| การเรียนรู้ | `merchant_memory` | กฎต่อผู้รับที่ผู้ใช้เคยยืนยัน: หมวด / โอนเข้าบัญชีตัวเอง / ไม่ต้องจำ |
| ข้อมูลดิบ | `email_sources`, `raw_emails`, `raw_attachments`, `sync_runs` | label ต่อแหล่ง อีเมล/ไฟล์ต้นฉบับ และประวัติ sync |
| รอจับคู่ | `ecommerce_orders`, `ecommerce_order_items` | order ของ Shopee/Lazada ก่อนผูกกับรายการจ่าย (ยังไม่ได้ใช้ รอ T1.3) |
| ใบเสร็จ | `receipts` | รูปใบเสร็จและผลที่อ่านได้ ลูกที่แยกออกมาชี้กลับด้วย `transactions.receipt_id` |
| ความลับ | `settings` | ค่าที่เข้ารหัส เช่น รหัส PDF ของ KTC |
| ผู้ใช้ | `users`, `auth_sessions`, `api_tokens` | ผู้ใช้ (admin/member), session จาก Google sign-in และ API token เก็บแค่ sha256 ของความลับ ข้อมูลยังไม่แยกต่อผู้ใช้ (P7.2) |

## View

- **`v_reportable_expenses`**: `transactions` ที่ `status = 'confirmed'`, `kind = 'expense'` และไม่มีลูก (leaf) เป็นแหล่งเดียวของรายงานรายจ่าย จึงไม่นับรายการแม่และ transfer

- **`v_account_postings`**: ความเคลื่อนไหวของเงินต่อบัญชี (บวก = เข้า, ลบ = ออก) ใช้คำนวณยอดคงเหลือ ขาบัญชีมาจากรายการ root (expense/transfer ออก, income/opening เข้า) และทุก transfer มีอีกขาเข้าบัญชีปลายทาง การโอนระหว่างบัญชีตัวเองจึงรวมกันได้ 0

## กติกาที่ schema บังคับ

- `transactions.kind = 'transfer'` ต้องมี `counter_account_id`
- `transactions.dedup_key` ไม่ซ้ำ ทำให้ sync ซ้ำได้โดยไม่เกิดรายการซ้ำ
- ลบรายการแม่จะลบลูกทั้งต้นไม้ (`parent_id ON DELETE CASCADE`) ลบอีเมลดิบจะลบไฟล์แนบ ส่วนรายการที่อ้างอีเมลนั้นจะเหลือ `raw_email_id = NULL`
- `email_sources` จะเปิด (`is_active`) ได้ก็ต่อเมื่อมี `gmail_label`

## จะเปลี่ยนใน Phase 6 (ยังไม่มีใน DB)


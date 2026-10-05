# ER diagram: schema `ledger`

สร้างจาก DB จริง (migration 0005, 2026-10-05) ถ้าเพิ่ม migration ให้อัปเดตไฟล์นี้ด้วย ส่วนตาราง `alembic_version` ไม่ได้แสดง

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
        text node_type "payment | statement | statement_line | order | order_item | adjustment | manual"
        text kind "expense | income | transfer"
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
        text category_source "none | memory | ai | user | manual"
        numeric ai_confidence
        text ai_reason
        jsonb raw
        bigint raw_email_id FK
        timestamptz confirmed_at
        timestamptz created_at
        timestamptz updated_at
    }

    merchant_memory {
        int id PK
        text merchant_key UK
        int category_id FK
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

    accounts ||--o{ transactions : "account_id"
    accounts |o--o{ transactions : "counter_account_id"
    accounts |o--o{ email_sources : "account_id"
    categories |o--o{ categories : "parent_id"
    categories |o--o{ transactions : "category_id"
    categories ||--o{ merchant_memory : "category_id"
    transactions |o--o{ transactions : "parent_id"
    transactions |o--o{ transactions : "ref_transaction_id"
    transactions |o--o{ ecommerce_orders : "matched_txn_id"
    raw_emails |o--o{ transactions : "raw_email_id"
    raw_emails |o--o{ ecommerce_orders : "raw_email_id"
    raw_emails ||--o{ raw_attachments : "raw_email_id"
    ecommerce_orders ||--o{ ecommerce_order_items : "order_pk"
    email_sources ||..o{ raw_emails : "source (ตรรกะ ไม่มี FK)"
```

## กลุ่มของตาราง

| กลุ่ม | ตาราง | หน้าที่ |
|---|---|---|
| ข้อมูลหลัก (master) | `accounts`, `categories` | บัญชีของผู้ใช้ และหมวดหมู่ 2 ระดับ |
| ledger | `transactions` | ทุกรายการ เก็บเป็นต้นไม้ในตารางเดียว |
| การเรียนรู้ | `merchant_memory` | ร้าน/ผู้รับ → หมวด ที่ผู้ใช้เคยยืนยัน |
| ข้อมูลดิบ | `email_sources`, `raw_emails`, `raw_attachments`, `sync_runs` | label ต่อแหล่ง อีเมล/ไฟล์ต้นฉบับ และประวัติ sync |
| รอจับคู่ | `ecommerce_orders`, `ecommerce_order_items` | order ของ Shopee/Lazada ก่อนผูกกับรายการจ่าย (ยังไม่ได้ใช้ รอ T1.3) |
| ความลับ | `settings` | ค่าที่เข้ารหัส เช่น รหัส PDF ของ KTC |

## View

- **`v_reportable_expenses`**: `transactions` ที่ `status = 'confirmed'`, `kind = 'expense'` และไม่มีลูก (leaf) เป็นแหล่งเดียวของรายงานรายจ่าย จึงไม่นับรายการแม่และ transfer

## กติกาที่ schema บังคับ

- `transactions.kind = 'transfer'` ต้องมี `counter_account_id`
- `transactions.dedup_key` ไม่ซ้ำ ทำให้ sync ซ้ำได้โดยไม่เกิดรายการซ้ำ
- ลบรายการแม่จะลบลูกทั้งต้นไม้ (`parent_id ON DELETE CASCADE`) ลบอีเมลดิบจะลบไฟล์แนบ ส่วนรายการที่อ้างอีเมลนั้นจะเหลือ `raw_email_id = NULL`
- `email_sources` จะเปิด (`is_active`) ได้ก็ต่อเมื่อมี `gmail_label`

## จะเปลี่ยนใน Phase 6 (ยังไม่มีใน DB)

- **T6.2** `merchant_memory`: เพิ่ม `action` (`category | transfer | ask`) และ `counter_account_id` → `accounts`
- **T6.4** `transactions`: `node_type = opening_balance` และ `kind = opening` สำหรับยอดเริ่มต้น และ view `v_postings` แปลงแต่ละรายการเป็น 2 ขา (เดบิต/เครดิต) ใช้คำนวณยอดคงเหลือ
- **T6.5** ตารางเก็บรูปใบเสร็จ และ `node_type = receipt_item` สำหรับรายการย่อยจากใบเสร็จ

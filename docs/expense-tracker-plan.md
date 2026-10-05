# แผน: Automated Expense Tracker & Smart Categorizer (MVP)

## Context

ตอนนี้ระบบดึงอีเมลผ่าน LangGraph แบบ CLI โดยให้ LLM อ่านจาก snippet แล้วบันทึกลงตาราง `transactions` ตารางเดียว ยังไม่มี status, dedup ตามแหล่งที่มา, หมวดหมู่, Merchant Memory หรือ UI เลย
PRD ต้องการให้ผู้ใช้กด Refresh แล้วรายการเข้าคิว Pending เพื่อตรวจและ Confirm ก่อนนับในรายงาน และ AI ต้องเรียนรู้จากการแก้ไขของผู้ใช้

**ข้อตัดสินใจที่ได้จากผู้ใช้แล้ว**
- ใช้ ledger แยกตาม account (KBank, KTC, e-wallet, เงินสด ฯลฯ) และรองรับรายรับในอนาคต
- ใช้โครงสร้างแม่/ลูก รายงานนับเฉพาะรายการลูกที่อยู่ปลายสุด (leaf)
- จ่ายบิล KTC จาก KBank บันทึกเป็น transfer จึงไม่ถูกนับเป็นรายจ่าย
- Order ของ Shopee/Lazada แตกเป็นรายการสินค้า ส่วนค่าส่ง/ส่วนลด/coin แยกเป็นบรรทัดปรับยอด เพื่อให้ผลรวมของลูกเท่ากับแม่เสมอ
- ไม่ทำ refund/cancel ในเฟสนี้ (ใส่ไว้ใน backlog)
- รายการ KTC ใช้เวลา 00:00 และตั้ง `has_time=false`
- ทิ้งข้อมูลเก่าแล้วเริ่มใหม่
- ยังไม่มีระบบ user (ทำ Google Auth ภายหลัง)
- UI ใช้ React + Vite
- AI ใช้ Gemini API key ผ่าน SDK `google-genai` ซึ่งสลับไป Vertex ได้ด้วย env

## สถาปัตยกรรม

| Service | สถานะ | หน้าที่ |
|---|---|---|
| `expense-api/` (ใหม่, FastAPI, host port 8002) | โฟลเดอร์ในรีโปนี้ | เป็นเจ้าของ DB schema (Alembic), sync, reconcile, categorize, REST API |
| `web/` (ใหม่, React + Vite, host port 3000) | โฟลเดอร์ในรีโปนี้ | หน้า Review, Manual Entry, Settings, Summary |
| `gmail-service/` | แก้ใน submodule | เพิ่มการดึงเนื้ออีเมลเต็ม, attachment, pagination |
| `credit-card-statement-service/` | แก้ | เพิ่ม endpoint ปลดล็อก PDF ของ KTC แล้ว parse |
| `langgraph/` | ยังไม่แตะใน MVP | ภายหลังจะเปลี่ยนเป็น chatbot ที่ query จาก ledger |

ตารางใหม่ทั้งหมดอยู่ใน Postgres schema `ledger` เหตุผลคือ [save_transactions.py](langgraph/nodes/save_transactions.py) เรียก `metadata.create_all` ซึ่งจะสร้างตาราง `public.transactions` ขึ้นมาใหม่ ถ้าใช้ชื่อเดียวกันจะชนกัน

## Data model (schema `ledger`)

- **accounts**: id, name, type (`bank|credit_card|ewallet|cash`), institution, masked_ref, currency, is_active
  - seed ไว้ 2 รายการ: KBank ออมทรัพย์ และบัตร KTC
- **categories**: id, parent_id, name_th, name_en, kind (`expense|income`), sort_order
  - seed หมวดหลัก 7 หมวดพร้อมหมวดย่อยตาม PRD
- **transactions**: เก็บเป็นต้นไม้ในตารางเดียว
  - ลำดับชั้นและการผูกบัญชี:
    - `id`, `account_id`, `parent_id` (FK ไปที่ตัวเอง)
    - `node_type`: `payment | statement | statement_line | order | order_item | adjustment | manual`
    - `kind` (`expense|income|transfer`) และ `counter_account_id` (ใช้กับ transfer)
  - สถานะ: `status` (`pending|confirmed|ignored`), `needs_review` (ใช้เมื่อผลรวมของลูกไม่เท่ากับแม่)
  - ยอดและเวลา: `amount`, `currency`, `occurred_at` (timestamptz, Asia/Bangkok), `has_time`
  - แหล่งที่มาและ dedup:
    - `source` (`kbank_email|ktc_pdf|shopee_email|lazada_email|manual`), `source_ref`
    - `dedup_key` (UNIQUE), `gmail_message_id`
  - ร้านค้า: `merchant_raw`, `merchant_key`, `description`
  - หมวดหมู่: `category_id`, `category_source` (`memory|ai|user|manual|none`), `ai_confidence`, `ai_reason`
  - อื่นๆ: `raw` (jsonb), `confirmed_at`, timestamps
- **ecommerce_orders / ecommerce_order_items**: staging ของ order ที่ parse ได้แต่ยังไม่ได้จับคู่ เมื่อจับคู่ได้จะแปลงเป็นลูก `order → order_item/adjustment` ของรายการจ่ายนั้น
- **email_sources**: ตั้งค่าว่าแต่ละแหล่งดึงอีเมลจาก label ไหน
  - id, source (`kbank_email|ktc_pdf|shopee_email|lazada_email`, UNIQUE), gmail_label, sender_filter (เช่น `KPLUS@kasikornbank.com`), account_id, is_active
  - seed ด้วย label ที่ผู้ใช้ให้มา และแก้ได้จากหน้า Settings โดยไม่ต้อง deploy ใหม่
- **raw_emails**: เก็บอีเมลต้นฉบับทุกฉบับที่ sync ดึงมา เพื่อ parse ใหม่ได้โดยไม่ต้องดึงจาก Gmail ซ้ำ (เช่น ตอนแก้ parser หรือเพิ่มประเภทใหม่อย่าง Top-up)
  - id, gmail_message_id (UNIQUE), source, gmail_label, subject, sender, received_at, text_body, html_body, attachments (jsonb: id, filename, mime, size), fetched_at
  - ผลการ parse: parse_status (`parsed|skipped|failed`), parser_version, parse_error, parsed_at
  - **raw_attachments**: raw_email_id, attachment_id, filename, mime_type, content (bytea) เก็บ PDF ของ KTC ตามต้นฉบับซึ่งยังติดรหัสผ่านอยู่ ไม่เก็บฉบับที่ปลดล็อกแล้ว
  - `transactions.raw_email_id` และ `ecommerce_orders.raw_email_id` FK กลับมาที่ตารางนี้ เพื่อตามรอยว่ารายการมาจากอีเมลไหน
- **merchant_memory**: merchant_key (UNIQUE), category_id, hit_count, last_confirmed_at
- **sync_runs**: started/finished, status, range_from/to, counts (jsonb), error
- **settings**: key, value_encrypted (Fernet ใช้ key จาก env `SECRET_KEY`) สำหรับ KTC password
- **View `v_reportable_expenses`**: `status='confirmed' AND kind='expense'` และเป็น leaf (ไม่มีลูก) ใช้เป็นแหล่งเดียวของรายงาน

**กฎการผูกรายการ**
- KBank จ่าย Shopee: node `payment` บน KBank → `order` → `order_item` + `adjustment`
- จ่ายบิล KTC จาก KBank: KBank `payment` เป็น `kind=transfer` (KBank → KTC) อ้างถึง `statement` ไม่นับในรายงาน และจ่ายไม่เต็มยอดก็ไม่เป็นไร
- KTC statement: node `statement` บน KTC account → `statement_line` (kind=expense)
- ซื้อ Shopee ด้วยบัตร KTC: `statement_line` ที่จับคู่ได้ → `order` → `order_item` + `adjustment`

## สถานะ (อัปเดต 2026-10-05)

| งาน | สถานะ |
|---|---|
| T0.2 โครง expense-api | เสร็จ (ยังไม่ได้ยืนยันด้วย docker compose) |
| T0.3, T0.4 migration 0001, 0002 | เขียนแล้ว ยังไม่ได้รันกับ Postgres จริง |
| T0.5 migration 0003 (raw_emails, email_sources) | เขียนแล้ว ยังไม่ได้รันกับ Postgres จริง |
| T1.1 gmail-service | เสร็จและทดสอบแล้ว (ยกเว้น endpoint ไฟล์แนบ) |
| T1.2 parser KBank | เสร็จ ทดสอบกับอีเมลจริง 419 ฉบับ |
| API: `/accounts`, `/categories`, `/email-sources` | เขียนแล้ว ยังไม่ได้ทดสอบกับ DB จริง |
| T0.1 fixture KTC / Shopee / Lazada | ยังไม่มี (รอชื่อ label) |

**Spec ที่เปลี่ยนจากแผนเดิม**
- **gmail-service ไม่มี logic การเงิน**: เป็นแค่ตัวกลางอ่าน Gmail ทั่วไป มี endpoint `GET /api/messages/{id}`, `GET /api/messages/{id}/attachments/{att_id}`, `DELETE /api/messages/{id}` (ย้ายไป Trash เท่านั้น) และ `page_token` ใน `/api/emails`
- **OAuth scope**: เปลี่ยนจาก `gmail.readonly` เป็น `gmail.modify` (ต้อง auth ใหม่) ลบถาวรไม่ได้ด้วย scope นี้ ตั้งใจให้เป็นแบบนั้น
- **Label ต่อแหล่งอยู่ใน `ledger.email_sources`**: KBank = `financial-kasikorn` ส่วน KTC/Shopee/Lazada seed เป็นปิดไว้จนกว่าจะตั้ง label (ตาราง enforce ว่าเปิดได้ต่อเมื่อมี label)
- **เก็บ raw ทุกอย่างใน DB**: `raw_emails`, `raw_attachments` และ `raw_email_id` บน transactions/orders
- **Gmail ตอบ 500 เป็นช่วงๆ** เมื่อดึงจำนวนมาก (น่าจะ rate limit) sync ต้อง retry/backoff และดึงทีละหน้าเล็กๆ

**ข้อเท็จจริงจากอีเมล KBank จริง (ใช้เป็น spec ของ parser)**
- subject ที่พบ: Bill Payment, Funds Transfer, PromptPay Funds Transfer, Donation (สำเร็จ) กับ Bill Payment (Failure) และ Top-up
- ใช้ส่วนข้อความธรรมดาของอีเมล ไม่มี HTML body; ใช้ label ภาษาอังกฤษอ่านค่า และอ่านชื่อผู้รับจากบล็อกภาษาไทย
- PromptPay มี 2 แบบ: โอนเข้า wallet (`ชื่อ Wallet`) และโอนผ่านรหัสพร้อมเพย์ (`ชื่อผู้รับเงิน`) แบบหลังเป็นส่วนใหญ่
- `(Failure)` ข้ามไป ส่วน `Top-up` ยังไม่รองรับ (**ต้องตัดสินใจ**: นับเป็น transfer ไป e-wallet หรือรายจ่าย)
- บิล KTC จาก KBank คือ Bill Payment ที่บริษัทผู้รับเป็น `KRUNGTHAI CARD PUBLIC COMPANY LIMITED` ใช้ตรวจจับใน T2.2

## งาน (ทำทีละงาน ตามลำดับ)

**Phase 0: รากฐาน**
- **T0.1** ผู้ใช้ส่งอีเมลตัวอย่างที่ปิดข้อมูลส่วนตัวแล้ว: KBank ตัดเงิน, KBank เงินเข้า, KBank จ่ายบิล KTC, Shopee, Lazada และ PDF ของ KTC 1 ฉบับ เก็บไว้ที่ `expense-api/tests/fixtures/` (ไฟล์จริงต้อง gitignore)
- **T0.2** สร้างโครง `expense-api`:
  - Dockerfile, requirements (fastapi, sqlalchemy 2, alembic, psycopg, httpx, cryptography, google-genai, pytest)
  - เพิ่ม entry ใน compose (network `financial-ai`, healthcheck `/docs`, depends_on postgres/gmail/cc)
  - ไฟล์ `expense-api/.env.example`
- **T0.3** Alembic migration แรก: สร้างตารางทั้งหมดข้างต้น พร้อม seed accounts/categories และ view
- **T0.4** ทิ้งตาราง `public.transactions` เดิม (DROP)
- **T0.5** migration 0003: ตาราง `email_sources`, `raw_emails`, `raw_attachments` และคอลัมน์ `raw_email_id` พร้อม seed label (KBank = `financial-kasikorn`)

**Phase 1: Ingestion**
- **T1.1** gmail-service (commit ใน submodule):
  - เพิ่ม `page_token` ใน `/api/emails`
  - เพิ่ม `GET /api/messages/{id}` คืน text/html body และรายการ attachment
  - เพิ่ม `GET /api/messages/{id}/attachments/{att_id}`
- **T1.2** `expense-api/parsers/kbank.py`: ใช้ regex/HTML ไม่ใช้ LLM
  - ดึง: เลขที่รายการ, เวลา, ยอด, ผู้รับ, ช่องทาง
  - จำแนกประเภท: debit / credit / จ่ายบิลบัตร
  - เงินเข้าให้ข้ามไปก่อน
  - dedup_key = `kbank:<เลขที่รายการ>`
- **T1.3** `parsers/shopee.py`, `parsers/lazada.py`:
  - ดึง order_id, เวลา, ยอดสุทธิ, items, ค่าส่ง, ส่วนลด, coin
  - dedup_key = `shopee:<order_id>`
  - ใช้ LLM เป็น fallback เฉพาะ items เมื่อ template เปลี่ยน
- **T1.4** `sync/engine.py` + `POST /sync`:
  - วนตาม `email_sources` ที่ active: query ด้วย `label:<gmail_label>` (+ `from:<sender_filter>`) แล้วบันทึกลง `raw_emails` ก่อน จากนั้นค่อย parse จาก `raw_emails`
  - `POST /sync/reparse?source=` parse ใหม่จาก `raw_emails` ที่มีอยู่ ไม่เรียก Gmail
  - เรียก gmail-service ด้วย retry/backoff เพราะดึงจำนวนมากแล้ว Gmail ตอบ 500 เป็นช่วงๆ
  - ครั้งแรกดึงย้อนหลัง 3 เดือน ครั้งถัดไปดึงต่อจาก `sync_runs` ที่สำเร็จล่าสุด (ซ้อนช่วงไว้ 1 วันกันตกหล่น)
  - insert ด้วย `ON CONFLICT (dedup_key) DO NOTHING`
  - คืน counts เป็นจำนวนรายการ ใหม่/ซ้ำ/ข้าม/error
- **T1.5** KTC:
  - `PUT /settings/ktc-password` (เก็บแบบเข้ารหัส)
  - credit-card-statement-service เพิ่ม `POST /parse-ktc-pdf` (file + password, ใช้ pikepdf + pdfplumber) คืนข้อมูล statement และรายการ lines
  - sync หา PDF ของ KTC ในอีเมล แล้วสร้าง `statement` → `statement_line`
  - dedup_key = hash(posting date + merchant + amount + approval code)

**Phase 2: Reconciliation**
- **T2.1** `reconcile.py`:
  - จับคู่ payment/statement_line ที่ merchant เป็น Shopee/Lazada/AirPay/ShopeePay กับ `ecommerce_orders`
  - เงื่อนไข: ยอดตรงกันพอดี และเวลาอยู่ใน ±30 นาที (ตั้งค่าได้) สำหรับ KTC ใช้แค่วันที่ ±2 วัน
  - ถ้าเข้าเงื่อนไขหลายตัวจะไม่จับคู่ และติด flag ให้ผู้ใช้ตรวจ
  - ผูกลูกแล้วตรวจว่าผลรวมเท่ากับแม่
- **T2.2** ตรวจจับการจ่ายบิล KTC จาก KBank แล้วแปลงเป็น transfer KBank → KTC พร้อมผูกกับ statement

**Phase 3: Categorization**
- **T3.1** `categorize/memory.py`:
  - `normalize_merchant()` (ตัดเลขสาขา/รหัส/ช่องว่าง, ทำเป็นตัวพิมพ์เล็ก) แล้ว lookup แบบตรงกันพอดี
  - ผู้รับพร้อมเพย์ใช้ชื่อบัญชีเป็น key
- **T3.2** `categorize/ai.py`:
  - ใช้ google-genai พร้อม structured output คืนค่า `{category_id, confidence, reason}`
  - prompt มี taxonomy + keywords + merchant/items/ช่องทาง/memo
  - categorize ที่ระดับ leaf (item/line) และ batch หลายรายการต่อ 1 call
  - เลือกรุ่น Gemini ที่ยังรองรับอยู่ตอนลงมือทำ
- **T3.3** ถ้า confidence < `AI_CONFIDENCE_THRESHOLD` (เริ่มที่ 0.7) ให้เป็น Uncategorized
- **T3.4** Feedback loop: เมื่อ confirm หรือแก้หมวด ให้ upsert ลง `merchant_memory` ใน transaction เดียวกับการ confirm

**Phase 4: API + UI**
- **T4.1** API:
  - `GET /transactions?status=pending` (คืนเป็น tree)
  - `PATCH /transactions/{id}`
  - `POST /transactions/confirm` (`{ids}` หรือ `{all:true}`) ซึ่งจะ confirm ลูกทั้งต้นไม้ด้วย
  - `POST /transactions` (manual: amount, occurred_at, category, account บังคับ แล้ว confirmed ทันที)
  - `GET /accounts`, `GET /categories`
  - `GET/PUT /email-sources` (แก้ label ของแต่ละแหล่ง)
  - `GET /reports/summary?month=` (อ่านจาก view)
- **T4.2** `web/`:
  - หน้า Review Queue (tree, checkbox, แก้หมวดใน cell, ปุ่ม Refresh/Confirm Selected/Confirm All)
  - หน้า Manual Entry, Settings (KTC password, accounts, Gmail label ของแต่ละแหล่ง), Summary รายเดือน

**Phase 5: Hardening & Backlog**
- ย้ายรหัสผ่าน postgres และ secrets ออกจาก compose ไปไว้ใน `.env`
- ห้าม log password หรือเนื้อหา PDF
- ทำ `/security-review`
- อัปเดต CLAUDE.md/README ให้ตรงกับระบบใหม่
- Backlog:
  - refund/cancel order
  - Google Auth + multi-user
  - เงินเข้าและบัญชีรายรับอื่น
  - payment 1 ครั้งที่ครอบคลุมหลาย order
  - เปลี่ยน chatbot ใน langgraph ให้อ่านจาก ledger
  - สลับไปใช้ Vertex

## Reuse จากโค้ดเดิม

- โครง FastAPI, Dockerfile และ healthcheck: ทำตาม [credit-card-statement-service](credit-card-statement-service/)
- `get_gmail_service()` ใน [gmail-service/main.py](gmail-service/main.py): ใช้ต่อใน endpoint ใหม่
- แนวคิดการทำ structured output ด้วย Pydantic ใน [analyze_emails.py](langgraph/nodes/analyze_emails.py): ใช้เป็นต้นแบบ schema ของ classifier
- `pg_insert().on_conflict_*` ใน [save_transactions.py](langgraph/nodes/save_transactions.py): ใช้ pattern เดียวกันในการ dedup

## Verification

- **ทุก parser**: pytest กับ fixture จริง (`docker compose run --rm expense-api pytest`) ซึ่งเป็น test suite แรกของรีโปนี้
- **Sync**:
  - `docker compose up -d --build expense-api` แล้ว `curl -X POST localhost:8002/sync`
  - ตรวจ counts และตรวจว่ารันซ้ำแล้วได้รายการใหม่ = 0 (dedup ทำงาน)
- **Reconcile**: query ว่าต้นไม้ payment → order → items มีผลรวมของลูกเท่ากับแม่ และ transfer ไม่ปรากฏใน `v_reportable_expenses`
- **Categorize**: confirm รายการที่หมวดผิดหลังแก้แล้ว sync ร้านเดียวกันอีกครั้ง ต้องได้ `category_source='memory'`
- **End-to-end**: เปิด `localhost:3000` → กด Refresh → แก้หมวด → Confirm → ตัวเลขในหน้า Summary ต้องตรงกับ SQL ที่รันบน view

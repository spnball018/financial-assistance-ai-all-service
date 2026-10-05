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
  - seed หมวดหลัก 7 หมวดพร้อมหมวดย่อยตาม PRD และเพิ่ม "บริจาค/ทำบุญ" ใน migration 0004 (ผู้ใช้บริจาคทุกเดือน) วาง Uncategorized ไว้ท้ายสุดเสมอ
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
| T0.2 โครง expense-api | เสร็จ (container ขึ้นได้ใน docker compose แล้ว ตอนพัฒนาใช้ venv) |
| T0.3, T0.4 migration 0001, 0002 | เสร็จ รันกับ DB จริงแล้ว |
| T0.5 migration 0003 (raw_emails, email_sources) | เสร็จ รันกับ DB จริงแล้ว |
| T1.1 gmail-service | เสร็จและทดสอบแล้ว (ยกเว้น endpoint ไฟล์แนบ) |
| T1.2 parser KBank | เสร็จ ทดสอบกับอีเมลจริง 419 ฉบับ |
| API: `/accounts`, `/categories`, `/email-sources` | เสร็จ ใช้กับ DB จริงแล้ว |
| T1.4 sync engine | เสร็จ ทดสอบกับ DB จริงแล้ว (ดู [sync-engine.md](sync-engine.md)) |
| T3.1 Merchant Memory | เสร็จ (`categorize/normalize.py`, `categorize/memory.py`) |
| T3.2 AI categorizer | adapter `gemini` / `vertex` / `none` เลือกด้วย env `CATEGORIZER` (registry `ADAPTERS` ใน `categorize/ai.py`) ลองกับ Google AI Studio (free tier) แล้ว: 152 รายการ ได้หมวด 79, Uncategorized 73 ใช้เวลา 27 วินาที |
| T3.3 threshold → Uncategorized | เสร็จ |
| T4.1 API | เสร็จ (`ledger/transactions.py`, `ledger/reports.py`, `app/routers/transactions.py`) ทดสอบกับ DB ทดสอบ และลองอ่านกับ DB จริงแล้ว |
| T4.2 web | หน้า ตรวจรายการ (Review Queue) และ สรุปรายเดือน เสร็จ (`web/`, React 19 + Vite 8, dev server พอร์ต 3000 proxy `/api` → expense-api) ยังไม่มีหน้า Manual Entry / Settings และยังไม่ได้ใส่ใน docker compose |
| T6.1 บัญชีของตัวเอง | เสร็จ: migration 0005 (12 บัญชี รวมเป๋าตัง), `GET/POST/PATCH /accounts`, หน้า ตั้งค่า → บัญชีของฉัน · ล้างข้อมูลรายการทั้งหมดใน DB จริงแล้ว (ผู้ใช้ขอ 2026-10-05) จะ sync ใหม่หลัง T6.2 |
| T6.2 + T6.3 กฎต่อผู้รับและ UI | เสร็จ: migration 0006, จับคู่ปลายทางกับ `masked_ref`, กฎ category/transfer/ask (กฎ transfer จำต่อ ผู้รับ+เลขท้าย 4 ตัว), dropdown มีกลุ่ม "โอนเข้าบัญชีตัวเอง", ติ๊ก "ไม่ต้องจำผู้รับนี้", สรุปรายเดือนแสดงยอดโอน · sync ใหม่ใน DB จริงแล้ว: 152 รายการ (AI 87, ไม่แน่ใจ 65, ยังไม่มีเลขท้ายบัญชีจึงจับการโอนอัตโนมัติไม่ได้) |
| T6.4 ยอดยกมาและยอดคงเหลือ | เสร็จ: migration 0007 (`kind=opening`, view `v_account_postings`), `GET /accounts/balances`, `PUT/DELETE /accounts/{id}/opening-balance`, เทียบ "ยอดถอนได้" ในอีเมล KBank ล่าสุด, หน้า ยอดคงเหลือ · view แยกขาบัญชีแบบบัญชีคู่โดยไม่เปลี่ยนวิธีเก็บข้อมูล (ตามที่ตกลง) |
| T3.4 feedback loop | เสร็จ: confirm และการแก้หมวดรายการที่ confirm แล้วจะบันทึก memory และใช้กับรายการ pending ร้านเดียวกันทันที |
| T0.1 fixture Shopee / Lazada | ยังไม่มี (รอชื่อ label) |
| KTC (T1.5) | แยกไป [ktc-track.md](ktc-track.md) label: `financial-ktc-[card]` (ชื่อจริง) |

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
- **T1.4** `sync/engine.py` + `POST /sync`: รายละเอียดการออกแบบและสถานะอยู่ใน [sync-engine.md](sync-engine.md)
  - parser ของแต่ละแหล่งอยู่ใน `sources/<source>.py` ตาม contract ใน `sources/base.py` (engine เป็นคนเดียวที่เขียน DB)
  - วนตาม `email_sources` ที่ active: query ด้วย `label:<gmail_label>` (+ `from:<sender_filter>`) แล้วบันทึกลง `raw_emails` ก่อน จากนั้นค่อย parse จาก `raw_emails`
  - `POST /sync/reparse?source=` parse ใหม่จาก `raw_emails` ที่มีอยู่ ไม่เรียก Gmail
  - เรียก gmail-service ด้วย retry/backoff เพราะดึงจำนวนมากแล้ว Gmail ตอบ 500 เป็นช่วงๆ
  - ครั้งแรกดึงย้อนหลัง 3 เดือน ครั้งถัดไปดึงต่อจาก `sync_runs` ที่สำเร็จล่าสุด (ซ้อนช่วงไว้ 1 วันกันตกหล่น)
  - insert ด้วย `ON CONFLICT (dedup_key) DO NOTHING`
  - คืน counts เป็นจำนวนรายการ ใหม่/ซ้ำ/ข้าม/error
- **T1.5** KTC: **แยกเป็น KTC track ให้ agent อีกตัวทำ** ดู [ktc-track.md](ktc-track.md) (ขอบเขตไฟล์, contract, กติกาต้นไม้)
  - main track ทำแค่ให้ sync engine เรียก `sources/ktc.py` ตาม contract ใน `expense-api/sources/base.py`

**Phase 2: Reconciliation**
- **T2.1** `reconcile.py`:
  - จับคู่ payment/statement_line ที่ merchant เป็น Shopee/Lazada/AirPay/ShopeePay กับ `ecommerce_orders`
  - เงื่อนไข: ยอดตรงกันพอดี และเวลาอยู่ใน ±30 นาที (ตั้งค่าได้) สำหรับ KTC ใช้แค่วันที่ ±2 วัน
  - ถ้าเข้าเงื่อนไขหลายตัวจะไม่จับคู่ และติด flag ให้ผู้ใช้ตรวจ
  - ผูกลูกแล้วตรวจว่าผลรวมเท่ากับแม่
- **T2.2** ตรวจจับการจ่ายบิล KTC จาก KBank แล้วแปลงเป็น transfer KBank → KTC พร้อมผูกกับ statement

**Phase 3: Categorization**
- **T3 สรุปการทำจริง**: `POST /categorize` และเรียกอัตโนมัติหลัง `POST /sync` จัดหมวดเฉพาะ leaf ที่ pending ส่วนรุ่น Gemini ตั้งผ่าน env `GEMINI_MODEL` ค่าเริ่มต้น `gemini-3.5-flash-lite` (ตรวจ 2026-10-05: `gemini-2.5-flash` ตอบ 404 สำหรับผู้ใช้ใหม่ ส่วน `gemini-3.8-flash` และ `gemini-3.5-flash` ตอบ 503 high demand บน free tier) adapter retry เมื่อเจอ 503/429 DB จริงมีร้าน/ผู้รับ 82 รายจาก 152 รายการ
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

**Phase 4: API + UI** (spec ของ API ทุก service อยู่ใน [api-spec.md](api-spec.md))
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

**Phase 6: บัญชีของตัวเอง, การโอน, ยอดเริ่มต้น, ใบเสร็จ** (เพิ่ม 2026-10-05 หลังผู้ใช้ลองหน้า Review)

ปัญหา: บางรายการจาก KBank ไม่ใช่รายจ่าย แต่เป็นการย้ายเงินเข้าบัญชีของตัวเอง เช่น เติม TrueMoney หรือโอนพร้อมเพย์เข้าชื่อตัวเอง ถ้านับเป็นรายจ่าย พอดึงรายการของบัญชีปลายทางมาจะนับซ้ำ และ Merchant Memory ไม่ควรจำผู้รับบางรายเป็นหมวด

ข้อตัดสินใจของผู้ใช้
- **การโอนเข้าบัญชีตัวเองไม่นับเป็นรายจ่าย** (เหมือนการโอนระหว่างบัญชีธนาคารปกติ)
- Lazada/Shopee map มาจากอีเมล (T1.3 เดิม) ส่วน TrueMoney บันทึกเอง
- ต้องมีรายการยอดเริ่มต้นของแต่ละบัญชี

**บัญชีที่จะเพิ่ม** (ชื่อในวงเล็บคือหน้าที่ตามที่ผู้ใช้บอก)

| บัญชี | ประเภท | หน้าที่ |
|---|---|---|
| KBank (มีอยู่แล้ว id 1) | bank | บัญชีจ่ายประจำ ที่รับอีเมลรายจ่าย |
| KBank ออมทรัพย์ฉุกเฉิน | bank | เงินสำรอง ใช้ต่อเมื่อ SCB รายรับหมด |
| SCB ค่าเช่า | bank | รับค่าเช่าบ้าน ตอนนี้ใช้เป็นรายได้ |
| SCB รายรับ | bank | เงินเดือน (ตอนนี้ว่างงาน) และงาน freelance |
| Bangkok Bank ลงทุน | bank | เงินพักสำหรับลงทุน |
| BAY ออมทรัพย์ | bank | ออมทรัพย์ |
| บัตรเครดิต KTC (มีอยู่แล้ว id 2) | credit_card | |
| Lazada | ewallet | |
| Shopee | ewallet | |
| TrueMoney | ewallet | บันทึกรายการเอง |
| Marketplace อื่นๆ | ewallet | |

- **T6.1 จัดการบัญชี**: migration seed บัญชีข้างบน, `accounts.masked_ref` เก็บเลขท้ายบัญชี/wallet ID สำหรับจับคู่การโอน, `GET/POST/PATCH /accounts` และหน้า Settings → บัญชี
- **T6.2 กฎต่อผู้รับ (ขยาย Merchant Memory)**: `merchant_memory` เพิ่ม `action` = `category` (แบบเดิม) | `transfer` (+ `counter_account_id`) | `ask` (ไม่จำ ให้ผู้ใช้ตรวจเองทุกครั้ง AI ยังเสนอได้)
  - การจับคู่ "โอนเข้าบัญชีตัวเอง": ใช้ **เลขบัญชีปลายทาง** จากอีเมล KBank (`to_account`) เทียบกับ `accounts.masked_ref` ก่อน เพราะชื่อผู้รับเป็นชื่อผู้ใช้เองในหลายบัญชี ชื่ออย่างเดียวจึงบอกไม่ได้ว่าเป็นบัญชีไหน ถ้าไม่มีเลขบัญชี ค่อยใช้กฎตาม `merchant_key` (เช่น TrueMoney)
  - รายการที่เป็น transfer: `kind=transfer`, `counter_account_id` = บัญชีปลายทาง, ไม่มีหมวด, ไม่อยู่ใน `v_reportable_expenses` และ AI ข้าม
- **T6.3 UI**: dropdown หมวดมีกลุ่ม "โอนเข้าบัญชีตัวเอง →" (รายชื่อบัญชี) และตัวเลือก "ไม่ต้องจำผู้รับนี้"; confirm แล้วจำกฎตามที่เลือก
- **T6.4 ยอดเริ่มต้น**: `node_type = opening_balance` (`kind` ใหม่ `opening`) 1 แถวต่อบัญชี ณ วันที่เริ่มใช้ระบบ ไม่อยู่ในรายงานรายจ่าย ใช้คำนวณยอดคงเหลือ (`GET /accounts/balances`: ยอดเริ่มต้น + รายรับ − รายจ่าย ± transfer)
- **T6.5 แยกรายการจากใบเสร็จ**: แนบรูปใบเสร็จกับรายการ KBank (เช่น ซูเปอร์มาร์เก็ต) → Gemini อ่านรูป (vision + structured output) → สร้างลูก `receipt_item` ทีละรายการพร้อมหมวด และ `adjustment` สำหรับส่วนลด/ส่วนต่าง ให้ผลรวมของลูกเท่ากับยอดจ่าย เก็บรูปต้นฉบับใน DB (แบบ `raw_attachments`) ผู้ใช้ตรวจก่อน confirm
  - ทำงานบนโครงแม่/ลูกที่มีอยู่แล้ว รายงานนับเฉพาะลูก (leaf) จึงไม่นับซ้ำ
- **T6.6 รายรับ**: หมวดรายรับ (ค่าเช่า, freelance, เงินเดือน, ดอกเบี้ย) และแหล่งข้อมูลเงินเข้า (ต้องเช็กว่า SCB ส่งอีเมลแจ้งเงินเข้าไหม) รายงานรายรับ/รายจ่ายรายเดือน
- **T6.7 จับคู่การโอน**: เมื่อมีข้อมูลฝั่งบัญชีปลายทาง (เช่น Shopee/Lazada wallet) ผูก "โอนออก" กับ "เงินเข้า" ด้วย `ref_transaction_id` ตามยอดและเวลา เหมือน T2.2

ลำดับ: T6.1 → T6.2 → T6.3 → T6.4 → T6.5 → T6.6 → T6.7 (T6.6 ขึ้นกับว่ามีอีเมลเงินเข้าไหม)

**Phase 5: Hardening & Backlog**
- ย้ายรหัสผ่าน postgres และ secrets ออกจาก compose ไปไว้ใน `.env`
- ห้าม log password หรือเนื้อหา PDF
- ทำ `/security-review`
- อัปเดต CLAUDE.md/README ให้ตรงกับระบบใหม่
- Backlog:
  - refund/cancel order
  - Google Auth + multi-user
  - ~~เงินเข้าและบัญชีรายรับอื่น~~ ย้ายไป T6.6
  - **บัญชีกองทุน** (ผู้ใช้ขอ 2026-10-05): บัญชีประเภท `investment` จดหน่วยลงทุน, NAV และกำไร/ขาดทุน (ทั้งที่รับรู้แล้วและยังไม่รับรู้) แยกจากรายงานรายจ่าย
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

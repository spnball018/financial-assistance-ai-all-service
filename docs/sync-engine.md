# T1.4: Sync engine (main track)

ดึงอีเมลจาก Gmail มาเป็นรายการ `pending` ใน `ledger.transactions` เอกสารนี้ขยายความ T1.4 ใน [expense-tracker-plan.md](expense-tracker-plan.md)

## สถานะ

| # | ส่วน | สถานะ | สิ่งที่มีอยู่แล้ว |
|---|---|---|---|
| 1 | ดึงอีเมล (client) | เสร็จ | ฝั่ง gmail-service มี endpoint ครบและทดสอบแล้ว (T1.1) |
| 2 | Engine | เสร็จ | ตาราง `raw_emails`, `sync_runs`, `transactions` มีใน migration แต่ยังไม่เคยรันจริง |
| 3 | KBank ต่อเข้า contract | เสร็จ | มี parser `parsers/kbank.py` ที่ทดสอบแล้ว และ contract `sources/base.py` แต่ยังไม่ได้ต่อกัน |
| 4 | API `/sync` | เสร็จ | `/sync`, `/sync/reparse`, `/sync/runs` |
| 5 | เทสต์ | เสร็จ | มีแค่เทสต์ของ parser |
| 6 | Verification | เสร็จ | ทั้ง DB ทดสอบและ DB จริง `financial` |

## การแบ่ง adapter

แบ่งเป็น 4 ชั้น แต่ละชั้นรู้จักแค่ชั้นที่อยู่ติดกัน

```
app/routers/sync.py          ← HTTP: POST /sync, /sync/reparse, GET /sync/runs
        │
sync/engine.py               ← ลำดับการทำงานอย่างเดียว ไม่มี SQL ไม่มี HTTP
   │            │            │
   ▼            ▼            ▼
MailClient   SourceParser   Repo
(port)       (port)         (sync/repo.py: ตัวเดียวที่เขียน DB)
   │            │
   ▼            ▼
GmailServiceClient     sources/kbank.py ─→ parsers/kbank.py
FakeMailClient (test)  sources/ktc.py   ─→ credit-card-statement-service  (KTC track)
                       sources/shopee.py, lazada.py ─→ parsers/marketplace.py (orders → ecommerce_orders)
```

- **`MailClient`** (`sync/gmail_client.py`): Protocol ที่มี `list_ids(query, page_token)`, `get_message(id)` และ `get_attachment(id, att_id)`
  - `GmailServiceClient` เรียก gmail-service ด้วย httpx มี retry/backoff และดึงหน้าละ 50 ฉบับ (Gmail ตอบ 500 เป็นช่วงๆ เมื่อดึงจำนวนมาก)
  - `FakeMailClient` สำหรับเทสต์ อ่านจาก fixture JSON
  - ถ้าเปลี่ยนแหล่งอีเมล (IMAP, Gmail API ตรง) แก้แค่ชั้นนี้
- **`SourceParser`**: ไฟล์ละแหล่งใน `sources/` ตาม `sources/base.py`
  - ผลการ parse เป็นต้นไม้ของรายการ (`nodes`) หรือ order ของ marketplace (`orders`) อย่างหลัง repo เก็บใน `ecommerce_orders` แล้ว `reconcile/marketplace.py` เอาไปแยกรายการจ่ายภายหลัง
  - `sources/__init__.py` มี registry `{"kbank_email": kbank, "ktc_pdf": ktc, ...}` เพิ่มแหล่งใหม่โดยไม่ต้องแก้ engine
  - `parsers/` แกะข้อความล้วนๆ ส่วน `sources/` แปลงเป็น `TxnNode` ตามกติกาบัญชี (node_type, kind, dedup_key) กติกาบัญชีอยู่ที่เดียว และเทสต์ parser ไม่ต้องรู้จัก ledger
  - parser ห้ามแตะ DB
- **`Repo`** (`sync/repo.py`): SQL ทั้งหมดของ sync
  - upsert `raw_emails` / `raw_attachments`
  - insert ต้นไม้ `transactions` แบบ `ON CONFLICT (dedup_key) DO NOTHING` ใส่ `parent_id`, `account_id`, `source`, `raw_email_id`
  - ติด `needs_review` เมื่อยอดรวมลูกไม่เท่ากับแม่
  - เปิด/ปิด `sync_runs`
- **Secrets** (`app/secrets.py`): เข้ารหัส/ถอดรหัส Fernet (`SECRET_KEY`), `get_setting` / `set_setting` บน `ledger.settings`
  - engine ใช้ส่งรหัส PDF ให้ KTC ผ่าน `ctx.get_setting`
  - งาน K3 ของ KTC track ใช้ไฟล์นี้ ไม่เขียนการเข้ารหัสซ้ำ (ต้องทำก่อน KTC เริ่ม K3)

## Flow ของ engine

1. สร้าง `sync_runs` สถานะ `running` ถ้ามีรอบที่ `running` ค้างอยู่ ให้ตอบ 409
2. คำนวณช่วงเวลา: ครั้งแรกย้อน 3 เดือน ครั้งต่อไปเริ่มจาก `range_to` ของรอบที่สำเร็จล่าสุดลบ 1 วัน
3. วนแหล่งที่ `is_active` ใน `email_sources` query ด้วย `label:<gmail_label>` (+ `from:<sender_filter>`) + `after:`/`before:`
4. อีเมลที่ยังไม่มีใน `raw_emails` ให้ดึงเต็มแล้วบันทึก ไฟล์แนบดึงตอน parser ขอ (`ctx.get_attachment`) แล้ว cache ใน `raw_attachments`
5. parse อีเมลที่ `parse_status` เป็น `pending` หรือ `failed` ด้วย parser ของแหล่งนั้น
6. บันทึกต้นไม้ผ่าน Repo และอัปเดต `parse_status`, `parser_version`, `parse_error`
7. ปิด `sync_runs` เป็น `success`/`failed` พร้อม counts ต่อแหล่ง (ใหม่, ซ้ำ, ข้าม, error)

แต่ละอีเมลอยู่ใน DB transaction ของตัวเอง ฉบับที่พังไม่ทำให้ทั้งรอบล้ม

## กติกา KBank (`sources/kbank.py`)

- bill_payment, transfer, promptpay, donation → node `payment`, `kind=expense`, `merchant_raw` จาก parser
- Failure, Top-up → `skipped` (Top-up ยังไม่ตัดสินใจ reparse ทีหลังได้)
- จ่ายบิล KTC (`KRUNGTHAI CARD PUBLIC COMPANY LIMITED`) → เป็น expense ไปก่อน ใส่ `raw.ktc_bill_payment=true` ให้ T2.2 แปลงเป็น transfer
- ค่าธรรมเนียม: **ยังไม่ตัดสินใจ** (ข้อเสนอ: รวมเข้ายอด เพราะเกือบทั้งหมดเป็น 0.00)

## API

- `POST /sync` คืน counts ต่อแหล่ง
- `POST /sync/reparse?source=` parse ใหม่จาก `raw_emails` ไม่เรียก Gmail
- `GET /sync/runs` ประวัติการ sync

## ลำดับการทำ

| ขั้น | งาน | ทดสอบได้ตอนนี้ไหม |
|---|---|---|
| 1 | `app/secrets.py` + แก้ ktc-track.md ข้อ K3 | **เสร็จ** (เทสต์ 3 ข้อ) |
| 2 | `sources/__init__.py` (registry) + `sources/kbank.py` | **เสร็จ** |
| 3 | `sync/gmail_client.py` (Protocol, ตัวจริง, ตัวปลอม) | **เสร็จ** |
| 4 | `sync/repo.py` + `sync/engine.py` | **เสร็จ** |
| 5 | `app/routers/sync.py` | **เสร็จ** |
| 6 | เทสต์ engine + `curl -X POST /sync` สองครั้ง | **เสร็จบน DB ทดสอบ** |

เทสต์ที่ต้องใช้ Postgres ให้ skip เองเมื่อไม่มี `DATABASE_URL`

## เทสต์

- เทสต์ที่ใช้ DB ต้องตั้ง `TEST_DATABASE_URL` เช่น `postgresql://postgres:postgres@localhost:5432/financial_test` ชื่อ DB ต้องมีคำว่า `test` ไม่งั้นเทสต์ปฏิเสธ เพราะเทสต์ truncate ตาราง ledger conftest รัน `alembic upgrade head` ให้เอง ถ้าไม่ตั้งค่า เทสต์กลุ่มนี้จะ skip
- สร้าง DB ครั้งแรก: `CREATE DATABASE financial_test;`

## ผล verification (2026-10-05, Gmail จริง → `financial_test`)

- `POST /sync` รอบแรก: ดึง 152 ฉบับ (ย้อนหลัง 90 วัน) ได้ 152 รายการ `pending` ใช้เวลาราว 3 นาที (gmail-service ดึงทีละฉบับ) ไม่มี failed
- รอบสอง: ดึง 0, ใหม่ 0 ใช้เวลา 0.4 วินาที และ `/sync/reparse` ได้ duplicate 152 ใหม่ 0
- ทุกแถวมี `raw_email_id` แยกตามช่องทาง: bill_payment 115, promptpay 18, transfer 17, donation 2
- ไม่พบบิล KTC ในช่วง 90 วัน (อีเมลบิล KTC ที่เจอก่อนหน้าเก่ากว่านั้น) การตั้ง flag `ktc_bill_payment` ทดสอบด้วยอีเมลที่ดัดแปลงในเทสต์แทน

**DB จริง `financial`** (Postgres ใน docker, gmail-service และ expense-api รันจาก venv): รอบแรกได้ 152 รายการ `pending` (ราว 2 นาที) รอบสองได้ใหม่ 0 ทุกแถวมี `raw_email_id` และ `merchant_raw` ตาราง `public.transactions` เดิมถูก drop แล้ว

**ปัญหาที่เจอ**: ถ้ารัน `gmail-auth`/`gmail-service` ใน docker แล้ว `token.json` จะเป็นของ root เมื่อ token หมดอายุ gmail-service ที่รันจาก venv จะเขียนทับไม่ได้และตอบ 500 (`Permission denied: 'token.json'`) แก้ด้วย `sudo chown $USER token.json`

## ตัดสินใจแล้ว (ใช้ค่าที่แนะนำ)

- ค่าธรรมเนียม: รวมเข้ายอดรายจ่าย
- Top-up: ข้ามไปก่อน (`skipped`) เก็บอีเมลไว้ใน `raw_emails` ใช้ `/sync/reparse` ภายหลังได้

## ที่ยังเปิดอยู่

- ผู้ใช้ยืนยันค่าที่เลือกไว้ข้างบน (ค่าธรรมเนียม, Top-up)
- sync รอบแรกช้า (ดึงทีละฉบับ) อาจต้องทำเป็น background job พร้อม progress ใน UI (T4)

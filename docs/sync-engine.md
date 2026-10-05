# T1.4: Sync engine (main track)

ดึงอีเมลจาก Gmail มาเป็นรายการ `pending` ใน `ledger.transactions` เอกสารนี้ขยายความ T1.4 ใน [expense-tracker-plan.md](expense-tracker-plan.md)

## สถานะ

| # | ส่วน | สถานะ | สิ่งที่มีอยู่แล้ว |
|---|---|---|---|
| 1 | ดึงอีเมล (client) | ยังไม่ทำ | ฝั่ง gmail-service มี endpoint ครบและทดสอบแล้ว (T1.1) |
| 2 | Engine | ยังไม่ทำ | ตาราง `raw_emails`, `sync_runs`, `transactions` มีใน migration แต่ยังไม่เคยรันจริง |
| 3 | KBank ต่อเข้า contract | ครึ่งหนึ่ง | มี parser `parsers/kbank.py` ที่ทดสอบแล้ว และ contract `sources/base.py` แต่ยังไม่ได้ต่อกัน |
| 4 | API `/sync` | ยังไม่ทำ | |
| 5 | เทสต์ | ยังไม่ทำ | มีแค่เทสต์ของ parser |
| 6 | Verification | ยังไม่ทำ | รอ migration ผ่าน |

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
                       sources/shopee.py, lazada.py (T1.3)
```

- **`MailClient`** (`sync/gmail_client.py`): Protocol ที่มี `list_ids(query, page_token)`, `get_message(id)` และ `get_attachment(id, att_id)`
  - `GmailServiceClient` เรียก gmail-service ด้วย httpx มี retry/backoff และดึงหน้าละ 50 ฉบับ (Gmail ตอบ 500 เป็นช่วงๆ เมื่อดึงจำนวนมาก)
  - `FakeMailClient` สำหรับเทสต์ อ่านจาก fixture JSON
  - ถ้าเปลี่ยนแหล่งอีเมล (IMAP, Gmail API ตรง) แก้แค่ชั้นนี้
- **`SourceParser`**: ไฟล์ละแหล่งใน `sources/` ตาม `sources/base.py`
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
| 2 | `sources/__init__.py` (registry) + `sources/kbank.py` | ได้ (fixture KBank) |
| 3 | `sync/gmail_client.py` (Protocol, ตัวจริง, ตัวปลอม) | ได้ (gmail-service บน venv) |
| 4 | `sync/repo.py` + `sync/engine.py` | ต้องมี Postgres |
| 5 | `app/routers/sync.py` | ต้องมี Postgres |
| 6 | เทสต์ engine (rerun ได้ใหม่ 0, ต้นไม้ถูก) + `curl -X POST localhost:8002/sync` สองครั้ง | ต้องมี Postgres |

เทสต์ที่ต้องใช้ Postgres ให้ skip เองเมื่อไม่มี `DATABASE_URL`

## รอคำตอบจากผู้ใช้

- ผลการรัน migration 0001–0003
- ค่าธรรมเนียม: รวมเข้ายอด หรือแยกเป็น `adjustment`
- Top-up: ข้ามไปก่อนได้ไหม

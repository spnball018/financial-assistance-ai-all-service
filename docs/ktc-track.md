# KTC track: ดึง statement บัตรเครดิต KTC จาก PDF ในอีเมล

เอกสารนี้แยกงาน KTC ออกจากแผนหลัก ([expense-tracker-plan.md](expense-tracker-plan.md)) เพื่อให้ agent อีกตัวทำคู่ขนานได้โดยไม่แก้ไฟล์ชนกัน อ่านแผนหลักส่วน Data model และกฎการผูกรายการก่อนเริ่ม

## เป้าหมาย

เมื่อ sync เจออีเมลจาก KTC ที่มี PDF statement แนบมา ต้องได้ต้นไม้ในตาราง `ledger.transactions` แบบนี้
- node `statement` 1 แถว บน account KTC
- ลูกเป็น `statement_line` ทีละรายการ (`kind=expense`)

ทุกแถวมี `status=pending` เพื่อให้ผู้ใช้ตรวจก่อน confirm

## โครงสร้าง expense-api

```
expense-api/
├── app/                 # ชั้น HTTP
│   ├── main.py          # main track (include_router(ktc.router) ไว้แล้ว)
│   ├── db.py            # main track: get_engine()
│   ├── secrets.py       # main track: Fernet + get/set ledger.settings  ← K3 ใช้ตัวนี้
│   └── routers/
│       ├── ktc.py       # KTC track
│       └── sync.py      # main track (กำลังจะสร้าง)
├── sync/                # main track: engine, repo (ตัวเดียวที่เขียน DB), gmail client
├── sources/             # adapter ต่อแหล่ง: RawEmail → TxnNode ไม่แตะ DB
│   ├── base.py          # main track: contract
│   ├── kbank.py         # main track
│   └── ktc.py           # KTC track
├── parsers/             # แกะข้อความล้วนๆ ไม่รู้จัก ledger (main track: kbank.py)
└── tests/
```

- **ทิศทาง import**: `app/routers` → `sync` → `sources` → `parsers` ชั้นล่างห้าม import ชั้นบน ข้อยกเว้นเดียวคือ `app/routers/ktc.py` ใช้ `app.secrets` ได้
- `sources/ktc.py` ห้าม import `app.*` หรือ `sync.*` ให้ใช้แค่ `sources.base` กับ `ParseContext` ที่ engine ส่งมา
- **อย่าย้ายโฟลเดอร์**: ตอนนี้ `sync/`, `sources/` และ `parsers/` อยู่นอก `app/` อาจจัดใหม่หลัง merge KTC track ซึ่ง main track จะเป็นคนทำ
- ถ้าเป็น logic แกะ PDF/ตารางที่ไม่เกี่ยวกับ ledger ให้วางใน `credit-card-statement-service/` ไม่ใช่ `parsers/`

## ขอบเขตไฟล์ (สำคัญ: กันชนกับ main track)

**แก้ได้ (เป็นของ KTC track)**
- `credit-card-statement-service/` ทั้งโฟลเดอร์
- `expense-api/sources/ktc.py` (มี stub ให้แล้ว)
- `expense-api/app/routers/ktc.py` (มี stub และ `include_router` ใน `app/main.py` ไว้แล้ว)
- `expense-api/tests/test_ktc_*.py` และ `expense-api/tests/fixtures/ktc_*`
- ไฟล์ใหม่ที่ขึ้นต้นด้วย `ktc` ใต้ `expense-api/` เช่น `expense-api/sources/ktc_pdf_layout.py`
- `docs/ktc-track.md` (อัปเดตสถานะในไฟล์นี้)

**ห้ามแก้ (เป็นของ main track)** ถ้าจำเป็นต้องเปลี่ยน ให้หยุดแล้วถามผู้ใช้
- `expense-api/sources/base.py` (contract)
- `expense-api/app/main.py`, `expense-api/app/db.py`, `expense-api/app/secrets.py`, `expense-api/sync/`, `expense-api/sources/kbank.py`, `expense-api/parsers/`
- `expense-api/alembic/` ห้ามเพิ่ม migration เพราะ revision chain จะแตกเป็นหลาย head schema ที่ต้องใช้มีครบแล้ว (ดูหัวข้อ Schema ที่มีอยู่)
- `docker-compose.yml`: ถ้าต้องเพิ่ม env หรือ depends_on ให้แจ้งผู้ใช้
- `docs/expense-tracker-plan.md`, `gmail-service/`

## Contract กับ sync engine

ดูรายละเอียดใน [expense-api/sources/base.py](../expense-api/sources/base.py) โดยสรุปคือ
- `sources/ktc.py` ต้องมี `SOURCE = "ktc_pdf"`, `PARSER_VERSION` และ `parse(email: RawEmail, ctx: ParseContext) -> ParseResult`
- `parse()` ห้ามแตะ database และห้ามเรียก Gmail เอง
  - ดึงไฟล์แนบผ่าน `ctx.get_attachment(attachment_id)`
  - อ่านรหัสผ่าน PDF ผ่าน `ctx.get_setting("ktc_pdf_password")`
- คืน `TxnNode` ที่เป็น root (`statement`) พร้อม `children` (`statement_line`)
- อีเมลที่ไม่มี PDF statement (เช่น โฆษณา หรือแจ้งเตือนอื่น) ให้คืน `status="skipped"`
- sync engine ฝั่ง main track ยังไม่ได้เขียน ระหว่างนี้ให้ทดสอบ `parse()` ตรงๆ ด้วย `RawEmail`/`ParseContext` ที่สร้างในเทสต์

## ก่อนเริ่มและระหว่างทำ

- main track ยังทำงานบน `main` อยู่ ก่อนเริ่มแต่ละงานให้ `git merge main` เข้า `ktc-track` เพื่อได้ contract และเอกสารล่าสุด (เช่น `docs/sync-engine.md`)
- ถ้า merge แล้วชนในไฟล์ของ main track ให้ใช้ฝั่ง `main` เสมอ

## งาน

**K1. `credit-card-statement-service`: `POST /parse-ktc-pdf`**
- รับ multipart: `file` (PDF) และ `password`
- ใช้ `pikepdf` ปลดล็อก และ `pdfplumber` อ่านตาราง ทำในหน่วยความจำ ห้ามเขียนไฟล์ที่ปลดล็อกแล้วลงดิสก์
- คืน JSON
  - ข้อมูล statement: statement date, due date, ยอดรวมใหม่, เลขบัตรแบบปิดบางส่วน
  - `lines`: posting date, transaction date (ถ้ามี), description, amount, approval/reference code (ถ้ามี) และประเภท (`purchase|payment|refund|fee|interest|other`)
- รหัสผ่านผิดให้คืน 401 และ PDF อ่านไม่ได้ให้คืน 422
- ห้าม log รหัสผ่านหรือเนื้อหา PDF
- เพิ่ม `pikepdf` และ `pdfplumber` ใน `credit-card-statement-service/requirements.txt` และห้ามทำ endpoint `/upload-statement/` เดิมพัง

**K2. `expense-api/sources/ktc.py`**
- หา attachment ที่เป็น PDF แล้วเรียก `POST {CREDIT_CARD_STATEMENT_SERVICE_URL}/parse-ktc-pdf` ด้วย httpx ซึ่ง env นี้มีใน compose ของ expense-api แล้ว
- ถ้ายังไม่ได้ตั้งรหัสผ่าน ให้คืน `status="failed"` พร้อม error ว่ายังไม่ได้ตั้งรหัสผ่าน KTC
- แปลงผลลัพธ์เป็นต้นไม้ตามกติกาในหัวข้อถัดไป

**K3. `expense-api/app/routers/ktc.py`: `PUT /settings/ktc-password`**
- body `{ "password": "..." }` แล้วเรียก `app.secrets.set_setting("ktc_pdf_password", password)`
- **ห้ามเขียนการเข้ารหัสเอง**: `app/secrets.py` (ของ main track) มี Fernet ด้วย env `SECRET_KEY` อยู่แล้ว ได้แก่ `set_setting`, `get_setting` และ `setting_updated_at`
- ห้ามมี endpoint ที่คืนรหัสผ่านออกไป ถ้า UI ต้องรู้สถานะ ทำได้แค่ `GET /settings/ktc-password/status` คืน `{ "is_set": bool, "updated_at": ... }` จาก `setting_updated_at`
- ถ้า `SECRET_KEY` ไม่ได้ตั้ง `app.secrets` จะ raise `RuntimeError` ให้แปลงเป็น HTTP 500 ที่ไม่มีค่ารหัสผ่านในข้อความ

**K4. เทสต์และ fixture**
- เก็บ PDF จริงไว้ที่ `expense-api/tests/fixtures/ktc_*.pdf` (gitignore อยู่แล้ว) รหัสผ่านให้อ่านจาก env ในเทสต์ ห้าม hardcode
- ถ้าจะดึง PDF จาก Gmail ต้องขออนุญาตผู้ใช้ก่อน เพราะเป็นข้อมูลการเงินส่วนตัว
- เทสต์ที่ใช้ fixture จริงต้อง `pytest.skip` เองเมื่อไม่มีไฟล์ (ดูตัวอย่างใน `tests/test_kbank_parser.py`)
- เทสต์การแปลงต้นไม้ใน K2 ใช้ JSON สมมติแทนผลจาก `/parse-ktc-pdf` ได้ ไม่ต้องมี PDF

## กติกาการแปลงเป็นต้นไม้

| ฟิลด์ | `statement` (root) | `statement_line` (ลูก) |
|---|---|---|
| `kind` | `expense` | `expense` |
| `amount` | ผลรวมของลูกที่สร้างจริง | ยอดของรายการ (ค่าบวก) |
| `occurred_at` | statement date เวลา 00:00 Asia/Bangkok | posting date (หรือ transaction date ถ้ามี) เวลา 00:00 Asia/Bangkok |
| `has_time` | `false` | `false` |
| `dedup_key` | `ktc:stmt:<เลขบัตร 4 ตัวท้าย>:<statement date YYYY-MM-DD>` | `ktc:line:` + sha256(card4, posting date, description, amount, approval code, ลำดับซ้ำ) |
| `merchant_raw` | ว่าง | description จาก statement |
| `raw` | statement date, due date, ยอดรวมใหม่ตาม PDF, รายการที่ข้ามไป | ข้อมูลดิบของบรรทัด |

- **รายการที่ไม่สร้างเป็น `statement_line`**:
  - `payment` (เช่น ยอดชำระจาก KBank) เพราะจะผูกเป็น transfer ใน T2.2 ของ main track
  - `refund` และยอดติดลบ เพราะ refund อยู่ใน backlog
  - ให้เก็บรายการเหล่านี้ไว้ใน `raw.skipped_lines` ของ statement แทน
- **`fee` และ `interest` นับเป็นรายจ่าย** จึงสร้างเป็นลูกตามปกติ
- **ลำดับซ้ำใน dedup_key**: ถ้า statement เดียวมีรายการเหมือนกันทุกช่อง (ร้านเดิม ยอดเดิม วันเดิม) ใส่ลำดับ 0, 1, 2... เพื่อไม่ให้ dedup ทับกัน
- **statement ที่ไม่มีลูกเลย** ให้คืน `status="skipped"` ไม่สร้าง node เพราะ statement ที่ไม่มีลูกจะกลายเป็น leaf แล้วถูกนับในรายงาน

## Schema ที่มีอยู่ (ไม่ต้องสร้างเพิ่ม)

- `ledger.accounts` มี `บัตรเครดิต KTC` (type `credit_card`)
- `ledger.email_sources` มีแถว `ktc_pdf` ที่ผูกกับ account KTC แต่ยังปิดอยู่และไม่มี label
- `ledger.transactions.source` รับค่า `ktc_pdf` และ `node_type` รับ `statement`/`statement_line`
- `ledger.settings` (key, value_encrypted) สำหรับรหัสผ่าน
- `ledger.raw_emails`, `ledger.raw_attachments`: sync engine เก็บอีเมลและ PDF ต้นฉบับให้

## Gmail label

อีเมล KTC อยู่ใน label ชื่อ `financial-ktc-[card]` (ชื่อจริงตามนี้ ผู้ใช้ยืนยันแล้ว มี label เดียว)
- **ต้องตรวจก่อนใช้**: gmail-service สร้าง query เป็น `label:<ชื่อ>` แต่ Gmail search อาจตีความวงเล็บเหลี่ยมไม่ตรงตัว (บางครั้งต้องเขียนเป็น `label:financial-ktc--card-`) ให้ลอง `GET localhost:8000/api/emails?labels=financial-ktc-[card]&max_results=5` ถ้าได้ 0 ฉบับ ให้ลองรูปแบบที่แทนอักขระพิเศษด้วย `-` แล้วใช้รูปแบบที่ได้ผลเป็นค่า `gmail_label`
- เปิดใช้ด้วย `PUT localhost:8002/email-sources/ktc_pdf` body `{"gmail_label": "<รูปแบบที่ได้ผล>", "is_active": true, "account_id": <id ของ บัตรเครดิต KTC จาก GET /accounts>}`

## ส่งต่อให้ main track (ไม่ใช่งานของ KTC track)

- เขียน sync engine และเรียก `sources/ktc.py`
- T2.2 ตรวจจับ Bill Payment ของ KBank ที่บริษัทผู้รับเป็น `KRUNGTHAI CARD PUBLIC COMPANY LIMITED` แล้วแปลงเป็น transfer KBank → KTC ผูกกับ statement
- Shopee/Lazada ที่จ่ายด้วยบัตร KTC: T2.1 จะจับคู่ `statement_line` กับ order เอง

## Verification

- `pytest` ใน `expense-api` ผ่าน และเทสต์ของ main track ต้องไม่พัง
- `curl -F file=@<pdf> -F password=<pw> localhost:8001/parse-ktc-pdf` ได้ JSON ที่ยอดรวมของ `lines` (เฉพาะ purchase/fee/interest) ตรงกับยอดในหน้าแรกของ PDF
- ในทุก log ต้องไม่มีรหัสผ่านหรือเนื้อหา PDF

## สถานะ

| งาน | สถานะ |
|---|---|
| K1 `/parse-ktc-pdf` | ยังไม่เริ่ม |
| K2 `sources/ktc.py` | stub |
| K3 รหัสผ่าน KTC | stub |
| K4 เทสต์/fixture | ยังไม่เริ่ม |

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

**K1. `credit-card-statement-service`: `POST /parse-ktc-pdf`** (เสร็จ)
- รับ multipart: `file` (PDF, ไม่เกิน 10 MB) และ `password` โค้ดอยู่ใน `credit-card-statement-service/ktc_pdf.py` และ endpoint ท้าย `main.py`
- ใช้ `pikepdf` ปลดล็อก และ `pdfplumber` อ่านตาราง ทำในหน่วยความจำ ห้ามเขียนไฟล์ที่ปลดล็อกแล้วลงดิสก์
- คืน JSON
  - `statement`: `statement_date`, `due_date`, `card_masked` (เช่น `1234-56XX-XXXX-7890`), `card_last4`, `previous_balance`, `total_new_balance`, `minimum_payment`
  - `lines`: `transaction_date`, `posting_date`, `description`, `amount`, `approval_code` และ `type` (`purchase|payment|refund|fee|interest|other`)
  - **`amount` เป็น string แบบ Decimal มีเครื่องหมาย** (เช่น `"-1000.00"`) payment/refund เป็นค่าติดลบ ส่วน K2 ต้องแปลงเป็นค่าบวกตามกติกาต้นไม้เอง
  - วันที่เป็น ISO `YYYY-MM-DD`
  - `approval_code` เป็น `null` เสมอ เพราะ statement จริงไม่มีคอลัมน์นี้ (ยังคงฟิลด์ไว้เผื่อ template อื่น)
- รหัสผ่านผิดคืน 401 (`{"detail":{"code":"wrong_password"}}`) และไฟล์ใหญ่เกิน 413 ส่วน PDF อ่านไม่ได้หรือ layout เปลี่ยนคืน 422 พร้อม `detail.code` (`unreadable_pdf`, `summary_layout_changed`, `table_header_not_found`, `line_layout_changed`, `line_without_amount`, `balance_rows_not_found`, `balance_check_failed`, `bad_date`, `bad_amount`) โดย `code` ไม่มีเนื้อหา PDF
- ห้าม log รหัสผ่านหรือเนื้อหา PDF: โค้ดไม่ log อะไรเอง และไม่ส่งข้อความ exception ของไลบรารี PDF ออกไป
- เพิ่ม `pikepdf` และ `pdfplumber` ใน `credit-card-statement-service/requirements.txt` แล้ว ส่วน `requirements-dev.txt` (pytest, httpx, reportlab) ใช้รันเทสต์ ส่วน endpoint `/upload-statement/` เดิมมีเทสต์ว่ายังทำงาน

**หลักการ parse (ทนต่อ template เปลี่ยน)**
- ไม่ยึดพิกัดตายตัว: ตารางสรุปหน้า 1 อ่านจากป้ายภาษาอังกฤษ (`CLOSING DATE`, `PAYMENT DUE DATE`, `CARD NUMBER`, `MINIMUM PAYMENT`) และตารางรายการหาจากแถวหัวตาราง (`TRANS. DATE / POSTING DATE / DESCRIPTION / AMOUNT (BAHT)`)
- **fail closed**: ต้อง `previous_balance + sum(lines) == total_new_balance` ไม่เช่นนั้นคืน 422 `balance_check_failed` ไม่เจอหัวตารางหรือแถวที่เริ่มด้วยวันที่แต่ parse ไม่ได้ ก็คืน 422 เช่นกัน เพื่อให้ sync บันทึกเป็น `failed` แทนที่จะได้ตัวเลขผิดเงียบๆ
- แก้ parser แล้วใช้ `POST /sync/reparse?source=ktc_pdf` (main track) เพื่อ parse ใหม่จาก PDF ที่เก็บไว้ โดยต้อง bump `PARSER_VERSION`
- ไม่ใช้ OCR หรือบริการภายนอก เพราะ PDF มี text layer และข้อมูลเป็นข้อมูลการเงินส่วนตัว ถ้าจะใช้เป็นทางสำรองในอนาคตต้องให้ผู้ใช้ตัดสินใจเป็นรายครั้ง

**ข้อเท็จจริงของ e-Statement KTC ที่ใช้เป็น spec**
- รหัสผ่าน PDF คือวันเกิดรูปแบบ `ddMmmyy` ปี ค.ศ. 2 หลัก (เช่น `15Dec85`) ตามที่อีเมลระบุ
- วันที่ในไฟล์เป็น `dd/mm/yy` ซึ่งปี 2 หลักที่ `>= 50` ตีเป็น พ.ศ. (69 → 2569 → 2026) และ `< 50` ตีเป็น ค.ศ. (26 → 2026) ปี 4 หลักที่มากกว่า 2400 ลบ 543
- แถวรายการ: วันที่ใช้บัตร | วันที่บันทึก | รายการ | ยอด (ชิดขวา) ยอดติดลบเขียนเป็น `-` แยกด้วยช่องว่างก่อนตัวเลข
- แถวไม่มีวันที่แถวแรกคือยอดรอบที่แล้ว และแถวสุดท้ายคือสรุปยอดรอบนี้ (ซึ่งเท่ากับยอดชำระเกิน/overpaid ในกรอบสรุปเมื่อติดลบ) ไม่มีช่อง "ยอดรวมใหม่" แยกต่างหาก
- รายการ description ที่ยาวอาจขึ้นบรรทัดใหม่ในคอลัมน์ description ซึ่ง parser ต่อเข้ากับรายการก่อนหน้า
- การจัด `type`: ยอดติดลบ → `payment` ถ้า description มี `PAYMENT`/`AUTOPAY`/`ชำระ` ไม่เช่นนั้น `refund` ยอดบวก → `interest` (`INTEREST`/`ดอกเบี้ย`), `fee` (`FEE`/`VAT`/`ค่าธรรมเนียม` เป็นคำเต็ม เพราะ `COFFEE` ต้องไม่ติด) นอกนั้น `purchase` คำที่ยังไม่เคยเห็นอาจถูกจัดเป็น `purchase` ผิด ต้องตรวจเมื่อเจอ statement ใหม่

**K2. `expense-api/sources/ktc.py`** (เสร็จ)
- หา attachment ที่เป็น PDF (mime `application/pdf` หรือชื่อไฟล์ลงท้าย `.pdf`) แล้วเรียก `POST {CREDIT_CARD_STATEMENT_SERVICE_URL}/parse-ktc-pdf` ด้วย httpx (timeout 60 วินาที) ซึ่ง env นี้มีใน compose ของ expense-api แล้ว
- `PARSER_VERSION = "ktc-1"`; `build_tree(json)` เป็นฟังก์ชันล้วนที่แปลงผลจาก K1 เป็นต้นไม้ ใช้ทดสอบโดยไม่ต้องมี PDF
- ลำดับตัดสินใจ:
  - ไม่มี PDF แนบ → `skipped` (โฆษณาไม่ต้องใช้รหัสผ่าน)
  - ไม่มีรหัสผ่าน → `failed` "ยังไม่ได้ตั้งรหัสผ่าน KTC" และไม่เรียก service
  - service ตอบ 401 → `failed` รหัสผ่านไม่ถูกต้อง, 422 → `failed` พร้อม `detail.code` ของ K1, ติดต่อ service ไม่ได้ หรือ HTTP อื่น → `failed` ข้อความ error ไม่มีรหัสผ่านหรือเนื้อหา PDF
  - ไม่มี `statement_line` เหลือ → `skipped`
- engine จะ parse อีเมลที่ `failed` ซ้ำเองในรอบถัดไป (ขั้นที่ 5 ใน `docs/sync-engine.md`) ดังนั้นเมื่อผู้ใช้ตั้งรหัสผ่านแล้ว sync อีกครั้งก็พอ
- ทีละรายการ: payment, refund และยอด `<= 0` ไม่เป็นลูก (เก็บใน `raw.skipped_lines`) ส่วน `purchase`, `fee`, `interest` และ `other` ที่ยอดเป็นบวกเป็นลูก (`other` ยังไม่เคยเจอใน statement จริง)
- `occurred_at` ของลูกใช้ transaction date ถ้ามี ไม่เช่นนั้นใช้ posting date; `dedup_key` ใช้ posting date, description, ยอดรูป `0.00`, approval code และลำดับซ้ำ
- `source_ref` ของลูกคือ approval code (ถ้ามี) และ `description` ของ statement คือ `KTC e-Statement <statement date>`
- ถ้าอีเมลมี PDF มากกว่า 1 ไฟล์ จะได้ statement หลาย root และถ้าไฟล์ใดล้มเหลว ทั้งอีเมลเป็น `failed`

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
- `curl -F file=@<pdf> -F password=<pw> localhost:8001/parse-ktc-pdf` ได้ JSON โดย `previous_balance` + ผลรวม `amount` ของ `lines` ทั้งหมด (ทุก type รวม payment/refund ที่ติดลบ) ต้องเท่ากับ `total_new_balance` ซึ่ง endpoint ตรวจเองและคืน 422 ถ้าไม่ตรง (ข้อความเดิมที่ให้รวมเฉพาะ purchase/fee/interest ไม่ตรงกับหน้า PDF จริงที่มียอดยกมาและยอดชำระ)
- เทสต์ของ K1: `cd credit-card-statement-service && pytest` (ต้องมี `pip install -r requirements.txt -r requirements-dev.txt`)
- ในทุก log ต้องไม่มีรหัสผ่านหรือเนื้อหา PDF

## สถานะ

| งาน | สถานะ |
|---|---|
| K1 `/parse-ktc-pdf` | เสร็จ: เทสต์ 8 ข้อผ่าน และทดสอบกับ statement จริง 1 ฉบับแล้ว (10 รายการ ตรวจยอดผ่าน) ยังไม่ได้ลองรันใน docker (ใช้ Python 3.10 ใน Dockerfile) และยังไม่ได้ curl กับ container |
| K2 `sources/ktc.py` | เสร็จ: เทสต์ 13 ข้อ (ใช้ JSON สมมติ) และทดสอบ end-to-end กับ PDF จริงผ่าน service ในเครื่อง (8 ลูก + ข้าม 2) ยังไม่ได้รันผ่าน sync engine กับ DB จริง |
| K3 รหัสผ่าน KTC | stub |
| K4 เทสต์/fixture | ยังไม่เริ่ม |

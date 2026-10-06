# Handoff (2026-10-06)

สำหรับทำงานต่อบนเครื่องใหม่ ทั้งคนและ Claude เริ่มอ่านจากไฟล์นี้ แล้วค่อยอ่าน [CLAUDE.md](../CLAUDE.md) และ [expense-tracker-plan.md](expense-tracker-plan.md) (ตารางสถานะอยู่ต้นไฟล์)

## ตอนนี้ระบบทำอะไรได้

- **Sync จาก Gmail**: KBank (label `financial-kasikorn`) และบิลบัตร KTC (PDF, label `financial-ktc-[card]`) เข้า `ledger.transactions` เป็นรายการ pending เก็บอีเมลและ PDF ต้นฉบับไว้ใน DB
- **จัดหมวด**: เรียงตาม เลขบัญชีปลายทาง → กฎต่อผู้รับ (หมวด / โอนเข้าบัญชีตัวเอง / ไม่ต้องจำ) → Gemini (`gemini-3.5-flash-lite`, Google AI Studio key)
- **จับคู่**: จ่ายบิล KTC เป็นโอนเข้าบัตร (รวมกรณีจ่ายผ่าน KTB) และเสนอรายรับที่ซ้ำกับการโอน
- **หน้าเว็บ** (http://localhost:3000)
  - ตรวจรายการ: ยืนยัน, แก้หมวด, ไม่นับ, แนบใบเสร็จแล้วแยกรายการ
  - บันทึกเอง: รายจ่าย/รายรับ
  - สรุปรายเดือน: รายจ่าย, รายรับ, สุทธิ, ยอดโอน
  - ยอดคงเหลือ: ยอดยกมา และเทียบยอดกับอีเมล KBank
  - ตั้งค่า: บัญชี, แหล่งอีเมล, รหัส PDF KTC, กฎที่จำไว้
- **เข้าสู่ระบบ (P7.1)**: Google sign-in ทุกหน้า, API token (Bearer) สำหรับสคริปต์/Bruno, หลายผู้ใช้ (admin เพิ่มสมาชิกในหน้าตั้งค่า), Caddy ทำ HTTPS สำหรับ public IP (`--profile public`) · ข้อมูลแยกต่อผู้ใช้แล้ว (P7.2) แต่สมาชิกยัง sync Gmail ของตัวเองไม่ได้ (P7.3) ตอนนี้บันทึกเองได้อย่างเดียว
- **เทสต์**: expense-api 153 ข้อ (+2 ข้อที่ต้องมี PDF จริง) และ credit-card-statement-service 8 ข้อ

## สิ่งที่ไม่อยู่ใน git (ต้องย้ายเอง)

| ไฟล์ / ข้อมูล | ใช้ทำอะไร | ถ้าไม่ย้าย |
|---|---|---|
| `credentials.json` (root) | OAuth client ของ Gmail | สร้างใหม่ใน Google Cloud แล้วรัน `gmail-auth` |
| `gmail-service/token.json` | token ที่ consent แล้ว (scope `gmail.modify`) | รัน `docker compose --profile manual up --build gmail-auth` ใหม่ |
| `expense-api/.env` | `SECRET_KEY`, `GEMINI_API_KEY`, `KTC_PDF_PASSWORD`, sign-in (`AUTH_MODE`, `ADMIN_EMAILS`, `PUBLIC_BASE_URL`, `GOOGLE_OAUTH_CLIENT_ID/SECRET`) | ต้องตั้งใหม่ **และ `SECRET_KEY` ต้องเป็นค่าเดิมถ้าย้าย DB ไปด้วย** ไม่งั้นถอดรหัส PDF ของ KTC ที่เก็บไว้ไม่ได้ (ตั้งรหัสใหม่ในหน้าตั้งค่าแทนได้) |
| `.env` (root) | `POSTGRES_PASSWORD` (ตอนนี้ยังไม่มี ใช้ค่าเริ่มต้น `postgres`), `DOMAIN` (เครื่อง public) | ไม่ต้องทำอะไร (เครื่อง public ต้องตั้งทั้งสองค่า) |
| `expense-api/tests/fixtures/` | อีเมล KBank 7 ฉบับ + PDF KTC 1 ฉบับ (ข้อมูลจริง) | เทสต์ที่ใช้ fixture จะ skip เอง |
| `data/receipts/` | รูปใบเสร็จ (ตอนนี้ยังว่าง) | — |
| **ข้อมูลใน Postgres** (docker volume) | รายการที่ยืนยันแล้ว, กฎที่จำไว้, ยอดยกมา, รายการบันทึกเอง | **หายถ้าไม่ dump** ส่วนที่มาจากอีเมล sync ใหม่ได้ แต่การยืนยัน/กฎ/ยอดยกมา/รายการบันทึกเองจะหายหมด |

ย้าย DB (รันบนเครื่องเดิม ต้องใช้ sudo):
```bash
docker compose exec -T postgres pg_dump -U postgres -d financial -Fc > financial.dump
# เครื่องใหม่ หลัง docker compose up -d postgres:
docker compose exec -T postgres pg_restore -U postgres -d financial --clean --if-exists < financial.dump
```
ไฟล์ dump และไฟล์ในตารางข้างบนมีข้อมูลการเงินและความลับ ย้ายด้วยช่องทางส่วนตัว (USB, scp) อย่าใส่ใน git หรือส่งผ่านแชต

## ตั้งเครื่องใหม่

```bash
git clone --recurse-submodules git@github.com:spnball018/financial-assistance-ai-all-service.git
cd financial-assistance-ai-all-service
# วางไฟล์จากตารางข้างบน แล้ว:
docker compose up -d postgres
python3 -m venv expense-api/.venv && expense-api/.venv/bin/pip install -r expense-api/requirements.txt python-dotenv
python3 -m venv gmail-service/.venv && gmail-service/.venv/bin/pip install -r gmail-service/requirements.txt
python3 -m venv credit-card-statement-service/.venv && \
  credit-card-statement-service/.venv/bin/pip install -r credit-card-statement-service/requirements.txt -r credit-card-statement-service/requirements-dev.txt
(cd web && npm install)
# DB สำหรับเทสต์ (ชื่อต้องมีคำว่า test)
docker compose exec postgres psql -U postgres -c "CREATE DATABASE financial_test"
```

รันแบบพัฒนา (4 terminal):
```bash
(cd gmail-service && .venv/bin/uvicorn main:app --port 8000)
(cd credit-card-statement-service && .venv/bin/uvicorn main:app --port 8003)
(cd expense-api && DATABASE_URL=postgresql://postgres:postgres@localhost:5432/financial \
   GMAIL_SERVICE_URL=http://localhost:8000 CREDIT_CARD_STATEMENT_SERVICE_URL=http://localhost:8003 \
   .venv/bin/alembic upgrade head && .venv/bin/uvicorn app.main:app --port 8002 --env-file .env)
(cd web && npm run dev)
```
`python-dotenv` ต้องมีเพราะ `uvicorn --env-file` ใช้ (ไม่อยู่ใน requirements เพราะ container ไม่ใช้)

เทสต์:
```bash
cd expense-api && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/financial_test .venv/bin/python -m pytest
# รวมเทสต์ PDF จริงของ KTC (ต้องรัน credit-card-statement-service อยู่):
CREDIT_CARD_STATEMENT_SERVICE_URL=http://localhost:8003 KTC_PDF_PASSWORD=... TEST_DATABASE_URL=... .venv/bin/python -m pytest
cd ../credit-card-statement-service && KTC_PDF_PASSWORD=... .venv/bin/python -m pytest
cd ../web && npm run build
```

## เรื่องที่ควรรู้

- **docker ใน compose ยังไม่เคยรันครบชุด**: `web` (Node build + nginx) และ credit-card-statement-service (Python 3.10) ยังไม่เคย build ใน docker เพราะเครื่องเดิมต้องใช้ sudo ตอนพัฒนาใช้ Postgres ใน docker และที่เหลือรันจาก venv
- **ทุกพอร์ตผูกกับ `127.0.0.1`** ยกเว้น Caddy (80/443) ส่วน expense-api รับเฉพาะ Host ใน `ALLOWED_HOSTS` (เครื่อง public ต้องเพิ่มโดเมน)
- **sign-in**: ถ้ายังไม่ได้สร้าง OAuth client แบบ Web application ให้ใส่ `AUTH_MODE=off` ใน `expense-api/.env` เพื่อใช้ในเครื่อง (รับเฉพาะ localhost) ขั้นตอนตั้งค่า Google อยู่ใน README หัวข้อ Sign-in · user 1 คือเจ้าของข้อมูลเดิม ถ้าตอน migrate ยังไม่ตั้ง `ADMIN_EMAILS` จะเป็น `owner@localhost` แล้วอีเมลแรกใน `ADMIN_EMAILS` ที่เข้าสู่ระบบจะรับข้อมูลนั้นไป
- **`token.json` เป็นของ root** หลังรัน `gmail-auth` ใน docker แก้ด้วย `sudo chown $USER gmail-service/token.json`
- **Gemini**: `gemini-2.5-flash` ตอบ 404 สำหรับผู้ใช้ใหม่ และรุ่น Flash ตัวเต็มตอบ 503 บน free tier บ่อย adapter retry ให้แล้ว เปลี่ยนรุ่นได้ด้วย `GEMINI_MODEL`
- **sync**: แหล่งที่เพิ่งเปิดดึงย้อน 90 วัน รอบแรกของ KBank ใช้ราว 2 นาที
- **branch `ktc-track`**: merge เข้า `main` หมดแล้ว ลบได้
- **migration ล่าสุดคือ 0012** (ข้อมูลต่อผู้ใช้) · DB จริงบนเครื่องเดิมยังอยู่ที่ 0010 (`alembic upgrade head` ตอน start container หรือรันเองก่อน start uvicorn): ถ้าเพิ่ม migration ต้องอัปเดต [er-diagram.md](er-diagram.md) (render PNG ใหม่) และ [api-spec.md](api-spec.md)

## งานถัดไป

1. **P7.3 Gmail ต่อผู้ใช้** เชื่อม Gmail ของแต่ละคนจากหน้าเว็บ (ดูข้อจำกัด test user / 7 วันในแผน) แล้วเอาข้อจำกัด `GMAIL_SERVICE_OWNER` ใน `app/routers/sync.py` ออก
2. **T1.3 marketplace**: label `marketplace-shopee`, `marketplace-lazada`, `marketplace-aliexpress`, `marketplace-temu`
   - เพิ่มค่า `aliexpress_email`, `temu_email` ใน CHECK ของ `email_sources.source`, `transactions.source` และ `ecommerce_orders.platform` (migration ถัดไป)
   - ดึงอีเมลตัวอย่างมาเก็บใน `expense-api/tests/fixtures/` (ผู้ใช้อนุญาตให้ดึงอีเมลจริงมาเก็บใน fixtures ที่ gitignore แล้ว)
   - เขียน `parsers/<shop>.py` + `sources/<shop>.py` ตาม `sources/base.py` แล้วลงทะเบียนใน `sources/__init__.py`
3. **T2.1**: จับคู่ order ของ marketplace กับรายการจ่าย (KBank / บรรทัดในบิล KTC) แล้วแตกเป็น order → รายการสินค้า
4. **T6.7 ส่วนที่เหลือ**: จับคู่กับข้อมูลฝั่ง wallet ของ marketplace หลัง T1.3
5. **Backlog**
   - ถ่ายใบเสร็จก่อนรายการเข้าแล้วจับคู่อัตโนมัติ
   - ย่อรูปใบเสร็จก่อนเก็บ
   - บัญชีกองทุน (กำไร/ขาดทุน)
   - refund
   - Top-up ของ KBank (ตอนนี้ข้าม)
   - chatbot ใน langgraph อ่านจาก ledger
   - `user:` ใน compose ของ gmail-service

ไม่มีเรื่องที่รอผู้ใช้ตัดสินใจ ณ ตอนนี้ คำตอบทั้งหมดบันทึกไว้ในแผนแล้ว

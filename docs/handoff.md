# Handoff (2026-10-12)

สำหรับทำงานต่อใน session ใหม่หรือบนเครื่องใหม่ ทั้งคนและ Claude เริ่มอ่านจากไฟล์นี้ แล้วค่อยอ่าน [CLAUDE.md](../CLAUDE.md) และ [expense-tracker-plan.md](expense-tracker-plan.md) (ตารางสถานะอยู่ต้นไฟล์)

## Deploy รอบ 2026-10-12 (ยังไม่ได้ทำบน server)

git บนเครื่อง dev แก้แล้ว: merge `origin/main` (0014 ตามที่ push ไป ไม่มีโรงแรม) แล้วโรงแรมย้ายไป 0015 ไม่มีการ force push · commit ใน session นี้: 0015, OAuth จาก `credentials.json` (รวม submodule gmail-service), Shopee/Lazada + จับคู่ order, แผงรายละเอียด/สรุปแบบกางได้, wallet จากรูปหน้าจอ (0016), หน้าเว็บ, เอกสาร

บน server:
1. ลบ `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` ออกจาก `expense-api/.env` (ถ้ามี env var จะใช้ก่อน) และวาง `credentials.json` ฉบับที่มี secret ใหม่ที่ root (secret เก่าน่าจะเป็นสาเหตุที่ sync บน production fail)
2. `git pull && git submodule update --init --recursive`
3. `docker compose --profile tunnel up -d --build` (expense-api รัน `alembic upgrade head` เองตอนเริ่ม)
4. หน้าเว็บ: ตั้งค่า → Gmail ของฉัน → เชื่อมใหม่, ตั้งค่า → แหล่งอีเมล: เปิด `marketplace-shopee` (บัญชี Shopee) และ `marketplace-lazada` (บัญชี Lazada)
5. กฎ "true money company limited" ใน DB dev จำเป็นหมวดค่าเน็ต/โทรศัพท์ ซึ่งผิด (เป็นการเติม TrueMoney) ถ้าเกิดบน production ให้เปลี่ยนเป็นโอนเข้า TrueMoney

ผู้ใช้ push เองระหว่างที่ Claude ทำงานได้ อย่า amend หรือ rebase commit ที่อาจ push ไปแล้ว ให้ commit ใหม่ต่อท้ายเสมอ · auto mode บล็อก `git reset` ใช้ merge แทนได้

## Production

- **https://pfa.panusian.com** รันบนเครื่องที่บ้าน ผ่าน **Cloudflare Tunnel** (ไม่เปิดพอร์ตที่ router)
- รันด้วย `docker compose --profile tunnel up -d --build` ซึ่งเปิด postgres, gmail-service, credit-card-statement-service, expense-api, web, `cloudflared`, `db-backup`
- Cloudflare Zero Trust → Tunnels → Public Hostname: `pfa.panusian.com` → `HTTP` → `web:80`
- เริ่มด้วย DB ใหม่ (ไม่ได้ย้ายข้อมูลจากเครื่อง dev)
- ตั้งค่าบน server (ไม่อยู่ใน git):
  - `./.env`: `POSTGRES_PASSWORD` (สุ่มใหม่), `CLOUDFLARE_TUNNEL_TOKEN` (ต้องขึ้นต้นด้วย `eyJ` ไม่มีเครื่องหมายคำพูด)
  - `expense-api/.env`: `SECRET_KEY` ใหม่, `AUTH_MODE=google`, `ADMIN_EMAILS` (มี `spnball@gmail.com`), `PUBLIC_BASE_URL=https://pfa.panusian.com` (**ต้อง https** ไม่งั้น Google ตอบ redirect_uri_mismatch), `ALLOWED_HOSTS=localhost,127.0.0.1,expense-api,pfa.panusian.com` (ไม่งั้น 400 Invalid host header), `SYNC_FIRST_RUN_DAYS=180` (ผู้ใช้อยากได้ 6 เดือน), **`GEMINI_API_KEY`** (ไม่มีแล้วจะไม่ error แต่ AI ไม่จัดหมวดเลย และอ่านรูปใบเสร็จ/wallet ได้ 503 · production เคยขาดค่านี้ 2026-10-12), Google OAuth client อ่านจาก `credentials.json` (ไม่ต้องใส่ใน `.env`)
  - `credentials.json`, `gmail-service/token.json` (ต้องมีไฟล์ ไม่งั้น docker สร้างเป็น directory แทน)
- Google OAuth client `178410589198-go5e846...` (ชื่อ "Personal Financial") มี redirect URI ของ localhost และต้องมี `https://pfa.panusian.com/api/auth/callback` กับ `/api/gmail/callback`
- แก้ env แล้วต้อง `up -d --force-recreate <service>` (`restart` ไม่อ่าน `.env` ใหม่)
- **สถานะล่าสุดที่รู้**: sign-in ผ่านแล้ว ลองดึงเมลแล้ว fail น่าจะเพราะ client secret เก่า (บนเครื่อง dev เจอ `invalid_client` แบบเดียวกัน) ยังไม่ได้ยืนยันกับ log บน server ดูได้จาก `docker compose logs expense-api gmail-service` และตาราง `ledger.sync_runs` (คอลัมน์ `status`, `error`)
- **Cloudflare ตัด request ที่ 100 วินาที**: `POST /sync` รอบแรกนานกว่านั้น browser จะได้ 524 แต่ sync ยังรันต่อบน server

เครื่อง dev: Docker ต้องใช้ sudo (Claude รัน docker ไม่ได้) จึงรัน gmail-service, expense-api, web จาก venv/Node และ Postgres ใน docker (ผู้ใช้เปิดเอง) `expense-api/.env` บนเครื่อง dev ตั้ง `AUTH_MODE=google` และ `ADMIN_EMAILS=surapun.prasit@gmail.com,spnball@gmail.com`

## ตอนนี้ระบบทำอะไรได้

- **Sync จาก Gmail**: KBank (label `financial-kasikorn`), บิลบัตร KTC (PDF, label `financial-ktc-[card]`), order Shopee/Lazada (`marketplace-shopee`, `marketplace-lazada` → `ecommerce_orders` แล้วแยกรายการจ่าย KBank เป็น order → สินค้า รอยืนยัน) เข้า `ledger.transactions` เป็นรายการ pending เก็บอีเมลและ PDF ต้นฉบับไว้ใน DB รอบแรกของแต่ละแหล่งดึงย้อน `SYNC_FIRST_RUN_DAYS` (ค่าเริ่มต้น 90)
- **จัดหมวด**: เรียงตาม เลขบัญชีปลายทาง → กฎต่อผู้รับ (หมวด / โอนเข้าบัญชีตัวเอง / ไม่ต้องจำ) → Gemini (`gemini-3.5-flash-lite`, Google AI Studio key) AI เลือกได้เฉพาะหมวดย่อยสุด (leaf)
- **จับคู่**: จ่ายบิล KTC เป็นโอนเข้าบัตร (รวมกรณีจ่ายผ่าน KTB) และเสนอรายรับที่ซ้ำกับการโอน
- **หน้าเว็บ**
  - ตรวจรายการ: ยืนยัน, แก้หมวด, ไม่นับ, แนบใบเสร็จแล้วแยกรายการ, ชื่อบัญชีใต้ชื่อร้าน, กดชื่อร้านดูรายละเอียด/อีเมลต้นฉบับ, ยกเลิกการจับคู่ order, นำเข้ารูปหน้าจอ wallet (TrueMoney)
  - บันทึกเอง: รายจ่าย/รายรับ
  - สรุปรายเดือน: รายจ่าย, รายรับ, สุทธิ, ยอดโอน, กดหมวดเพื่อกางรายการ
  - ยอดคงเหลือ: ยอดยกมา และเทียบยอดกับอีเมล KBank
  - ตั้งค่า: บัญชี, แหล่งอีเมล, รหัส PDF KTC, กฎที่จำไว้, API token, ผู้ใช้, Gmail ของฉัน
- **เข้าสู่ระบบ**: Google sign-in, API token (Bearer), หลายผู้ใช้, ข้อมูลแยกต่อผู้ใช้, แต่ละคนเชื่อม Gmail ของตัวเอง
- **หมวดหมู่** ใช้ร่วมทุกผู้ใช้ เพิ่ม/แก้ได้ผ่าน migration เท่านั้น (ยังไม่มีหน้าจัดการหมวด)
- **Backup**: `db-backup` dump ทุก 24 ชั่วโมงไป `./data/backups` เก็บ 14 วัน (`BACKUP_KEEP_DAYS`) dump ใช้ไม่ได้ถ้าไม่มี `SECRET_KEY` เดิม
- **เทสต์**: expense-api 207 ข้อ (+2 ข้อที่ต้องมี PDF จริง; fixture อีเมล Shopee/Lazada จริงอยู่ใน `tests/fixtures/` ซึ่ง gitignore) และ credit-card-statement-service 8 ข้อ

## สิ่งที่ไม่อยู่ใน git

| ไฟล์ / ข้อมูล | ใช้ทำอะไร | ถ้าไม่มี |
|---|---|---|
| `credentials.json` (root) | OAuth client แบบ web (client เดียวกับ sign-in) `gmail-service/credentials.json` เป็น symlink มาที่ไฟล์นี้ | gmail-service เริ่มไม่ได้ |
| `gmail-service/token.json` | token ของ user 1 แบบเดิม (scope `gmail.modify`) | รัน `docker compose --profile manual up --build gmail-auth` |
| `expense-api/.env` | ดูหัวข้อ Production · `SECRET_KEY` **ถอดรหัส Gmail ของทุกคนและรหัส PDF KTC** ห้ามเก็บไว้ที่เดียวกับ DB dump | ตั้งใหม่ ถ้า `SECRET_KEY` เปลี่ยน ทุกคนต้องเชื่อม Gmail ใหม่ |
| `.env` (root) | `POSTGRES_PASSWORD`, `CLOUDFLARE_TUNNEL_TOKEN` (หรือ `DOMAIN` ถ้าใช้ Caddy), `BACKUP_KEEP_DAYS` | เครื่อง dev ไม่ต้องมี |
| `expense-api/tests/fixtures/` | อีเมล KBank 7 ฉบับ + PDF KTC 1 ฉบับ (ข้อมูลจริง) | เทสต์ที่ใช้ fixture จะ skip เอง |
| `data/receipts/`, `data/backups/` | รูปใบเสร็จ, dump รายวัน | — |

ย้าย DB:
```bash
docker compose exec -T postgres pg_dump -U postgres -d financial -Fc > financial.dump
docker compose exec -T postgres pg_restore -U postgres -d financial --clean --if-exists < financial.dump
```
ไฟล์ dump และไฟล์ในตารางข้างบนมีข้อมูลการเงินและความลับ ย้ายด้วยช่องทางส่วนตัว (USB, scp) อย่าใส่ใน git หรือส่งผ่านแชต

## ตั้งเครื่อง dev

```bash
git clone --recurse-submodules git@github.com:spnball018/financial-assistance-ai-all-service.git
cd financial-assistance-ai-all-service
docker compose up -d postgres
python3 -m venv expense-api/.venv && expense-api/.venv/bin/pip install -r expense-api/requirements.txt python-dotenv
python3 -m venv gmail-service/.venv && gmail-service/.venv/bin/pip install -r gmail-service/requirements.txt
python3 -m venv credit-card-statement-service/.venv && \
  credit-card-statement-service/.venv/bin/pip install -r credit-card-statement-service/requirements.txt -r credit-card-statement-service/requirements-dev.txt
(cd web && npm install)
docker compose exec postgres psql -U postgres -c "CREATE DATABASE financial_test"
```

รันแบบพัฒนา:
```bash
(cd gmail-service && .venv/bin/uvicorn main:app --port 8000)
(cd credit-card-statement-service && .venv/bin/uvicorn main:app --port 8003)
(cd expense-api && DATABASE_URL=postgresql://postgres:postgres@localhost:5432/financial \
   GMAIL_SERVICE_URL=http://localhost:8000 CREDIT_CARD_STATEMENT_SERVICE_URL=http://localhost:8003 \
   .venv/bin/alembic upgrade head && .venv/bin/uvicorn app.main:app --port 8002 --env-file .env)
(cd web && npm run dev)
```
หยุด expense-api ด้วย PID ของ process uvicorn (`ss -ltnp | grep 8002`) ไม่ใช่ `pkill -f` เพราะจะฆ่า shell ของตัวเองด้วย

เทสต์:
```bash
cd expense-api && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/financial_test .venv/bin/python -m pytest
cd ../web && npm run build
```

## เรื่องที่ควรรู้

- **compose**: `DOMAIN` และ `CLOUDFLARE_TUNNEL_TOKEN` ใช้ `${VAR:-}` เพราะ `:?` ถูกตรวจกับทุก service แม้ profile ไม่ได้เปิด ตรวจ config ได้โดยไม่ต้องใช้ daemon: `docker compose --profile tunnel config -q`
- **nginx (`web`)** เป็นทางเข้าเดียวของ browser เสิร์ฟหน้าเว็บ และส่ง `/api/*` ต่อไป expense-api พร้อม `Host` เดิม ใส่ security headers เอง (เพราะใช้ tunnel ไม่มี Caddy)
- **Gmail consent screen ยังเป็น Testing**: Google หมดอายุการเชื่อม Gmail ของแต่ละคนทุก 7 วัน และทุกคนต้องอยู่ในรายชื่อ test user
- **Gemini**: `gemini-2.5-flash` ตอบ 404 สำหรับผู้ใช้ใหม่ รุ่น Flash ตัวเต็มตอบ 503 บน free tier บ่อย adapter retry ให้แล้ว
- **migration**: ล่าสุดคือ 0016 (`wallet_screenshots`) DB `financial` และ `financial_test` บนเครื่อง dev อยู่ที่ 0016 ถ้าเพิ่ม migration ที่เปลี่ยน schema ต้องอัปเดต [er-diagram.md](er-diagram.md) (render PNG ใหม่) และ [api-spec.md](api-spec.md)

## งานถัดไป

1. **Deploy** (หัวข้อแรก) แล้วดูว่า sync บน production ผ่านไหม
2. **Wallet**: ผู้ใช้ลองนำเข้ารูปจริงผ่านหน้าเว็บ (ทดสอบแล้วแค่ผ่านสคริปต์ + rollback) ชื่อร้านจากภาพถ่ายสะกดเพี้ยนบ้าง
3. **sync แบบ background**: `POST /sync` ตอบทันที แล้วหน้าเว็บ poll `GET /sync/runs` แก้ปัญหา Cloudflare ตัดที่ 100 วินาที (เสนอผู้ใช้ไว้ ยังไม่ได้ตอบ)
4. **หน้าจัดการหมวด** (admin เท่านั้น เพราะหมวดใช้ร่วมกัน) ผู้ใช้ขอเพิ่มหมวด 4 ครั้งในวันเดียว (เสนอไว้ ยังไม่ได้ตอบ)
5. **T1.3 ส่วนที่เหลือ**: AliExpress, Temu (label `marketplace-aliexpress`, `marketplace-temu`) ต้องเพิ่มค่าใน CHECK ของ `email_sources.source`, `transactions.source`, `ecommerce_orders.platform` · Shopee/Lazada เสร็จแล้ว
6. **T2.1 ส่วนที่เหลือ**: จับคู่ order กับบรรทัดบิล KTC (ใช้วันที่ ±2 วัน) order ที่จ่ายด้วยบัตรยังค้างอยู่
7. **T6.7 ส่วนที่เหลือ**: จับคู่กับข้อมูลฝั่ง wallet ของ marketplace หลัง T1.3
8. **Backlog**: ถ่ายใบเสร็จก่อนรายการเข้าแล้วจับคู่อัตโนมัติ, ย่อรูปใบเสร็จ, บัญชีกองทุน, refund, Top-up ของ KBank, chatbot ใน langgraph อ่านจาก ledger, `user:` ใน compose ของ gmail-service, ย้าย Gmail consent เป็น In production

รอผู้ใช้ตัดสินใจ: ข้อ 3 และ 4

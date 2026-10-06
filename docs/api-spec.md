# API Spec

รวม HTTP API ของทุก service ในโปรเจกต์ Expense Tracker ทุก service เป็น FastAPI จึงดู Swagger แบบ interactive ได้ที่ `/docs` และ OpenAPI ที่ `/openapi.json` ของแต่ละ service ถ้าเอกสารนี้ไม่ตรงกับ `/openapi.json` ให้ถือโค้ดเป็นหลัก แล้วแก้เอกสารนี้

**สถานะ**: ✅ ใช้งานได้แล้ว · 🚧 กำลังทำ (KTC track) · 📝 วางแผนไว้ ยังไม่มีโค้ด

## Services

| Service | Host port (local) | URL ภายใน docker network | หน้าที่ |
|---|---|---|---|
| `expense-api` | 8002 | `http://expense-api:8000` | ledger, sync, ตั้งค่า (เรียกจาก UI) |
| `gmail-service` | 8000 | `http://gmail-service:8000` | ตัวกลางอ่าน Gmail ทั่วไป ไม่มี logic การเงิน |
| `credit-card-statement-service` | 8003 | `http://credit-card-statement-service:8000` | แกะไฟล์ statement (CSV, PDF ของ KTC) |

ยังไม่มีระบบ auth ทุก endpoint เปิดให้ใครก็ได้ที่เข้าถึงพอร์ตได้ (Google Auth อยู่ใน backlog)

ข้อตกลงร่วม
- เงินเป็น decimal: ใน request ส่งเป็น string (แนะนำ เช่น `"45.50"`) หรือ number ก็ได้ ใน response เป็น number สกุลเงินเริ่มต้นคือ `THB`
- เวลาเป็น ISO 8601 พร้อม timezone; `occurred_at` ของรายการเป็นเวลา Asia/Bangkok
- error ใช้รูปแบบของ FastAPI: `{"detail": "..."}` หรือ `{"detail": {"code": "..."}}`

---

## expense-api (`localhost:8002`)

### ข้อมูลอ้างอิง

#### ✅ `GET /accounts?include_inactive=true`
บัญชีทั้งหมด เรียงตาม `sort_order` (ส่ง `include_inactive=false` เพื่อเอาเฉพาะที่ใช้งาน)

```json
[
  {"id": 1, "name": "KBank ใช้จ่าย", "type": "bank", "institution": "KBank", "masked_ref": "x5204",
   "currency": "THB", "is_active": true, "purpose": "บัญชีจ่ายประจำ ได้อีเมลแจ้งรายการจาก K PLUS", "sort_order": 1}
]
```
`type`: `bank | credit_card | ewallet | cash | investment`

#### ✅ `GET /accounts/balances`
ยอดคงเหลือของทุกบัญชี คำนวณจาก view `ledger.v_account_postings`
```json
[{"account_id": 1, "name": "KBank ใช้จ่าย", "type": "bank", "is_active": true,
  "balance": 12800.0, "balance_with_pending": 11801.0, "pending_count": 1, "last_movement_at": "2026-10-03T03:52:02+00:00",
  "opening": {"amount": 10000.0, "as_of": "2026-09-01"},
  "bank_reported": {"balance": 14189.79, "as_of": "2026-10-03T03:52:02+00:00", "computed": 14300.0, "difference": -110.21}}]
```
- `balance` = ยอดยกมา + รายการ `confirmed` ตั้งแต่ต้นวัน `opening.as_of` (เวลาไทย) ถ้าไม่มียอดยกมาจะนับทุกรายการ ส่วน `balance_with_pending` รวมรายการ `pending` ด้วย และไม่นับ `ignored`
- การนับยอด: รายการระดับบนสุด (root) เท่านั้นที่ขยับยอดบัญชี (expense/transfer = ออก, income/opening = เข้า) ลูกในต้นไม้ไม่นับซ้ำ ส่วนทุกรายการ transfer เพิ่มยอดให้บัญชีปลายทาง
- `bank_reported`: เฉพาะบัญชีที่ผูกกับแหล่ง `kbank_email` "ยอดถอนได้" จากอีเมล K PLUS ล่าสุด เทียบกับยอดที่ระบบคำนวณ ณ เวลาเดียวกัน (รวม pending) ถ้าต่างกัน มักเป็นเงินเข้าที่ไม่มีอีเมลแจ้ง หรือยังไม่ได้ใส่ยอดยกมา

#### ✅ `PUT /accounts/{id}/opening-balance`
```json
{"amount": "10000.00", "as_of": "2026-09-01"}
```
ยอดเงิน ณ ต้นวัน `as_of` (บัญชีละ 1 รายการ ส่งซ้ำ = แก้) บัตรเครดิตใส่ติดลบสำหรับยอดค้าง เก็บเป็น transaction `node_type=opening_balance`, `kind=opening`, `status=confirmed` ไม่อยู่ในรายงานรายจ่าย · `404` ถ้าไม่มีบัญชี

#### ✅ `DELETE /accounts/{id}/opening-balance`
`204`

#### ✅ `POST /accounts`
```json
{"name": "กองทุน SSF", "type": "investment", "institution": "KAsset", "masked_ref": null, "purpose": null}
```
Response `201` · `409` ชื่อซ้ำ · `422` ข้อมูลผิด

`masked_ref` คือเลขท้ายบัญชี / wallet ID / พร้อมเพย์ อย่างน้อย 4 ตัวเลข (เช่น `7722`, `x5204`) ใช้จับคู่ว่าการโอนออกจาก KBank เข้าบัญชีไหนของผู้ใช้ (T6.2) **ห้ามเก็บเลขบัญชีเต็ม**

#### ✅ `PATCH /accounts/{id}`
แก้เฉพาะฟิลด์ที่ส่งมา (`name`, `type`, `institution`, `masked_ref`, `purpose`, `is_active`, `sort_order`) ส่ง `masked_ref: ""` เพื่อลบ บัญชีลบไม่ได้เพราะมีรายการอ้างถึง ให้ตั้ง `is_active: false` แทน

Errors: `404` · `409` ชื่อซ้ำ · `422`

#### ✅ `GET /categories`
หมวดหมู่แบบ flat เรียงให้หมวดแม่มาก่อนหมวดลูกของตัวเอง UI สร้าง tree จาก `parent_id`

```json
[
  {"id": 1, "parent_id": null, "name_th": "อาหารและเครื่องดื่ม", "name_en": "Food & Dining", "kind": "expense", "sort_order": 1},
  {"id": 2, "parent_id": 1, "name_th": "ตลาดสด/ของสด", "name_en": "Fresh Market", "kind": "expense", "sort_order": 1}
]
```

### แหล่งอีเมล (Gmail label ต่อแหล่ง)

#### ✅ `GET /email-sources`
```json
[
  {"id": 1, "source": "kbank_email", "gmail_label": "financial-kasikorn",
   "sender_filter": "KPLUS@kasikornbank.com", "account_id": 1, "is_active": true},
  {"id": 2, "source": "ktc_pdf", "gmail_label": null, "sender_filter": null, "account_id": 2, "is_active": false}
]
```
`source`: `kbank_email | ktc_pdf | shopee_email | lazada_email`

#### ✅ `PUT /email-sources/{source}`
แทนที่ค่าทั้งแถว (ไม่ใช่ partial update) ฟิลด์ที่ไม่ส่งจะกลายเป็น `null`

Request
```json
{"gmail_label": "financial-ktc-[card]", "sender_filter": null, "account_id": 2, "is_active": true}
```
| ฟิลด์ | ชนิด | บังคับ | หมายเหตุ |
|---|---|---|---|
| `is_active` | bool | ใช่ | |
| `gmail_label` | string \| null | ต้องมีถ้า `is_active=true` | string ว่างถือเป็น `null` |
| `sender_filter` | string \| null | ไม่ | sync จะเพิ่ม `from:<ค่านี้>` ใน query |
| `account_id` | int \| null | ไม่ | บัญชีที่รายการจากแหล่งนี้จะผูกด้วย ต้องมีก่อน sync สร้างรายการได้ |

Response: แถวที่อัปเดตแล้ว (รูปแบบเดียวกับ `GET`)

Errors: `404` ไม่มี source นี้ · `422` เปิดใช้โดยไม่มี `gmail_label` หรือ body ผิดรูปแบบ

### Sync

#### ✅ `POST /sync`
ดึงอีเมลใหม่ของทุกแหล่งที่ `is_active` บันทึกลง `raw_emails` แล้ว parse เป็นรายการ `pending`
- ครั้งแรกดึงย้อนหลัง 90 วัน ครั้งต่อไปเริ่มจาก `range_to` ของรอบที่ `success` ล่าสุดลบ 1 วัน
- **เป็น request แบบรอผล**: รอบแรกใช้เวลาหลายนาที (ราว 2–3 นาทีต่อ 150 ฉบับ) client ควรตั้ง timeout ยาว

Response `200`
```json
{
  "run_id": 4,
  "counts": {
    "kbank_email": {"fetched": 152, "new": 152, "duplicate": 0, "skipped": 0, "failed": 0}
  }
}
```
| ฟิลด์ใน counts | ความหมาย |
|---|---|
| `fetched` | อีเมลใหม่ที่บันทึกลง `raw_emails` รอบนี้ |
| `new` | แถวใน `transactions` ที่สร้างใหม่ (นับทุก node ในต้นไม้) |
| `duplicate` | node ที่มี `dedup_key` อยู่แล้ว |
| `skipped` | อีเมลที่ parser ตั้งใจข้าม (เช่น Top-up, รายการ Failure) |
| `failed` | อีเมลที่ดึงหรือ parse ไม่สำเร็จ จะถูกลองใหม่ในรอบถัดไป |
| `error` | มีเฉพาะเมื่อทั้งแหล่งล้ม เช่น `"no parser registered"` หรือ `"gmail: ..."` |

ถ้ามีแหล่งไหนเรียก Gmail ไม่สำเร็จ รอบนั้นถูกบันทึกเป็น `failed` และรอบถัดไปจะดึงช่วงเวลาเดิมซ้ำ

Errors: `409` มี sync อื่นกำลังรัน (sync ที่ค้าง `running` นานเกิน 30 นาทีถือว่าตายแล้ว และไม่บล็อก)

หลัง sync เสร็จจะจัดหมวดอัตโนมัติ (เหมือนเรียก `POST /categorize`) และใส่ผลใน key `categorize` ของ response ถ้าการจัดหมวดล้ม sync ยังถือว่าสำเร็จ และ key นี้จะเป็น `{"error": "<ชื่อ exception>"}`

#### ✅ `POST /sync/reparse?source={source}`
parse อีเมลทุกฉบับของแหล่งนั้นที่เก็บใน `raw_emails` ใหม่ ไม่เรียก Gmail ใช้ตอนแก้หรือเพิ่ม parser รันซ้ำได้ไม่ซ้ำข้อมูล เพราะมี `dedup_key`

Response `200`
```json
{"source": "kbank_email", "counts": {"fetched": 0, "new": 0, "duplicate": 152, "skipped": 0, "failed": 0}}
```
Errors: `404` ไม่มี parser สำหรับ source นี้

#### ✅ `GET /sync/runs?limit=20`
ประวัติ sync ล่าสุดก่อน (`limit` 1–100)
```json
[
  {"id": 5, "started_at": "2026-10-05T07:40:01+00:00", "finished_at": "2026-10-05T07:40:02+00:00",
   "status": "success", "range_from": "2026-10-04T07:37:55+00:00", "range_to": "2026-10-05T07:40:01+00:00",
   "counts": {"kbank_email": {"fetched": 0, "new": 0, "duplicate": 0, "skipped": 0, "failed": 0}}, "error": null}
]
```
`status`: `running | success | failed`

### จัดหมวดหมู่

#### ✅ `POST /categorize`
จัดหมวดรายการที่ `pending`, `kind=expense`, ยังไม่มีหมวด (`category_source='none'`) และเป็น leaf (ไม่มีลูก) ตามลำดับ
1. ตั้ง `merchant_key` จาก `merchant_raw` (ตัดวงเล็บ, ตัวเลขสาขา/รหัส, เครื่องหมาย, คำนำหน้าชื่อ แล้วทำเป็นตัวพิมพ์เล็ก)
2. **โอนเข้าบัญชีตัวเอง (ตรงเลขบัญชี)**: ถ้าเลขปลายทางในอีเมล (`to_account`) ลงท้ายด้วย `masked_ref` ของบัญชีผู้ใช้ **บัญชีเดียว** รายการจะเป็น `kind=transfer` ไปบัญชีนั้น (`category_source='account'`) ถ้าตรงหลายบัญชีจะไม่เดา
3. **กฎต่อผู้รับ (Merchant Memory)**: ค้นจาก `ผู้รับ|เลขท้าย 4 ตัวของปลายทาง` ก่อน แล้วค่อยค้นจากผู้รับอย่างเดียว ได้ผลเป็น
   - `category` → หมวดนั้น (`category_source='memory'`)
   - `transfer` → โอนเข้าบัญชีที่จำไว้ (`category_source='memory'`)
   - `ask` (ไม่ต้องจำ) → ข้ามไปให้ AI เสนอ
4. **AI** (Gemini): ส่งทีละไม่เกิน 25 รายการ (เฉพาะรายการรายจ่าย ไม่ส่ง transfer) ถ้า confidence ≥ `AI_CONFIDENCE_THRESHOLD` (ค่าเริ่มต้น 0.7) และเป็นหมวด leaf ที่มีอยู่จริง จะได้หมวดนั้น ไม่งั้นได้ "Uncategorized" ทั้งสองกรณีเป็น `category_source='ai'` และเก็บ `ai_confidence`, `ai_reason`
5. ถ้าไม่ได้ตั้ง AI ไว้ หรือ AI ล้ม รายการจะคงเป็น `none` และถูกลองใหม่ในการเรียกครั้งถัดไป

**เลือก AI adapter ด้วย env `CATEGORIZER`** (ตั้งใน `expense-api/.env`)

| ค่า | ใช้อะไร | env ที่ต้องมี |
|---|---|---|
| `gemini` | Gemini API ด้วย API key | `GEMINI_API_KEY` |
| `vertex` | Gemini บน Google Vertex AI คิดเงินกับ GCP project | `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` (ค่าเริ่มต้น `global`) และ Application Default Credentials |
| `none` | ไม่ใช้ AI (ใช้แค่ Merchant Memory) | |
| ไม่ตั้ง | `gemini` ถ้ามี `GEMINI_API_KEY` ไม่งั้น `none` | |

ทุก adapter ใช้ prompt เดียวกัน (`build_prompt`) และรุ่นจาก `GEMINI_MODEL` (ค่าเริ่มต้น `gemini-3.5-flash-lite`) และ retry เองเมื่อ Gemini ตอบ 503/429 ส่วน `CATEGORIZE_BATCH_SIZE` (ค่าเริ่มต้น 25) คือจำนวนรายการต่อการเรียก 1 ครั้ง ถ้าตั้งค่า adapter ผิด `POST /categorize` จะตอบ `500` พร้อมชื่อ env ที่ขาด ส่วน `POST /sync` ยังทำงานตามปกติ แค่ key `categorize` จะเป็น error

Response
```json
{"transfer": 3, "memory": 12, "ai": 30, "uncategorized": 5, "left": 0}
```
| ฟิลด์ | ความหมาย |
|---|---|
| `transfer` | เป็นการโอนเข้าบัญชีตัวเอง (จากเลขบัญชี หรือกฎที่จำไว้) |
| `memory` | ได้หมวดจาก Merchant Memory |
| `ai` | AI มั่นใจพอ ได้หมวดที่ AI เลือก |
| `uncategorized` | AI ไม่มั่นใจ หรือตอบหมวดที่ใช้ไม่ได้ จึงได้ Uncategorized |
| `left` | ยังไม่มีหมวด (ไม่มี AI หรือ AI ล้ม) |
| `error` | มีเมื่อ AI ล้ม เช่น `"ai: ClientError"` (มีแค่ชื่อ exception เพราะข้อความอาจมีชื่อร้านและยอดเงิน) |

ข้อมูลที่ส่งไปให้ Gemini มีแค่ id, ชื่อร้าน/ผู้รับ, ช่องทาง, ยอดเงิน และคำอธิบาย ไม่มีเลขบัญชี

### ใบเสร็จ (T6.5)

แนบรูปใบเสร็จกับรายการจ่าย (`pending`, `expense`, ยังไม่มีลูก) → Gemini อ่านรายการสินค้าพร้อมเสนอหมวด → ผู้ใช้แก้แล้วสั่งแยก ระบบสร้างลูก `receipt_item` ทีละรายการ และ `adjustment` 1 บรรทัดถ้ายอดรวมไม่เท่ายอดจ่าย (ส่วนลด, ปัดเศษ, บรรทัดที่อ่านไม่ได้) ทำให้ผลรวมของลูกเท่ากับแม่เสมอ รายงานนับที่ลูก ส่วนยอดบัญชีนับที่แม่ จึงไม่นับซ้ำ **รูปเก็บเป็นไฟล์** ใน `RECEIPTS_DIR` (ค่าเริ่มต้น `<repo>/data/receipts` ซึ่ง docker-compose mount เข้า container) ตั้งชื่อเป็น `YYYY/MM/<sha256>.<ext>` ส่วน DB เก็บแค่ path นั้นใน `storage_uri`

#### ✅ `POST /transactions/{id}/receipts`
multipart `file` (JPEG/PNG/WebP/HEIC, ไม่เกิน 10 MB) บันทึกรูปแล้วอ่านทันที (ราว 4 วินาที)
```json
{"id": 1, "transaction_id": 140, "storage_uri": "2026/10/3f2a...e1.jpg", "mime_type": "image/jpeg", "ocr_status": "done", "ocr_model": "gemini-3.5-flash-lite",
 "ocr_result": {"merchant": "...", "total": 339.0,
   "items": [{"name": "นมสดจืด 1L", "quantity": 2, "amount": 90.0, "category_id": 8, "confidence": 0.95}],
   "adjustments": [{"name": "ส่วนลดสมาชิก", "amount": -20.0}]},
 "payment_amount": 339.0, "items_total": 359.0, "difference": -20.0, "applied_at": null}
```
Errors: `409` รูปเดิมซ้ำ (เทียบ sha256) · `422` ชนิดไฟล์/ขนาดผิด หรือรายการแยกไม่ได้ · `503` ไม่ได้ตั้ง `GEMINI_API_KEY` (รูปถูกเก็บแล้ว อ่านทีหลังด้วย `/receipts/{id}/read`) ถ้าอ่านไม่สำเร็จ `ocr_status = failed` และ `ocr_error` เป็นชื่อ exception

#### ✅ `GET /transactions/{id}/receipts` · `GET /receipts/{id}` · `GET /receipts/{id}/image` · `POST /receipts/{id}/read`
ดูรายการใบเสร็จของรายการจ่าย, ดูใบเดียว, ดูรูป (`404` ถ้าไฟล์ถูกย้ายหรือลบออกจากโฟลเดอร์) และสั่งอ่านใหม่ (`RECEIPT_MODEL` ใช้แทน `GEMINI_MODEL` ได้)

#### ✅ `POST /receipts/{id}/apply`
```json
{"items": [{"name": "นมสดจืด 1L", "amount": 90, "category_id": 8, "confidence": 0.95}]}
```
ไม่ส่ง `items` = ใช้ตามที่อ่านได้ · `confidence` ส่งเฉพาะบรรทัดที่ผู้ใช้ไม่ได้แก้ (ได้ `category_source = ai`, ไม่งั้น `user`) · หมวดที่ใช้ไม่ได้จะกลายเป็น Uncategorized · `adjustment` ได้หมวดเดียวกับรายการแม่
```json
{"parent_id": 140, "items": 4, "adjustment": -20.0}
```

#### ✅ `DELETE /transactions/{id}/receipt-items`
ยกเลิกการแยก (ลบลูกที่มาจากใบเสร็จ รูปยังอยู่) ใช้ได้เฉพาะรายการ `pending` → `{"removed": 5}`

### กฎต่อผู้รับ (Merchant Memory)

#### ✅ `GET /merchant-rules`
กฎที่ระบบจำไว้ เรียงตามจำนวนครั้งที่ใช้
```json
[{"merchant_key": "ptt station", "action": "category", "category_id": 17, "category_name": "ค่าน้ำมัน",
  "counter_account_id": null, "account_name": null, "hit_count": 2, "last_confirmed_at": "2026-10-05T11:24:00+00:00",
  "transactions": 8}]
```
`merchant_key` ที่มี `|1234` ต่อท้ายคือกฎ transfer ที่จำแยกตามเลขท้ายปลายทาง · `transactions` = จำนวนรายการของผู้รับนี้

#### ✅ `DELETE /merchant-rules?merchant_key=...`
ลืมกฎ 1 ข้อ (`204`, `404` ถ้าไม่มี) รายการเดิมไม่เปลี่ยน มีผลเฉพาะการจัดหมวดครั้งต่อไป

### ตั้งค่า KTC

#### 🚧 `PUT /settings/ktc-password` (K3 ใน [ktc-track.md](ktc-track.md))
Request `{"password": "..."}` → เก็บแบบเข้ารหัส Fernet ใน `ledger.settings` (key `ktc_pdf_password`) ไม่มี endpoint ไหนคืนรหัสผ่านออกไป

#### 🚧 `GET /settings/ktc-password/status`
Response `{"is_set": true, "updated_at": "2026-10-05T07:00:00+00:00"}`

### รายการและรายงาน (T4.1)

#### ✅ `GET /transactions`
รายการระดับบนสุด (root) เรียงใหม่สุดก่อน แต่ละรายการมีลูกทั้งหมดซ้อนอยู่ใน `children` ตัวกรองใช้กับ root เท่านั้น ส่วนต้นไม้จะได้ครบทั้งต้นเสมอ

| Query | ชนิด | หมายเหตุ |
|---|---|---|
| `status` | `pending \| confirmed \| ignored` | |
| `account_id` | int | |
| `month` | `YYYY-MM` | ตามเวลา Asia/Bangkok |
| `limit`, `offset` | int (1–1000, ค่าเริ่มต้น 200) | แบ่งหน้าตามจำนวน root |

```json
{
  "total": 152,
  "items": [{
    "id": 140, "parent_id": null, "account_id": 1, "counter_account_id": null,
    "node_type": "payment", "kind": "expense", "status": "pending", "needs_review": false,
    "amount": 269.0, "currency": "THB", "occurred_at": "2026-10-02T17:09:57+00:00", "has_time": true,
    "source": "kbank_email", "channel": "bill_payment",
    "merchant_raw": "MEB CORPORATION .,LTD", "merchant_key": "meb corporation ltd", "description": "bill_payment",
    "to_account": null, "memory_action": "category", "receipt_id": null, "receipts": 0,
    "category_id": 21, "category_source": "ai", "ai_confidence": 0.9, "ai_reason": "...",
    "confirmed_at": null, "children": []
  }]
}
```
- `category_source`: `none` (ยังไม่มีหมวด) · `memory` · `ai` · `user` (ผู้ใช้เลือก) · `manual` (บันทึกเอง) · `account` (เป็น transfer เพราะเลขปลายทางตรงกับบัญชีผู้ใช้)
- `kind = transfer`: โอนเข้าบัญชีตัวเอง `counter_account_id` คือบัญชีปลายทาง ไม่มีหมวดและไม่นับในรายงานรายจ่าย
- `to_account`: เลขปลายทางจากอีเมล (มีบางส่วนที่ธนาคารปิดไว้) · `memory_action`: กฎของผู้รับนี้ (`category` / `transfer` / `ask` / `null`)
- `channel` มาจาก parser เช่น `bill_payment`, `promptpay`, `transfer`, `donation`
- `needs_review = true` เมื่อผลรวมของลูกไม่เท่ากับแม่

#### ✅ `GET /transactions/{id}`
รายการเดียว (ไม่มี `children`) · `404` ถ้าไม่มี

#### ✅ `PATCH /transactions/{id}`
แก้เฉพาะฟิลด์ที่ส่งมา
```json
{"category_id": 12, "description": "ค่ากาแฟประชุม", "status": "ignored"}
{"transfer_account_id": 11}
{"remember": false}
```
- `category_id`: ต้องเป็นหมวด leaf ชนิดเดียวกับรายการ (รายจ่าย/รายรับ) (เลือกหมวดหลักที่มีหมวดย่อยไม่ได้) ตั้งแล้ว `category_source` จะเป็น `user` และจะไม่ถูก AI หรือ memory ทับ ถ้ารายการ confirm แล้ว การแก้หมวดจะบันทึก Merchant Memory ทันที
- `transfer_account_id`: เปลี่ยนเป็น**โอนเข้าบัญชีตัวเอง** (`kind=transfer`, ลบหมวด) ต้องเป็นบัญชีอื่นที่มีอยู่จริง ส่งคู่กับ `category_id` ไม่ได้ และการส่ง `category_id` จะเปลี่ยนรายการ transfer กลับเป็นรายจ่าย ทั้งสองฟิลด์ใช้ได้เฉพาะรายการ leaf
- `remember`: `false` = **ไม่ต้องจำผู้รับนี้** (ลบกฎที่เคยจำของผู้รับนี้ และตั้งกฎ `ask` ให้มีผลทันทีกับทุกรายการของผู้รับนี้) · `true` = ยกเลิก
- `status`: ตั้งได้แค่ `ignored` หรือ `pending` และมีผลกับทั้งต้นไม้ รายการที่ confirm แล้วเปลี่ยนสถานะกลับไม่ได้ ส่วนการ confirm ให้ใช้ endpoint ด้านล่าง

Errors: `404` · `422` หมวดใช้ไม่ได้ หรือเปลี่ยนสถานะไม่ได้

#### ✅ `POST /transactions/confirm`
```json
{"ids": [140, 141]}
```
หรือ `{"all": true}` เพื่อ confirm ทุกรายการ pending
- confirm **ทั้งต้นไม้** ถ้าส่ง id ของลูกมา จะ confirm ทั้งต้นไม้ของ root นั้น
- รายการรายจ่าย leaf ที่ยังไม่มีหมวดจะได้ "Uncategorized"
- **Merchant Memory**: ทุก leaf ที่ confirm จะถูกจำเป็นกฎ แล้วใช้กับรายการ pending อื่นของผู้รับเดียวกันทันที เฉพาะรายการที่ `category_source` เป็น `none` หรือ `ai` (ไม่ทับที่ผู้ใช้เลือกเอง)
  - รายจ่ายที่มีหมวด (ยกเว้น Uncategorized) → กฎ `category` ต่อผู้รับ
  - transfer → กฎ `transfer` ต่อ **ผู้รับ + เลขท้าย 4 ตัวของปลายทาง** (เพราะชื่อผู้ใช้เองเป็นผู้รับในหลายบัญชี) และใช้กับรายการ pending ที่ปลายทางตรงกันเท่านั้น
  - ผู้รับที่ตั้งว่าไม่ต้องจำ (`ask`) จะไม่ถูกจำ
- ทุกขั้นตอนอยู่ใน DB transaction เดียว

```json
{"confirmed": 2, "remembered": 2, "remembered_transfers": 1, "applied_to_pending": 3}
```
Errors: `404` มี id ที่ไม่มีอยู่ (ไม่มีอะไรถูก confirm) · `422` ไม่ได้ส่ง `ids` หรือ `all`

#### ✅ `POST /transactions`
บันทึกรายจ่ายหรือรายรับเอง (เช่น เงินสด, TrueMoney, ค่าเช่าที่ได้รับ) ได้สถานะ `confirmed` ทันที (`node_type=manual`, `source=manual`) `kind` = `expense` (ค่าเริ่มต้น) หรือ `income` หมวดต้องเป็นชนิดเดียวกับ `kind` และรายรับจะไม่ถูกจำใน Merchant Memory
```json
{"account_id": 1, "amount": "45.50", "occurred_at": "2026-09-20T12:30:00+07:00", "category_id": 3,
 "merchant": "ร้านข้าวมันไก่", "description": null, "has_time": true}
```
- `occurred_at` ต้องมี timezone
- ถ้าใส่ `merchant` จะบันทึก Merchant Memory ด้วย

Response `201`: รายการที่สร้าง · `422` ยอดไม่เป็นบวก, ไม่มี timezone, ไม่มี account หรือหมวดใช้ไม่ได้

#### ✅ `GET /reports/summary?month=YYYY-MM`
สรุปรายจ่ายรายเดือนจาก view `ledger.v_reportable_expenses` (เฉพาะ leaf ที่ confirmed และเป็น expense) จึงไม่นับรายการแม่และ transfer เดือนคิดตามเวลา Asia/Bangkok
```json
{
  "month": "2026-09", "total": 1150.0, "count": 4, "pending_not_counted": 52,
  "categories": [
    {"category_id": 1, "name_th": "อาหารและเครื่องดื่ม", "name_en": "Food & Dining", "count": 3, "amount": 450.0,
     "subcategories": [{"category_id": 4, "name_th": "คาเฟ่/เบเกอรี่", "name_en": "Cafe & Bakery", "count": 3, "amount": 450.0}]}
  ]
}
```
- หมวดหลักเรียงตามยอดมากไปน้อย หมวดที่ไม่มีหมวดย่อย (เช่น บริจาค, Uncategorized) จะมี `subcategories` ว่าง
- `pending_not_counted`: จำนวนรายการ root ที่ยัง pending ในเดือนนั้น (ยังไม่นับในยอด)
- `income`: รายรับที่ confirm แล้ว แยกตามหมวด (`{"total": 8000.0, "categories": [{"category_id": 31, "name_th": "ค่าเช่า", "name_en": "Rental Income", "count": 1, "amount": 8000.0}]}`) และ `net` = รายรับ − รายจ่าย
- `transfers_out`: ยอดที่ confirm แล้วว่าโอนเข้าบัญชีตัวเอง แยกตามบัญชีปลายทาง แสดงให้เห็นว่าเงินไปไหน แต่ไม่รวมใน `total`
  ```json
  "transfers_out": [{"account_id": 10, "name": "TrueMoney", "count": 2, "amount": 1800.0}]
  ```

---

## gmail-service (`localhost:8000`)

OAuth scope คือ `gmail.modify` อ่านได้และย้ายไปถังขยะได้ แต่ลบถาวรไม่ได้

#### ✅ `GET /api/emails`
| Query | ชนิด | หมายเหตุ |
|---|---|---|
| `labels` | string (ส่งซ้ำได้) | หลาย label = OR |
| `date_after`, `date_before` | `YYYY/MM/DD` | ตามรูปแบบ Gmail search |
| `search_text` | string | ต่อท้าย query ตรงๆ เช่น `from:x@y.com` |
| `max_results` | int 1–100 | ค่าเริ่มต้น 10 |
| `page_token` | string | `next_page_token` จาก response ก่อนหน้า |

Response
```json
{"query_used": "label:financial-kasikorn", "total_fetched": 1, "next_page_token": "0190...",
 "emails": [{"id": "1a0f...", "snippet": "...", "subject": "...", "sender": "K PLUS <KPLUS@kasikornbank.com>", "date": "Sat, 03 Oct 2026 10:52:03 +0700"}]}
```
ถ้ามี env `ALLOWED_LABELS` label ที่ไม่อยู่ในรายการจะได้ `400`

#### ✅ `GET /api/messages/{message_id}`
```json
{"id": "1a0f...", "thread_id": "1a0f...", "subject": "...", "sender": "...", "date": "...",
 "internal_date": "1790999523000", "label_ids": ["Label_123", "INBOX"],
 "text_body": "...", "html_body": "",
 "attachments": [{"attachment_id": "ANGj...", "filename": "statement.pdf", "mime_type": "application/pdf", "size": 182345}]}
```
`internal_date` เป็น epoch milliseconds ในรูป string

#### ✅ `GET /api/messages/{message_id}/attachments/{attachment_id}`
คืน bytes ของไฟล์แนบ (`application/octet-stream`)

#### ✅ `DELETE /api/messages/{message_id}`
ย้ายไปถังขยะ (กู้คืนได้ใน 30 วัน) คืน `{"id": "...", "label_ids": ["TRASH", ...]}`

Errors (ทุก endpoint ของ message): `400`/`404` id ผิด · `403` scope ไม่พอ (token เก่าที่เป็น readonly) · `500` Gmail หรือ token มีปัญหา (Gmail ตอบ 500 เป็นช่วงๆ เมื่อเรียกจำนวนมาก ผู้เรียกควร retry)

---

## credit-card-statement-service (`localhost:8003`)

#### ✅ `POST /upload-statement/`
multipart `file` เป็น CSV ที่มีคอลัมน์ `Date`, `Description` และ `Amount` คืน `[{"date": "...", "description": "...", "amount": 0.0}]` (endpoint เดิมก่อนโปรเจกต์นี้)

#### ✅ `POST /parse-ktc-pdf` (K1 ใน [ktc-track.md](ktc-track.md))
multipart: `file` (PDF ไม่เกิน 10 MB) และ `password` ปลดล็อกและอ่านในหน่วยความจำ ไม่เขียน PDF ที่ปลดล็อกแล้วลงดิสก์

Response
```json
{
  "statement": {"statement_date": "2026-09-25", "due_date": "2026-10-10", "card_masked": "4xxx-xxxx-xxxx-1234",
                "card_last4": "1234", "previous_balance": "5000.00", "total_new_balance": "3200.00", "minimum_payment": "320.00"},
  "lines": [
    {"transaction_date": "2026-09-01", "posting_date": "2026-09-02", "description": "SHOPEE", "amount": "1200.00",
     "approval_code": "123456", "type": "purchase"},
    {"transaction_date": "2026-09-05", "posting_date": "2026-09-05", "description": "PAYMENT - THANK YOU", "amount": "-5000.00",
     "approval_code": null, "type": "payment"}
  ]
}
```
- `amount` มีเครื่องหมาย: payment และ refund ติดลบ
- `type`: `purchase | payment | refund | fee | interest | other`
- endpoint ตรวจว่า `previous_balance` + ผลรวม `amount` ของทุก line = `total_new_balance` ถ้าไม่ตรงคืน `422`

Errors: `401 {"code": "wrong_password"}` · `413 {"code": "file_too_large"}` · `422 {"code": "..."}` อ่าน PDF ไม่ได้หรือ layout เปลี่ยน

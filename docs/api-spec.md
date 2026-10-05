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
- เงินเป็น decimal ส่งใน JSON เป็น string หรือ number ทศนิยม 2 ตำแหน่ง สกุลเงินเริ่มต้นคือ `THB`
- เวลาเป็น ISO 8601 พร้อม timezone; `occurred_at` ของรายการเป็นเวลา Asia/Bangkok
- error ใช้รูปแบบของ FastAPI: `{"detail": "..."}` หรือ `{"detail": {"code": "..."}}`

---

## expense-api (`localhost:8002`)

### ข้อมูลอ้างอิง

#### ✅ `GET /accounts`
บัญชีทั้งหมดใน ledger

```json
[
  {"id": 1, "name": "KBank ออมทรัพย์", "type": "bank", "institution": "KBank",
   "masked_ref": null, "currency": "THB", "is_active": true},
  {"id": 2, "name": "บัตรเครดิต KTC", "type": "credit_card", "institution": "KTC",
   "masked_ref": null, "currency": "THB", "is_active": true}
]
```
`type`: `bank | credit_card | ewallet | cash`

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
2. **Merchant Memory**: ถ้า `merchant_key` ตรงกับที่ผู้ใช้เคย confirm จะได้ `category_source='memory'`
3. **AI** (Gemini): ส่งทีละไม่เกิน 25 รายการ ถ้า confidence ≥ `AI_CONFIDENCE_THRESHOLD` (ค่าเริ่มต้น 0.7) และเป็นหมวด leaf ที่มีอยู่จริง จะได้หมวดนั้น ไม่งั้นได้ "Uncategorized" ทั้งสองกรณีเป็น `category_source='ai'` และเก็บ `ai_confidence`, `ai_reason`
4. ถ้าไม่ได้ตั้ง AI ไว้ (ไม่มี `GEMINI_API_KEY` และไม่ได้เปิด Vertex) หรือ AI ล้ม รายการจะคงเป็น `none` และถูกลองใหม่ในการเรียกครั้งถัดไป

Response
```json
{"memory": 12, "ai": 30, "uncategorized": 5, "left": 0}
```
| ฟิลด์ | ความหมาย |
|---|---|
| `memory` | ได้หมวดจาก Merchant Memory |
| `ai` | AI มั่นใจพอ ได้หมวดที่ AI เลือก |
| `uncategorized` | AI ไม่มั่นใจ หรือตอบหมวดที่ใช้ไม่ได้ จึงได้ Uncategorized |
| `left` | ยังไม่มีหมวด (ไม่มี AI หรือ AI ล้ม) |
| `error` | มีเมื่อ AI ล้ม เช่น `"ai: ClientError"` (มีแค่ชื่อ exception เพราะข้อความอาจมีชื่อร้านและยอดเงิน) |

ข้อมูลที่ส่งไปให้ Gemini มีแค่ id, ชื่อร้าน/ผู้รับ, ช่องทาง, ยอดเงิน และคำอธิบาย ไม่มีเลขบัญชี

### ตั้งค่า KTC

#### 🚧 `PUT /settings/ktc-password` (K3 ใน [ktc-track.md](ktc-track.md))
Request `{"password": "..."}` → เก็บแบบเข้ารหัส Fernet ใน `ledger.settings` (key `ktc_pdf_password`) ไม่มี endpoint ไหนคืนรหัสผ่านออกไป

#### 🚧 `GET /settings/ktc-password/status`
Response `{"is_set": true, "updated_at": "2026-10-05T07:00:00+00:00"}`

### รายการและรายงาน (T4.1)

#### 📝 `GET /transactions?status=pending`
คืนรายการเป็น tree (แม่ → ลูก) กรองตาม `status` (`pending | confirmed | ignored`)

#### 📝 `PATCH /transactions/{id}`
แก้หมวด, ร้านค้า หรือคำอธิบายของรายการ การแก้หมวดจะตั้ง `category_source='user'`

#### 📝 `POST /transactions/confirm`
Request `{"ids": [1, 2]}` หรือ `{"all": true}` confirm ทั้งต้นไม้ และบันทึก Merchant Memory ใน DB transaction เดียวกัน

#### 📝 `POST /transactions`
สร้างรายการเอง: `amount`, `occurred_at`, `category_id` และ `account_id` บังคับ ได้ `status=confirmed` ทันที

#### 📝 `GET /reports/summary?month=YYYY-MM`
สรุปรายจ่ายรายเดือนจาก view `ledger.v_reportable_expenses` (เฉพาะ leaf ที่ confirmed และ kind=expense)

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

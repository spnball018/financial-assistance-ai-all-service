# MCP server สำหรับ Claude

expense-api เปิด MCP server ให้ Claude (claude.ai บนเว็บและแอปมือถือ) อ่านข้อมูลการเงินของผู้ใช้ได้ **อ่านอย่างเดียว ไม่มี tool ที่แก้ไขข้อมูล**

| | |
|---|---|
| URL | `https://<DOMAIN>/mcp` (= `PUBLIC_BASE_URL` + `/mcp`) |
| Transport | Streamable HTTP แบบ stateless ตอบเป็น JSON (ไม่ใช้ SSE ยาวๆ จึงผ่าน Cloudflare tunnel ได้) |
| เข้าสู่ระบบ | OAuth 2.1 + PKCE (S256) ผ่าน Google sign-in ของเว็บ scope `read` |
| โค้ด | `expense-api/app/mcp_server.py` (server และ tool), `app/oauth.py` (OAuth), `app/routers/oauth.py` (หน้าอนุญาต) |
| SDK | MCP Python SDK v2 (`mcp>=2.3,<3`, class `MCPServer`) |

**สิ่งที่ server นี้มีให้:**

| ประเภท | จำนวน |
|---|---|
| Tools | 14 (ทั้งหมด `readOnlyHint: true`) |
| Prompts | ไม่มี |
| Resources | ไม่มี |
| Server instructions | มี (ดูด้านล่าง) |

## เชื่อมต่อ

1. claude.ai → Settings → Connectors → **Add custom connector** แล้วใส่ URL ข้างบน (คัดลอกได้จากหน้าตั้งค่า → การเข้าถึง → เชื่อมต่อ Claude (MCP))
2. Claude เปิดหน้าเข้าสู่ระบบ: เข้าด้วย Google (บัญชีที่เป็นผู้ใช้ของระบบ) แล้วกด **อนุญาต**
3. ถามได้เลย เช่น "สรุปรายจ่ายเดือนนี้", "เดือนที่แล้วจ่ายร้านกาแฟไปเท่าไหร่", "ยอดในบัญชี KBank เหลือเท่าไหร่"

ยกเลิกได้ที่หน้าตั้งค่า → การเข้าถึง → ปุ่ม "ยกเลิก" ของการเชื่อมต่อนั้น (token ทุกตัวของการอนุญาตนั้นใช้ไม่ได้ทันที)

## ความปลอดภัย

- **ข้อมูลของใครของมัน:** ทุก tool อ่าน user id จาก access token เท่านั้น ไม่มี tool ที่รับ user id จากผู้เรียก ทุก query กรองด้วย `user_id` (ทดสอบใน `tests/test_mcp.py`: ผู้ใช้อีกคนขอรายการหรือบัญชีของเราด้วย id ได้ "not found" และไม่เห็นข้อมูลในคำตอบ)
- **token แยกจาก REST API:** access token `mcp_at_…` (1 ชั่วโมง) และ refresh token `mcp_rt_…` (60 วัน หมุนทุกครั้งที่ refresh) เปิดได้แค่ `/mcp` เรียก REST API ไม่ได้ ส่วน API token `fat_…` เปิด `/mcp` ไม่ได้ DB เก็บแค่ sha256
- **ใครลงทะเบียนได้:** client ลงทะเบียนเองได้ (RFC 7591) แต่ redirect URI ต้องอยู่บน `https://claude.ai` / `https://claude.com` หรือ `http://localhost` (สำหรับทดสอบ) เท่านั้น
- **หน้าอนุญาต:** ต้องเข้าสู่ระบบด้วย Google ก่อน และฟอร์มมี CSRF token เว็บอื่นจึงกดอนุญาตแทนไม่ได้
- ปิดการใช้งานผู้ใช้แล้ว token ของคนนั้นใช้ไม่ได้ทันที

## Server instructions

ข้อความที่ส่งให้ Claude ตอนเริ่มเชื่อมต่อ เพื่อให้ตีความข้อมูลถูก:

- ผู้ใช้อยู่ประเทศไทย ยอดเป็นบาท เวลาเป็นเวลาไทย
- ข้อมูลมาจากอีเมลธนาคาร (K PLUS), บิลบัตร (KTC), รูปหน้าจอ e-wallet และการบันทึกเอง
- รายจ่าย = เฉพาะรายการที่ยืนยันแล้ว รายการ `pending` ยังรอตรวจและยังไม่นับ
- การโอนระหว่างบัญชีตัวเอง (ธนาคาร → e-wallet, ชำระบัตร) ไม่ใช่รายจ่าย
- รายการหนึ่งอาจแยกเป็นรายการย่อย (สินค้าในคำสั่งซื้อ, บรรทัดในใบเสร็จ) หมวดอยู่ที่รายการย่อย
- เดือนเป็นรูปแบบ `YYYY-MM` ชื่อหมวดและร้านมักเป็นภาษาไทย ให้ตอบตามภาษาที่ผู้ใช้ใช้
- connector นี้อ่านได้อย่างเดียว

## Tools

ทุก tool เป็น `readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false` ผลลัพธ์เป็น JSON ยอดเงินเป็นตัวเลข (บาท) เวลาเป็น ISO เวลาไทย (`2026-11-10T12:30+07:00`) ถ้า `month` ผิดรูปแบบหรือหา id ไม่เจอ tool จะตอบเป็น error

### สรุปและรายงาน

#### `monthly_summary(month)`
สรุปรายจ่ายของเดือนแยกตามหมวด (มีหมวดย่อย) รายรับแยกตามหมวด ยอดสุทธิ เงินที่โอนเข้าบัญชีตัวเอง (ไม่นับเป็นรายจ่าย) และจำนวนรายการที่ยังรอตรวจ

| พารามิเตอร์ | ชนิด | |
|---|---|---|
| `month` | string | `YYYY-MM` จำเป็น |

ผลลัพธ์: `month`, `total`, `count`, `pending_not_counted`, `categories[]` (`category_id`, `name_th`, `name_en`, `count`, `amount`, `subcategories[]`), `transfers_out[]` (`account_id`, `name`, `count`, `amount`), `income` (`total`, `categories[]`), `net`

#### `category_expenses(month, category_id)`
รายจ่ายที่ยืนยันแล้วเบื้องหลังหมวดหนึ่งใน `monthly_summary` (หมวดหลักรวมหมวดย่อย) เรียงยอดมากไปน้อย

| พารามิเตอร์ | ชนิด | |
|---|---|---|
| `month` | string | `YYYY-MM` |
| `category_id` | int | จาก `monthly_summary` หรือ `categories` |

ผลลัพธ์: `items[]`: `id`, `occurred_at`, `has_time`, `amount`, `merchant_raw`, `description`, `node_type`, `note`, `category_id`, `category_th`, `account_name`, `path` (รายการแม่ที่แยกออกมา)

#### `income(month)`
รายรับที่ยืนยันแล้วของเดือน เรียงยอดมากไปน้อย

ผลลัพธ์: `items[]`: `id`, `occurred_at`, `has_time`, `amount`, `merchant_raw`, `description`, `node_type`, `note`, `source`, `category_id`, `category_th`, `account_name`

### รายการ

#### `search_transactions(month?, status?, account_id?, text_contains?, limit=50)`
รายการใหม่สุดก่อน พร้อมรายการย่อยของรายการที่แยก ตัวกรองทุกตัวไม่บังคับ

| พารามิเตอร์ | ชนิด | |
|---|---|---|
| `month` | string | `YYYY-MM` |
| `status` | `pending` \| `confirmed` \| `ignored` | |
| `account_id` | int | จาก `accounts` |
| `text_contains` | string | ค้นในชื่อผู้รับ รายละเอียด และโน้ต รวมรายการย่อย (ไม่สนตัวพิมพ์เล็กใหญ่) |
| `limit` | int | 1–100 |

ผลลัพธ์: `total` (จำนวนทั้งหมดที่ตรงเงื่อนไข), `items[]`: `id`, `when`, `amount`, `kind` (`expense` \| `income` \| `transfer`), `status`, `merchant`, `description`, `account`, `category`, `transfer_to` (ถ้าเป็นการโอน), `note`, `items[]` (รายการย่อย รูปแบบเดียวกัน)

#### `pending_review(limit=50)`
รายการที่รอผู้ใช้ตรวจและยืนยัน (ยังไม่นับในรายจ่าย) ใหม่สุดก่อน ผลลัพธ์เหมือน `search_transactions`

#### `transaction_details(transaction_id)`
ข้อมูลทั้งหมดของรายการหนึ่ง: ช่องที่ parser อ่านได้ (`raw`: บัญชีต้นทาง/ปลายทาง ค่าธรรมเนียม ยอดคงเหลือที่ธนาคารแจ้ง), หมวดและที่มาของหมวด (`category_source`, `ai_reason`), รายการแม่ (`ancestors`), หัวเรื่องและผู้ส่งของอีเมลต้นทาง (`email`, **ไม่มีเนื้ออีเมล**) และคำสั่งซื้อที่จับคู่ (`orders`)

### บัญชีและยอดคงเหลือ

#### `accounts()`
บัญชีของผู้ใช้ (ธนาคาร บัตรเครดิต e-wallet เงินสด การลงทุน) พร้อม id สำหรับ tool อื่น
ผลลัพธ์: `items[]`: `id`, `name`, `type`, `institution`, `is_active`

#### `account_balances()`
ยอดคงเหลือทุกบัญชี
ผลลัพธ์: `items[]`: `account_id`, `name`, `type`, `is_active`, `balance` (ยืนยันแล้ว), `balance_with_pending`, `pending_count`, `last_movement_at`, `opening` (ยอดยกมา), `bank_reported` (ยอดล่าสุดที่ K PLUS แจ้ง เทียบกับที่คำนวณได้)

#### `account_ledger(account_id, limit=50)`
ความเคลื่อนไหวของบัญชีเดียว ใหม่สุดก่อน พร้อมยอดคงเหลือหลังแต่ละรายการ

| พารามิเตอร์ | ชนิด | |
|---|---|---|
| `account_id` | int | จาก `accounts` |
| `limit` | int | 1–200 |

ผลลัพธ์: `id`, `name`, `type`, `is_active`, `opening`, `total_rows`, `rows[]`: `transaction_id`, `occurred_at`, `kind`, `status`, `amount` (+ เข้า / − ออก), `counted`, `merchant_raw`, `description`, `note`, `category_th`, `other_account`, `balance`, `balance_with_pending`, `bank_balance`, `difference`, `gap_starts`

#### `fund_portfolio()`
บัญชีการลงทุน: เงินสด กองทุนพร้อมจำนวนหน่วย ต้นทุนเฉลี่ย มูลค่าตลาด กำไร/ขาดทุน และยอดรวม
ผลลัพธ์: `accounts[]` (รายละเอียดตาม `GET /funds/portfolio` ใน [api-spec.md](api-spec.md))

#### `fund_trades(account_id, fund_code?, limit=100)`
รายการซื้อขายกองทุนของบัญชีการลงทุนหนึ่ง ใหม่สุดก่อน พร้อมที่มาของเงิน (ซื้อ) หรือปลายทาง (ขาย) และกองทุนอีกฝั่งของการสับเปลี่ยน

| พารามิเตอร์ | ชนิด | |
|---|---|---|
| `account_id` | int | บัญชีประเภท investment จาก `accounts` หรือ `fund_portfolio` |
| `fund_code` | string | เฉพาะกองนี้ (ไม่สนตัวพิมพ์เล็กใหญ่) ไม่บังคับ |
| `limit` | int | 1–500 |

ผลลัพธ์: `account_id`, `name`, `total` (จำนวนรายการที่ตรงเงื่อนไข), `trades[]`: `id`, `trade_date`, `fund_code`, `side` (`buy` \| `sell`), `units`, `amount`, `nav`, `funding` (`new_transfer` \| `linked_transfer` \| `held_before` \| `switch`), `other_account`, `switch_fund_code`, `transfer_status`, `note` (ช่องที่ว่างจะไม่ส่งมา), `unlinked[]` (เงินโอนเข้า/ออกบัญชีที่ยังไม่ผูกกับรายการซื้อขาย; `amount` บวก = เข้า, ลบ = ออก) บัญชีที่ไม่ใช่ investment หรือไม่ใช่ของผู้ใช้ตอบเป็น error

```json
{"account_id": 15, "name": "KAsset", "total": 2,
 "trades": [{"id": 3, "trade_date": "2026-10-20", "fund_code": "SCBSET50", "side": "sell", "units": 300.0,
             "amount": 2550.0, "nav": 8.5, "funding": "new_transfer", "other_account": "KBank ใช้จ่าย",
             "transfer_status": "confirmed"},
            {"id": 1, "trade_date": "2026-08-15", "fund_code": "SCBSET50", "side": "buy", "units": 1000.0,
             "amount": 8000.0, "nav": 8.0, "funding": "new_transfer", "other_account": "KBank ใช้จ่าย",
             "transfer_status": "confirmed", "note": "DCA"}],
 "unlinked": []}
```

#### `fund_nav_history(fund_code, since?, interval="auto")`
NAV รายวันของกองทุนหนึ่งที่เก็บไว้ (ดึงจาก Finnomena ทุกวัน ย้อนหลังเท่าที่ Finnomena มี) เรียงเก่าไปใหม่ พร้อมสถิติของช่วงนั้น ใช้ดูผลตอบแทน ความผันผวน และเทียบกองทุน

| พารามิเตอร์ | ชนิด | |
|---|---|---|
| `fund_code` | string | เช่น `K-USXNDQ-A(A)` (ไม่สนตัวพิมพ์เล็กใหญ่) จาก `fund_portfolio` |
| `since` | string | `YYYY-MM-DD` ไม่บังคับ |
| `interval` | `auto` \| `day` \| `week` \| `month` | `auto` = รายวันถ้าไม่เกิน 400 จุด ไม่งั้นรายสัปดาห์ ไม่งั้นรายเดือน |

ผลลัพธ์: `code`, `interval` (ที่ใช้จริง), `points[]` (`date`, `nav`), `stats` (`from`, `to`, `first`, `last`, `change`, `change_pct`, `high`, `low`, `max_drawdown_pct`, `max_drawdown_at`) สถิติคิดจากข้อมูลรายวันเต็ม ไม่ใช่จุดที่ย่อแล้ว

#### `portfolio_value_history(account_id, since?, interval="auto")`
มูลค่าของบัญชีกองทุนหนึ่งตามเวลา (หน่วยที่ถือวันนั้น × NAV วันนั้น) คู่กับต้นทุน ตั้งแต่รายการซื้อแรก ใช้ดูว่าพอร์ตโตหรือลดเท่าไหร่ และช่วงไหนขาดทุน

| พารามิเตอร์ | ชนิด | |
|---|---|---|
| `account_id` | int | บัญชีประเภท investment จาก `accounts` หรือ `fund_portfolio` |
| `since` | string | `YYYY-MM-DD` ไม่บังคับ |
| `interval` | `auto` \| `day` \| `week` \| `month` | เหมือน `fund_nav_history` |

ผลลัพธ์: `account_id`, `name`, `funds[]`, `interval`, `points[]` (`date`, `value`, `cost`, `by_fund` มูลค่าแยกตามรหัส), `stats` (ของ `value`; การขายหน่วยออกนับเป็นมูลค่าที่ลดลงด้วย) บัญชีที่ไม่ใช่ investment หรือไม่ใช่ของผู้ใช้ตอบเป็น error

### ข้อมูลอ้างอิง

#### `categories()`
หมวดรายจ่ายและรายรับ (ชื่อไทยและอังกฤษ) พร้อม id หมวดหลักมาก่อนหมวดย่อย หมวดเป็นของกลาง ใช้ร่วมทุกผู้ใช้
ผลลัพธ์: `items[]`: `id`, `parent_id`, `name_th`, `name_en`, `kind`

## Prompts และ Resources

ยังไม่มีทั้งสองอย่าง Claude ใช้แค่ tools ข้างบน ถ้าจะเพิ่ม:

- **Prompts** (ผู้ใช้เลือกจากเมนูใน Claude): เช่น "สรุปเดือนนี้เทียบเดือนที่แล้ว" หรือ "ช่วยตรวจรายการที่รอตรวจ" เพิ่มด้วย `@server.prompt()` ใน `build()`
- **Resources** (ข้อมูลที่ผู้ใช้แนบเข้าแชตได้): เช่น `expense://categories` หรือ `expense://summary/{month}` เพิ่มด้วย `@server.resource(...)` ข้อมูลที่เป็นของผู้ใช้ต้องอ่าน user id จาก token แบบเดียวกับ tool

ทุกอย่างที่เพิ่มต้องอ่านอย่างเดียว (`tests/test_mcp.py` ตรวจว่าทุก tool เป็น `readOnlyHint`)

## เพิ่ม tool ใหม่

1. เพิ่มใน `build()` ของ `app/mcp_server.py` ด้วย `@server.tool(annotations=READ_ONLY)` เป็น `async` และอ่าน DB ผ่าน `_read(lambda conn, uid: …)` ซึ่งดึง user id จาก token และรันใน worker thread
2. ใช้ฟังก์ชันเดียวกับ REST API (`ledger/`, `funds/`) ที่รับ `uid` และกรองด้วย `user_id` เสมอ
3. ตัดผลลัพธ์ให้เหลือเท่าที่ต้องใช้ตอบคำถาม (ไม่ส่ง JSON ดิบของ parser หรือเนื้ออีเมล)
4. เพิ่ม test ใน `tests/test_mcp.py` รวมกรณีผู้ใช้อีกคนเรียก แล้วอัปเดตเอกสารนี้

## ทดสอบ

- `tests/test_mcp.py`: OAuth ทั้ง flow, PKCE ผิด, code ใช้ซ้ำ, ฟอร์มปลอม, หมุน refresh token, ยกเลิกการเชื่อมต่อ, แยกข้อมูลระหว่างผู้ใช้, tool ทุกตัวอ่านอย่างเดียว
- ทดสอบด้วยมือ: `npx @modelcontextprotocol/inspector` ชี้ไปที่ `http://localhost:3000/mcp` (expense-api + `npm run dev`)
- ถ้า Cloudflare บล็อก server ของ Anthropic (Bot Fight Mode / WAF) ให้ตั้ง WAF rule ข้าม `/mcp`, `/authorize`, `/token`, `/register`, `/revoke`, `/.well-known/*`

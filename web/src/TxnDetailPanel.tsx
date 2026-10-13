import { useEffect, useState } from "react";
import { api } from "./api";
import { CategorySelect, type Choice } from "./CategorySelect";
import { CHANNEL_LABEL, fmtMoney, fmtWhen } from "./format";
import type { Account, Category, TxnDetails } from "./types";

const STATUS_LABEL: Record<string, string> = { pending: "รอตรวจ", confirmed: "ยืนยันแล้ว", ignored: "ไม่นับ" };
const SOURCE_LABEL: Record<string, string> = {
  kbank_email: "อีเมล K PLUS",
  ktc_pdf: "บิลบัตร KTC",
  shopee_email: "อีเมล Shopee",
  lazada_email: "อีเมล Lazada",
  wallet_screenshot: "รูปหน้าจอ wallet",
  manual: "บันทึกเอง",
};
const CATEGORY_SOURCE_LABEL: Record<string, string> = {
  memory: "จากกฎที่จำไว้",
  ai: "AI เดา",
  user: "เลือกเอง",
  manual: "บันทึกเอง",
  account: "บัญชีปลายทาง",
  none: "",
};
// what the parsers put in transactions.raw, in the order worth showing
const RAW_LABEL: Record<string, string> = {
  from_account: "จากบัญชี",
  to_account: "บัญชีปลายทาง",
  fee: "ค่าธรรมเนียม",
  balance_after: "ยอดคงเหลือหลังรายการ",
  transaction_date: "วันที่ใช้บัตร",
  posting_date: "วันที่ลงบัญชี",
  statement_date: "วันที่สรุปยอด",
  due_date: "วันครบกำหนดชำระ",
};

// the email runs in a sandboxed iframe: no scripts, no forms, no remote images (trackers)
const EMAIL_CSP =
  "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:\">";

/** "2026-10-05T14:30:00+07:00" -> date and time as shown in Thailand. */
function bangkokParts(iso: string): { date: string; time: string } {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Bangkok", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(new Date(iso)).map((x) => [x.type, x.value]),
  );
  return { date: `${p.year}-${p.month}-${p.day}`, time: `${p.hour}:${p.minute}` };
}

type Draft = { date: string; time: string; hasTime: boolean; amount: string; accountId: number; merchant: string; description: string };

export function TxnDetailPanel({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged?: () => void }) {
  const [d, setD] = useState<TxnDetails | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [note, setNote] = useState("");
  const [noteSaved, setNoteSaved] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);

  const load = () =>
    api.txnDetails(id).then((x) => {
      setD(x);
      setNote(x.note ?? "");
    }, (e: Error) => setError(e.message));
  useEffect(() => {
    setD(null);
    setEmail(null);
    setDraft(null);
    setNoteSaved(false);
    load();
  }, [id]);
  useEffect(() => {
    Promise.all([api.categories(), api.accounts()]).then(([c, a]) => {
      setCategories(c);
      setAccounts(a);
    }, (e: Error) => setError(e.message));
  }, []);

  // a category belongs to the leaves: a split payment is categorized through its items
  const categorizable = !!d && !d.has_children && ["expense", "transfer", "income"].includes(d.kind);
  const setCategory = async (choice: Choice) => {
    setError(null);
    try {
      await api.patch(id, choice);
      await load();
      onChanged?.();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const saveNote = async () => {
    if (!d || note.trim() === (d.note ?? "")) return;
    setError(null);
    try {
      const t = await api.patch(id, { note: note.trim() || null });
      setD({ ...d, note: t.note });
      setNoteSaved(true);
      onChanged?.();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  // only what the user typed in can be edited; rows from email stay as the bank reported them
  const manual = d?.source === "manual" && d.node_type === "manual";
  const startEdit = async () => {
    if (!d) return;
    const { date, time } = bangkokParts(d.occurred_at);
    setDraft({ date, time, hasTime: d.has_time, amount: String(d.amount), accountId: d.account_id,
               merchant: d.merchant_raw ?? "", description: d.description ?? "" });
  };
  const saveEdit = async () => {
    if (!draft) return;
    setSaving(true);
    setError(null);
    try {
      await api.patch(id, {
        occurred_at: `${draft.date}T${draft.hasTime ? draft.time : "00:00"}:00+07:00`,
        has_time: draft.hasTime,
        amount: draft.amount,
        account_id: draft.accountId,
        merchant: draft.merchant.trim() || null,
        description: draft.description.trim() || null,
      });
      setDraft(null);
      await load();
      onChanged?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };
  const remove = async () => {
    if (!window.confirm("ลบรายการที่บันทึกเองนี้? ลบแล้วกู้คืนไม่ได้")) return;
    try {
      await api.deleteTransaction(id);
      onChanged?.();
      onClose();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const showEmail = async () => {
    try {
      setEmail(EMAIL_CSP + (await api.txnEmail(id)));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const rows: [string, React.ReactNode][] = [];
  if (d) {
    rows.push(["เวลา", fmtWhen(d.occurred_at, d.has_time)]);
    rows.push(["บัญชี", d.account_name]);
    if (d.counter_account_name) rows.push(["โอนไปบัญชี", d.counter_account_name]);
    rows.push(["ประเภท", CHANNEL_LABEL[d.channel ?? ""] ?? CHANNEL_LABEL[d.node_type] ?? d.channel ?? d.node_type]);
    if (d.description && d.description !== d.channel) rows.push(["รายละเอียด", d.description]);
    rows.push(["สถานะ", STATUS_LABEL[d.status] ?? d.status]);
    if (d.kind === "expense" || d.kind === "transfer" || d.kind === "income")
      rows.push([
        d.kind === "transfer" ? "โอนไป / หมวด" : "หมวด",
        <>
          {categorizable && categories.length > 0 ? (
            d.kind === "income" ? (
              <select value={d.category_id ?? ""} onChange={(e) => setCategory({ category_id: Number(e.target.value) })}>
                <option value="" disabled>
                  — เลือกหมวดรายรับ —
                </option>
                {categories.filter((c) => c.kind === "income").map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name_th}
                  </option>
                ))}
              </select>
            ) : (
              <CategorySelect
                categories={categories}
                accounts={accounts}
                fromAccountId={d.account_id}
                categoryId={d.category_id}
                transferAccountId={d.kind === "transfer" ? d.counter_account_id : null}
                onChange={setCategory}
              />
            )
          ) : d.kind === "transfer" ? (
            d.counter_account_name
          ) : (
            d.category_th ?? (d.has_children ? "เลือกหมวดที่รายการย่อย" : "ยังไม่มี")
          )}
          {CATEGORY_SOURCE_LABEL[d.category_source] && <span className="muted small"> · {CATEGORY_SOURCE_LABEL[d.category_source]}</span>}
          {d.ai_reason && <div className="muted small">{d.ai_reason}</div>}
        </>,
      ]);
    for (const [k, label] of Object.entries(RAW_LABEL)) {
      const v = d.raw?.[k];
      if (v !== null && v !== undefined && v !== "") rows.push([label, String(v)]);
    }
    if (d.source_ref) rows.push(["เลขที่รายการ", d.source_ref]);
    rows.push(["ที่มา", SOURCE_LABEL[d.source] ?? d.source]);
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="panel" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="panel-head">
          <div>
            <div className="merchant">{d?.merchant_raw ?? "…"}</div>
            {d && d.ancestors.length > 0 && (
              <div className="muted small">อยู่ใต้ {d.ancestors.map((a) => a.merchant_raw ?? a.node_type).join(" › ")}</div>
            )}
          </div>
          <div className="spacer" />
          {d && <div className="total num">{fmtMoney(d.amount)}</div>}
          <button className="link" onClick={onClose} aria-label="ปิด">
            ✕
          </button>
        </div>
        {error && <div className="message error">{error}</div>}
        {manual && !draft && (
          <div className="toolbar">
            <button onClick={startEdit}>แก้ไข</button>
            <button className="link danger" onClick={remove}>
              ลบ
            </button>
          </div>
        )}
        {draft && (
          <div className="form-grid edit-manual">
            <label>
              วันที่
              <input type="date" value={draft.date} onChange={(e) => setDraft({ ...draft, date: e.target.value })} />
            </label>
            <label>
              เวลา
              <span className="inline">
                <input type="time" value={draft.time} disabled={!draft.hasTime}
                  onChange={(e) => setDraft({ ...draft, time: e.target.value })} />
                <span className="small muted">
                  <input type="checkbox" checked={!draft.hasTime}
                    onChange={(e) => setDraft({ ...draft, hasTime: !e.target.checked })} /> ไม่ระบุเวลา
                </span>
              </span>
            </label>
            <label>
              จำนวนเงิน (บาท)
              <input inputMode="decimal" value={draft.amount}
                onChange={(e) => setDraft({ ...draft, amount: e.target.value.replace(/[^\d.]/g, "") })} />
            </label>
            <label>
              {d?.kind === "transfer" ? "จากบัญชี" : "บัญชี"}
              <select value={draft.accountId} onChange={(e) => setDraft({ ...draft, accountId: Number(e.target.value) })}>
                {accounts.filter((a) => a.is_active || a.id === draft.accountId).map((a) => (
                  <option key={a.id} value={a.id} disabled={d?.kind === "transfer" && a.id === d.counter_account_id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {d?.kind === "income" ? "ผู้จ่าย" : d?.kind === "transfer" ? "รายละเอียด" : "ร้าน / ผู้รับ"}
              <input value={draft.merchant} onChange={(e) => setDraft({ ...draft, merchant: e.target.value })} />
            </label>
            <label>
              บันทึกเพิ่มเติม
              <input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
            </label>
            <div className="full actions">
              <button className="primary" disabled={saving || !(Number(draft.amount) > 0) || !draft.date} onClick={saveEdit}>
                {saving ? "กำลังบันทึก…" : "บันทึก"}
              </button>
              <button className="link" onClick={() => setDraft(null)}>
                ยกเลิก
              </button>
              <span className="muted small">
                {d?.kind === "transfer" ? "บัญชีปลายทาง" : "หมวด"}เปลี่ยนได้ที่ dropdown ในหน้าตรวจรายการ
              </span>
            </div>
          </div>
        )}
        {d && !draft && (
          <>
            <label className="note-field">
              <span className="small muted">โน้ต</span>
              <textarea
                rows={2}
                value={note}
                placeholder="จดอะไรไว้กับรายการนี้ เช่น ซื้อให้ใคร ใช้ทำอะไร"
                onChange={(e) => {
                  setNote(e.target.value);
                  setNoteSaved(false);
                }}
                onBlur={saveNote}
              />
              {note.trim() !== (d.note ?? "") ? (
                <button className="link small" onClick={saveNote}>
                  บันทึกโน้ต
                </button>
              ) : (
                noteSaved && <span className="small muted">บันทึกแล้ว</span>
              )}
            </label>
            <dl className="details">
              {rows.map(([k, v]) => (
                <div key={k}>
                  <dt>{k}</dt>
                  <dd>{v}</dd>
                </div>
              ))}
            </dl>
            {d.orders.length > 0 && (
              <>
                <h2>คำสั่งซื้อ</h2>
                <ul className="subs">
                  {d.orders.map((o) => (
                    <li key={`${o.platform}:${o.order_id}`}>
                      <span>
                        {o.platform === "shopee" ? "Shopee" : "Lazada"} #{o.order_id}
                        <span className="muted small">
                          {" "}
                          · {(o.sellers ?? []).join(", ")} · สั่ง {fmtWhen(o.ordered_at, true)}
                          {o.payment_method && ` · ${o.payment_method}`}
                        </span>
                      </span>
                      <span className="num">{fmtMoney(o.total_amount)}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
            {typeof d.raw?.screenshot_id === "number" && (
              <>
                <h2>รูปหน้าจอต้นฉบับ</h2>
                <img className="screenshot" src={`/api/wallet-screenshots/${d.raw.screenshot_id}/image`} alt="รูปหน้าจอ wallet" />
              </>
            )}
            {d.email && (
              <>
                <h2>อีเมลต้นฉบับ</h2>
                <div className="small">
                  {d.email.subject}
                  <div className="muted">
                    {d.email.sender}
                    {d.email.received_at && ` · ${fmtWhen(d.email.received_at, true)}`}
                  </div>
                </div>
                {email === null ? (
                  <button className="link" onClick={showEmail}>
                    แสดงอีเมล
                  </button>
                ) : (
                  <iframe className="email" title="อีเมลต้นฉบับ" sandbox="" srcDoc={email} />
                )}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

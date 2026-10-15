import { useMemo, useState } from "react";
import { api } from "./api";
import { fmtMoney, fmtWhen } from "./format";
import { TxnDetailPanel } from "./TxnDetailPanel";
import type { Account, Category, Txn } from "./types";

function nowInBangkok(): { date: string; time: string } {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Bangkok", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(new Date()).map((x) => [x.type, x.value]),
  );
  return { date: `${p.year}-${p.month}-${p.day}`, time: `${p.hour}:${p.minute}` };
}

/** Cash, TrueMoney, income, a move between own accounts... anything without an email. Saved as confirmed right away. */
export function ManualEntryPage({ categories, accounts }: { categories: Category[]; accounts: Account[] }) {
  const start = nowInBangkok();
  const [kind, setKind] = useState<"expense" | "income" | "transfer">("expense");
  const [toAccountId, setToAccountId] = useState<number | "">("");
  const [accountId, setAccountId] = useState<number | "">("");
  const [amount, setAmount] = useState("");
  const [date, setDate] = useState(start.date);
  const [time, setTime] = useState(start.time);
  const [hasTime, setHasTime] = useState(true);
  const [categoryId, setCategoryId] = useState<number | "">("");
  const [merchant, setMerchant] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<Txn[]>([]);
  const [detailId, setDetailId] = useState<number | null>(null);
  // after an edit or delete in the detail panel: show the row as it is now
  const refreshSaved = (id: number) =>
    api.txnDetails(id).then(
      (t) => setSaved((prev) => prev.map((x) => (x.id === id ? { ...x, ...t } : x))),
      () => setSaved((prev) => prev.filter((x) => x.id !== id)),
    );

  const active = accounts.filter((a) => a.is_active);
  const catName = (id: number | null) => categories.find((c) => c.id === id)?.name_th ?? "";
  const accountName = (id: number | null) => accounts.find((a) => a.id === id)?.name ?? "";
  // leaf categories of this kind, grouped by parent
  const groups = useMemo(() => {
    const parents = categories.filter((c) => c.parent_id === null && c.kind === kind);
    return parents.map((p) => ({ parent: p, leaves: categories.filter((c) => c.parent_id === p.id) }));
  }, [categories, kind]);

  const valid =
    accountId !== "" &&
    (kind === "transfer" ? toAccountId !== "" && toAccountId !== accountId : categoryId !== "") &&
    Number(amount) > 0 &&
    date !== "";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setSaving(true);
    setError(null);
    try {
      const t = await api.createTransaction({
        kind,
        account_id: Number(accountId),
        amount,
        occurred_at: `${date}T${hasTime ? time : "00:00"}:00+07:00`,
        has_time: hasTime,
        category_id: kind === "transfer" ? null : Number(categoryId),
        transfer_account_id: kind === "transfer" ? Number(toAccountId) : null,
        merchant: merchant.trim() || null,
        description: description.trim() || null,
      });
      setSaved((prev) => [t, ...prev]);
      setAmount("");
      setMerchant("");
      setDescription("");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="manual">
      <h2>บันทึกรายการเอง</h2>
      <p className="muted small">
        สำหรับรายการที่ไม่มีอีเมล เช่น เงินสด, รายรับ หรือย้ายเงินระหว่างบัญชีของตัวเอง บันทึกแล้วนับในรายงานทันที
        (การโอนเข้าบัญชีตัวเองไม่นับเป็นรายจ่าย)
      </p>
      <form onSubmit={submit} className="form-grid">
        <div className="tabs full" role="radiogroup">
          {(["expense", "income", "transfer"] as const).map((k) => (
            <button
              type="button"
              key={k}
              role="radio"
              aria-checked={kind === k}
              className={kind === k ? "tab active" : "tab"}
              onClick={() => {
                setKind(k);
                setCategoryId("");
              }}
            >
              {{ expense: "รายจ่าย", income: "รายรับ", transfer: "โอนเข้าบัญชีตัวเอง" }[k]}
            </button>
          ))}
        </div>
        <label>
          {kind === "transfer" ? "จากบัญชี" : "บัญชี"}
          <select value={accountId} onChange={(e) => setAccountId(e.target.value ? Number(e.target.value) : "")} required>
            <option value="">— เลือกบัญชี —</option>
            {active.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          จำนวนเงิน (บาท)
          <input
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))}
            placeholder="0.00"
            required
          />
        </label>
        <label>
          วันที่
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
        </label>
        <label>
          เวลา
          <span className="inline">
            <input type="time" value={time} disabled={!hasTime} onChange={(e) => setTime(e.target.value)} />
            <span className="small muted">
              <input type="checkbox" checked={!hasTime} onChange={(e) => setHasTime(!e.target.checked)} /> ไม่ระบุเวลา
            </span>
          </span>
        </label>
        {kind === "transfer" ? (
          <label className="full">
            ไปบัญชี
            <select value={toAccountId} onChange={(e) => setToAccountId(e.target.value ? Number(e.target.value) : "")} required>
              <option value="">— เลือกบัญชีปลายทาง —</option>
              {active.filter((a) => a.id !== accountId).map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </label>
        ) : (
        <label className="full">
          หมวด
          <select value={categoryId} onChange={(e) => setCategoryId(e.target.value ? Number(e.target.value) : "")} required>
            <option value="">— เลือกหมวด —</option>
            {groups.map(({ parent, leaves }) =>
              leaves.length === 0 ? (
                <option key={parent.id} value={parent.id}>
                  {parent.name_th}
                </option>
              ) : (
                <optgroup key={parent.id} label={parent.name_th}>
                  {leaves.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name_th}
                    </option>
                  ))}
                </optgroup>
              ),
            )}
          </select>
        </label>
        )}
        <label>
          {kind === "income" ? "ผู้จ่าย" : kind === "transfer" ? "รายละเอียด" : "ร้าน / ผู้รับ"}
          <input value={merchant} onChange={(e) => setMerchant(e.target.value)} placeholder="ไม่บังคับ" />
        </label>
        <label>
          บันทึกเพิ่มเติม
          <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="ไม่บังคับ" />
        </label>
        <div className="full actions">
          <button className="primary" type="submit" disabled={!valid || saving}>
            {saving ? "กำลังบันทึก…" : "บันทึก"}
          </button>
          {error && <span className="error-text small">{error}</span>}
        </div>
      </form>

      {saved.length > 0 && (
        <>
          <h2>บันทึกแล้วรอบนี้</h2>
          <ul className="expenses saved-list">
            {saved.map((t) => (
              <li key={t.id}>
                <button onClick={() => setDetailId(t.id)} title="ดูรายละเอียด / แก้ไข">
                  <span>
                    {t.kind === "transfer"
                      ? `โอน ${accountName(t.account_id)} → ${accountName(t.counter_account_id)}`
                      : `${t.kind === "income" ? "รายรับ" : "รายจ่าย"} · ${catName(t.category_id)} · ${accountName(t.account_id)}`}
                    {t.merchant_raw ? ` · ${t.merchant_raw}` : ""}
                    <span className="muted small"> · {fmtWhen(t.occurred_at, t.has_time)}</span>
                  </span>
                  <span className="num">{fmtMoney(t.amount)}</span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {detailId !== null && (
        <TxnDetailPanel id={detailId} onClose={() => setDetailId(null)} onChanged={() => refreshSaved(detailId)} />
      )}
    </section>
  );
}

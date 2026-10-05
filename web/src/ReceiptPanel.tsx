import { useState } from "react";
import { api } from "./api";
import { fmtMoney } from "./format";
import type { Category, Receipt, Txn } from "./types";

type Line = { name: string; amount: string; category_id: number | null; confidence: number | null };

/** Review what was read from a receipt, fix lines, then split the payment into those items. */
export function ReceiptPanel(props: {
  txn: Txn;
  receipt: Receipt;
  categories: Category[];
  onDone: (message: string) => void;
  onCancel: () => void;
  onReread: (r: Receipt) => void;
}) {
  const { txn, receipt, categories, onDone, onCancel, onReread } = props;
  const [lines, setLines] = useState<Line[]>(
    (receipt.ocr_result?.items ?? []).map((i) => ({
      name: i.name, amount: i.amount.toFixed(2), category_id: i.category_id, confidence: i.confidence,
    })),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const total = lines.reduce((s, l) => s + (Number(l.amount) || 0), 0);
  const diff = Math.round((txn.amount - total) * 100) / 100;
  const parents = categories.filter((c) => c.parent_id === null && c.kind === "expense");
  const set = (i: number, patch: Partial<Line>) => setLines((ls) => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const valid = lines.length > 0 && lines.every((l) => l.name.trim() && Number(l.amount) > 0);

  const apply = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.applyReceipt(
        receipt.id,
        // confidence stays only on lines the user did not touch, so they keep the "AI" badge
        lines.map((l) => ({ name: l.name.trim(), amount: Number(l.amount), category_id: l.category_id, confidence: l.confidence })),
      );
      onDone(`แยกเป็น ${r.items} รายการ` + (r.adjustment ? ` + ส่วนต่าง ${fmtMoney(r.adjustment)}` : ""));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const reread = async () => {
    setBusy(true);
    try {
      onReread(await api.rereadReceipt(receipt.id));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="receipt-panel">
      <a href={`/api/receipts/${receipt.id}/image`} target="_blank" rel="noreferrer" className="receipt-thumb">
        <img src={`/api/receipts/${receipt.id}/image`} alt="ใบเสร็จ" />
      </a>
      <div className="receipt-body">
        {receipt.ocr_status === "failed" && (
          <div className="message error">
            อ่านใบเสร็จไม่สำเร็จ ({receipt.ocr_error}){" "}
            <button className="link" onClick={reread} disabled={busy}>
              ลองอ่านใหม่
            </button>
          </div>
        )}
        {receipt.ocr_result && (
          <div className="muted small">
            {receipt.ocr_result.merchant} · ยอดในใบเสร็จ {fmtMoney(receipt.ocr_result.total)}
            {receipt.ocr_result.adjustments.length > 0 &&
              ` · ${receipt.ocr_result.adjustments.map((a) => `${a.name} ${fmtMoney(a.amount)}`).join(", ")}`}
          </div>
        )}
        <table className="receipt-lines">
          <tbody>
            {lines.map((l, i) => (
              <tr key={i}>
                <td>
                  <input value={l.name} onChange={(e) => set(i, { name: e.target.value, confidence: null })} aria-label="รายการ" />
                </td>
                <td className="num">
                  <input
                    inputMode="decimal"
                    value={l.amount}
                    onChange={(e) => set(i, { amount: e.target.value.replace(/[^\d.]/g, ""), confidence: null })}
                    aria-label="จำนวนเงิน"
                    className="amount-input"
                  />
                </td>
                <td>
                  <select
                    value={l.category_id ?? ""}
                    onChange={(e) => set(i, { category_id: Number(e.target.value), confidence: null })}
                    aria-label="หมวด"
                  >
                    <option value="">— หมวด —</option>
                    {parents.map((p) => {
                      const kids = categories.filter((c) => c.parent_id === p.id);
                      return kids.length === 0 ? (
                        <option key={p.id} value={p.id}>
                          {p.name_th}
                        </option>
                      ) : (
                        <optgroup key={p.id} label={p.name_th}>
                          {kids.map((k) => (
                            <option key={k.id} value={k.id}>
                              {k.name_th}
                            </option>
                          ))}
                        </optgroup>
                      );
                    })}
                  </select>
                </td>
                <td>
                  <button className="link" onClick={() => setLines((ls) => ls.filter((_, j) => j !== i))} aria-label="ลบบรรทัด">
                    ✕
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <button className="link" onClick={() => setLines((ls) => [...ls, { name: "", amount: "", category_id: null, confidence: null }])}>
          + เพิ่มบรรทัด
        </button>
        <div className="receipt-totals small">
          รวมรายการ {fmtMoney(total)} · ยอดที่จ่าย {fmtMoney(txn.amount)}
          {diff !== 0 && <span className="warn-text"> · ส่วนต่าง {fmtMoney(diff)} จะเป็นบรรทัด "ส่วนต่างจากใบเสร็จ"</span>}
        </div>
        {error && <div className="error-text small">{error}</div>}
        <div className="actions">
          <button className="primary" onClick={apply} disabled={!valid || busy}>
            แยกรายการ
          </button>
          <button onClick={onCancel} disabled={busy}>
            ปิด
          </button>
        </div>
      </div>
    </div>
  );
}

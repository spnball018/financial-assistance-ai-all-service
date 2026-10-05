import { useEffect, useState } from "react";
import { api } from "./api";
import { fmtMoney, fmtWhen } from "./format";
import type { Balance } from "./types";

function OpeningEditor({ b, onSaved }: { b: Balance; onSaved: () => void }) {
  const [amount, setAmount] = useState(b.opening ? String(b.opening.amount) : "");
  const [asOf, setAsOf] = useState(b.opening?.as_of ?? "");
  const [error, setError] = useState<string | null>(null);
  const dirty = amount !== (b.opening ? String(b.opening.amount) : "") || asOf !== (b.opening?.as_of ?? "");

  const save = async () => {
    setError(null);
    try {
      if (amount === "" && b.opening) await api.deleteOpening(b.account_id);
      else await api.setOpening(b.account_id, { amount, as_of: asOf });
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="opening">
      <input
        inputMode="decimal"
        placeholder={b.type === "credit_card" ? "เช่น -2500 (ยอดค้าง)" : "ยอดยกมา"}
        value={amount}
        onChange={(e) => setAmount(e.target.value.replace(/[^\d.-]/g, ""))}
        aria-label="ยอดยกมา"
      />
      <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} aria-label="ณ วันที่" />
      <button className="primary" disabled={!dirty || (amount !== "" && !asOf)} onClick={save}>
        บันทึก
      </button>
      {error && <div className="error-text small">{error}</div>}
    </div>
  );
}

export function BalancesPage() {
  const [rows, setRows] = useState<Balance[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showInactive, setShowInactive] = useState(false);

  const load = () => api.balances().then(setRows, (e: Error) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  const visible = rows.filter((r) => showInactive || r.is_active);
  const total = visible.reduce((s, r) => s + r.balance, 0);

  return (
    <section>
      <div className="toolbar">
        <h2>ยอดคงเหลือ</h2>
        <div className="spacer" />
        <label className="small muted">
          <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} /> แสดงบัญชีที่ปิด
        </label>
      </div>
      <p className="muted small">
        <b>ยอดยกมา</b> = ยอดเงิน ณ ต้นวันที่ระบุ ยอดคงเหลือ = ยอดยกมา + รายการที่ยืนยันแล้วตั้งแต่วันนั้น
        (การโอนเข้าบัญชีตัวเองจะย้ายยอดจากบัญชีหนึ่งไปอีกบัญชี) บัตรเครดิตใส่ติดลบสำหรับยอดค้าง
      </p>
      {error && <div className="message error">{error}</div>}
      <div className="table-wrap">
        <table className="balances">
          <thead>
            <tr>
              <th>บัญชี</th>
              <th className="num">ยอดคงเหลือ</th>
              <th className="num">รวมรอตรวจ</th>
              <th>ยอดยกมา / ณ วันที่</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((b) => (
              <tr key={b.account_id}>
                <td className="b-name">
                  {b.name}
                  {b.last_movement_at && (
                    <div className="muted small">ล่าสุด {fmtWhen(b.last_movement_at, false)}</div>
                  )}
                  {b.bank_reported && (
                    <div className={Math.abs(b.bank_reported.difference) < 0.005 ? "small ok-text" : "small warn-text"}>
                      ธนาคารแจ้ง {fmtMoney(b.bank_reported.balance)} ({fmtWhen(b.bank_reported.as_of, true)}) · ระบบคำนวณ{" "}
                      {fmtMoney(b.bank_reported.computed)}
                      {Math.abs(b.bank_reported.difference) >= 0.005 &&
                        ` · ต่าง ${fmtMoney(b.bank_reported.difference)} (มักเป็นเงินเข้าที่ไม่มีอีเมลแจ้ง หรือยังไม่ได้ใส่ยอดยกมา)`}
                    </div>
                  )}
                </td>
                <td className={`num b-bal ${b.balance < 0 ? "neg" : ""}`}>{fmtMoney(b.balance)}</td>
                <td className="num b-pend muted">
                  {b.pending_count > 0 ? `${fmtMoney(b.balance_with_pending)} (${b.pending_count})` : "—"}
                </td>
                <td className="b-open">
                  <OpeningEditor key={`${b.account_id}-${b.opening?.amount}-${b.opening?.as_of}`} b={b} onSaved={load} />
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td>รวม</td>
              <td className="num">{fmtMoney(total)}</td>
              <td colSpan={2}></td>
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}

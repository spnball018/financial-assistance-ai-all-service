import { useEffect, useState } from "react";
import { api } from "./api";
import { CHANNEL_LABEL, fmtMoney, fmtWhen } from "./format";
import { TxnDetailPanel } from "./TxnDetailPanel";
import type { AccountLedger, Balance, LedgerRow } from "./types";

function what(r: LedgerRow): string {
  if (r.kind === "opening") return "ยอดยกมา";
  if (r.kind === "transfer" && r.other_account) return r.amount < 0 ? `โอนไป ${r.other_account}` : `โอนจาก ${r.other_account}`;
  return r.merchant_raw ?? CHANNEL_LABEL[r.channel ?? r.node_type] ?? r.description ?? "—";
}

function notCounted(r: LedgerRow, opening: AccountLedger["opening"]): string | null {
  if (r.status === "ignored") return "ไม่นับ";
  if (!r.counted && opening) return "ก่อนยอดยกมา";
  return null;
}

/** Every movement of one account with the running balance after each row, next to what K PLUS reported. */
export function AccountLedgerPage({ accountId, onBack, onPick }: {
  accountId: number;
  onBack: () => void;
  onPick: (id: number) => void;
}) {
  const [data, setData] = useState<AccountLedger | null>(null);
  const [accounts, setAccounts] = useState<Balance[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showUncounted, setShowUncounted] = useState(true);
  const [onlyGaps, setOnlyGaps] = useState(false);
  const [detailId, setDetailId] = useState<number | null>(null);

  const load = () => {
    setError(null);
    api.accountLedger(accountId).then(setData, (e: Error) => setError(e.message));
  };
  useEffect(() => {
    setData(null);
    load();
  }, [accountId]);
  useEffect(() => {
    api.balances().then(setAccounts, () => {});
  }, []);

  const rows = data?.rows ?? [];
  const gaps = rows.filter((r) => r.gap_starts);
  const latestBank = rows.find((r) => r.difference !== null);
  const pending = rows.filter((r) => r.status === "pending" && r.counted).length;
  const visible = rows.filter((r) => (showUncounted || r.counted) && (!onlyGaps || r.gap_starts));
  const now = rows[0];

  return (
    <section>
      <div className="toolbar">
        <button className="link" onClick={onBack}>← ยอดคงเหลือ</button>
        <select value={accountId} onChange={(e) => onPick(Number(e.target.value))} aria-label="บัญชี">
          {accounts.filter((a) => a.is_active || a.account_id === accountId).map((a) => (
            <option key={a.account_id} value={a.account_id}>{a.name}</option>
          ))}
        </select>
        <div className="spacer" />
        <label className="small muted">
          <input type="checkbox" checked={showUncounted} onChange={(e) => setShowUncounted(e.target.checked)} /> แสดงรายการที่ไม่นับ
        </label>
        {gaps.length > 0 && (
          <label className="small muted">
            <input type="checkbox" checked={onlyGaps} onChange={(e) => setOnlyGaps(e.target.checked)} /> เฉพาะจุดที่ยอดเริ่มต่าง
          </label>
        )}
      </div>
      {error && <div className="message error">{error}</div>}
      {data === null && !error && <p className="muted">กำลังโหลด…</p>}
      {data && (
        <>
          <p className="ledger-summary">
            <span>ยอดคงเหลือ <b className={now && now.balance < 0 ? "neg" : ""}>{fmtMoney(now?.balance ?? 0)}</b></span>
            {pending > 0 && <span className="muted">รวมรอตรวจ {fmtMoney(now.balance_with_pending)} ({pending} รายการ)</span>}
            <span className="muted">
              {data.opening ? `ยอดยกมา ${fmtMoney(data.opening.amount)} ณ ${fmtWhen(data.opening.as_of, false)}` : "ยังไม่ได้ใส่ยอดยกมา"}
            </span>
          </p>
          {latestBank && (
            Math.abs(latestBank.difference!) < 0.005 ? (
              <div className="message ok">ยอดล่าสุดตรงกับที่ธนาคารแจ้ง ({fmtWhen(latestBank.occurred_at, true)})</div>
            ) : (
              <div className="message warn">
                <span>
                  ล่าสุดธนาคารแจ้ง {fmtMoney(latestBank.bank_balance!)} แต่ระบบคำนวณ {fmtMoney(latestBank.balance_with_pending)} · ต่าง{" "}
                  {fmtMoney(latestBank.difference!)} · ยอดเริ่มต่างที่ {gaps.length} จุด (แถวสีส้ม) ให้ดูรายการที่แถวนั้นหรือก่อนหน้า:
                  มักเป็นเงินเข้าที่ไม่มีอีเมลแจ้ง รายการที่ไม่นับทั้งที่ควรนับ หรือยอดเงินผิด
                </span>
              </div>
            )
          )}
          <p className="muted small">
            ยอดหลังรายการรวมรายการรอตรวจด้วย (เหมือนที่ธนาคารเห็น) · "ต่าง" = ธนาคารแจ้ง − ระบบคำนวณ ·
            ตัวเลขบวกแปลว่ามีเงินเข้าที่ระบบไม่รู้ ลบแปลว่ามีเงินออกที่ระบบไม่รู้ (หรือบันทึกเงินเข้าเกิน)
          </p>
          <div className="table-wrap">
            <table className="ledger">
              <thead>
                <tr>
                  <th>วันที่</th>
                  <th>รายการ</th>
                  <th className="num">จำนวน</th>
                  <th className="num">ยอดหลังรายการ</th>
                  <th className="num">ธนาคารแจ้ง</th>
                </tr>
              </thead>
              <tbody>
                {visible.length === 0 && (
                  <tr><td colSpan={5} className="empty muted">ไม่มีรายการ</td></tr>
                )}
                {visible.map((r) => {
                  const off = notCounted(r, data.opening);
                  return (
                    <tr key={`${r.transaction_id}-${r.amount}`} className={`${off ? "uncounted" : ""} ${r.gap_starts ? "gap" : ""}`}>
                      <td className="l-date muted small">{fmtWhen(r.occurred_at, r.has_time)}</td>
                      <td className="l-what">
                        <button className="link l-open" onClick={() => setDetailId(r.transaction_id)}>{what(r)}</button>
                        {r.status === "pending" && <span className="badge badge-warn">รอตรวจ</span>}
                        {off && <span className="badge badge-none">{off}</span>}
                        {r.category_th && r.kind !== "transfer" && <span className="muted small"> · {r.category_th}</span>}
                        {r.note && <span className="small note"> 📝 {r.note}</span>}
                      </td>
                      <td className={`num l-amt ${r.amount < 0 ? "neg" : "ok-text"}`}>
                        {r.amount > 0 ? "+" : ""}{fmtMoney(r.amount)}
                      </td>
                      <td className="num l-bal">{off ? "" : fmtMoney(r.balance_with_pending)}</td>
                      <td className="num l-bank">
                        {r.bank_balance !== null && (
                          <>
                            {fmtMoney(r.bank_balance)}
                            {r.difference !== null && (
                              <div className={`small ${Math.abs(r.difference) < 0.005 ? "ok-text" : "warn-text"}`}>
                                {Math.abs(r.difference) < 0.005 ? "ตรง" : `ต่าง ${fmtMoney(r.difference)}`}
                              </div>
                            )}
                          </>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
      {detailId !== null && <TxnDetailPanel id={detailId} onClose={() => setDetailId(null)} onChanged={load} />}
    </section>
  );
}

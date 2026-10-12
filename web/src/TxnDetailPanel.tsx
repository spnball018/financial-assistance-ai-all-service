import { useEffect, useState } from "react";
import { api } from "./api";
import { CHANNEL_LABEL, fmtMoney, fmtWhen } from "./format";
import type { TxnDetails } from "./types";

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

export function TxnDetailPanel({ id, onClose }: { id: number; onClose: () => void }) {
  const [d, setD] = useState<TxnDetails | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    setD(null);
    setEmail(null);
    api.txnDetails(id).then(setD, (e: Error) => setError(e.message));
  }, [id]);

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
    if (d.kind === "expense")
      rows.push([
        "หมวด",
        <>
          {d.category_th ?? "ยังไม่มี"}
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
        {d && (
          <>
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

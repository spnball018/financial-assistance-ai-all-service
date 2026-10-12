import { useEffect, useState } from "react";
import { api } from "./api";
import { fmtMoney, fmtWhen, thisMonth } from "./format";
import { TxnDetailPanel } from "./TxnDetailPanel";
import type { ReportExpense, Summary } from "./types";

export function SummaryPage() {
  const [month, setMonth] = useState(thisMonth());
  const [data, setData] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null); // expanded category
  const [rows, setRows] = useState<ReportExpense[] | null>(null);
  const [detailId, setDetailId] = useState<number | null>(null);

  useEffect(() => {
    setError(null);
    setOpen(null);
    api.summary(month).then(setData, (e: Error) => setError(e.message));
  }, [month]);

  const toggle = (categoryId: number) => {
    if (open === categoryId) {
      setOpen(null);
      return;
    }
    setOpen(categoryId);
    setRows(null);
    api.reportExpenses(month, categoryId).then(setRows, (e: Error) => setError(e.message));
  };

  const max = Math.max(1, ...(data?.categories.map((c) => c.amount) ?? [1]));

  return (
    <section>
      <div className="toolbar">
        <label className="muted small">
          เดือน <input type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} />
        </label>
      </div>
      {error && <div className="message error">{error}</div>}
      {data && (
        <>
          <div className="cards">
            <div className="total-card">
              <div className="muted small">รายจ่ายที่ยืนยันแล้ว</div>
              <div className="total">{fmtMoney(data.total)}</div>
              <div className="muted small">
                {data.count} รายการ
                {data.pending_not_counted > 0 && ` · ยังรอตรวจอีก ${data.pending_not_counted} รายการ (ยังไม่นับ)`}
              </div>
            </div>
            <div className="total-card">
              <div className="muted small">รายรับ</div>
              <div className="total">{fmtMoney(data.income.total)}</div>
              <div className="muted small">
                {data.income.categories.map((c) => `${c.name_th ?? "ไม่ระบุ"} ${fmtMoney(c.amount)}`).join(" · ") || "ยังไม่มี"}
              </div>
            </div>
            <div className="total-card">
              <div className="muted small">คงเหลือสุทธิ (รายรับ − รายจ่าย)</div>
              <div className={`total ${data.net < 0 ? "neg" : ""}`}>{fmtMoney(data.net)}</div>
            </div>
          </div>
          {data.categories.length === 0 && <p className="muted">ยังไม่มีรายการที่ยืนยันในเดือนนี้</p>}
          <ul className="bars">
            {data.categories.map((g) => (
              <li key={g.category_id}>
                <button className="bar-toggle" onClick={() => toggle(g.category_id)} aria-expanded={open === g.category_id}>
                  <div className="bar-head">
                    <span>
                      {open === g.category_id ? "▾" : "▸"} {g.name_th}
                      <span className="muted small"> · {g.count} รายการ</span>
                    </span>
                    <span className="num">{fmtMoney(g.amount)}</span>
                  </div>
                  <div className="bar">
                    <div className="bar-fill" style={{ width: `${(g.amount / max) * 100}%` }} />
                  </div>
                </button>
                {g.subcategories.length > 0 && (
                  <ul className="subs">
                    {g.subcategories.map((s) => (
                      <li key={s.category_id}>
                        <span className="muted">{s.name_th}</span>
                        <span className="num muted">
                          {fmtMoney(s.amount)} · {s.count} รายการ
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                {open === g.category_id && (
                  <ul className="expenses">
                    {rows === null && <li className="muted">กำลังโหลด…</li>}
                    {rows?.map((r) => (
                      <li key={r.id}>
                        <button onClick={() => setDetailId(r.id)}>
                          <span>
                            {r.merchant_raw ?? "—"}
                            <span className="muted small">
                              {" "}
                              · {fmtWhen(r.occurred_at, r.has_time)}
                              {r.path.length > 0 && ` · ${r.path.join(" › ")}`}
                              {g.subcategories.length > 0 && ` · ${r.category_th}`}
                            </span>
                          </span>
                          <span className="num">{fmtMoney(r.amount)}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
          {data.transfers_out.length > 0 && (
            <div className="transfers">
              <h2>โอนเข้าบัญชีตัวเอง (ไม่นับเป็นรายจ่าย)</h2>
              <ul className="subs">
                {data.transfers_out.map((t) => (
                  <li key={t.account_id}>
                    <span>→ {t.name}</span>
                    <span className="num muted">
                      {fmtMoney(t.amount)} · {t.count} รายการ
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
      {detailId !== null && <TxnDetailPanel
          id={detailId}
          onClose={() => setDetailId(null)}
          onChanged={() => {
            api.summary(month).then(setData, (e: Error) => setError(e.message));
            if (open !== null) api.reportExpenses(month, open).then(setRows, (e: Error) => setError(e.message));
          }}
        />}
    </section>
  );
}

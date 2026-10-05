import { useEffect, useState } from "react";
import { api } from "./api";
import { fmtMoney, thisMonth } from "./format";
import type { Summary } from "./types";

export function SummaryPage() {
  const [month, setMonth] = useState(thisMonth());
  const [data, setData] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    api.summary(month).then(setData, (e: Error) => setError(e.message));
  }, [month]);

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
          <div className="total-card">
            <div className="muted small">รายจ่ายที่ยืนยันแล้ว</div>
            <div className="total">{fmtMoney(data.total)}</div>
            <div className="muted small">
              {data.count} รายการ
              {data.pending_not_counted > 0 && ` · ยังรอตรวจอีก ${data.pending_not_counted} รายการ (ยังไม่นับ)`}
            </div>
          </div>
          {data.categories.length === 0 && <p className="muted">ยังไม่มีรายการที่ยืนยันในเดือนนี้</p>}
          <ul className="bars">
            {data.categories.map((g) => (
              <li key={g.category_id}>
                <div className="bar-head">
                  <span>{g.name_th}</span>
                  <span className="num">{fmtMoney(g.amount)}</span>
                </div>
                <div className="bar">
                  <div className="bar-fill" style={{ width: `${(g.amount / max) * 100}%` }} />
                </div>
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
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

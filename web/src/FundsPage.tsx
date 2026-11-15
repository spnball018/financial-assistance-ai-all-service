import { useEffect, useState } from "react";
import { api } from "./api";
import { fmtMoney, fmtUnits, fmtWhen } from "./format";
import { bangkokDate, finnomenaUrl, FundTradeForm, navOff, todayInBangkok, type Prefill } from "./FundTradeForm";
import { FundValueChart } from "./FundValueChart";
import type { Account, FundAccount, FundPosition, FundTrade, UnlinkedTransfer } from "./types";

const gainClass = (n: number | null) => (n === null || Math.abs(n) < 0.005 ? "" : n < 0 ? "neg" : "ok-text");

function Gain({ amount, base }: { amount: number | null; base?: number }) {
  if (amount === null) return <span className="muted">—</span>;
  const pct = base ? ` (${amount >= 0 ? "+" : ""}${((amount / base) * 100).toFixed(2)}%)` : "";
  return (
    <span className={gainClass(amount)}>
      {amount > 0 ? "+" : ""}
      {fmtMoney(amount)}
      {pct}
    </span>
  );
}

function NavEditor({ fund, onSaved }: { fund: FundPosition; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [nav, setNav] = useState("");
  const [date, setDate] = useState(todayInBangkok());
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setError(null);
    try {
      await api.updateFund(fund.id, { nav, nav_date: date });
      setOpen(false);
      setNav("");
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (!open)
    return (
      <button className="link small" onClick={() => setOpen(true)}>
        อัปเดต NAV
      </button>
    );
  return (
    <div className="opening">
      <input inputMode="decimal" placeholder="NAV" value={nav} onChange={(e) => setNav(e.target.value.replace(/[^\d.]/g, ""))} aria-label="NAV" />
      <input type="date" value={date} onChange={(e) => setDate(e.target.value)} aria-label="ณ วันที่" />
      <button className="primary" disabled={!(Number(nav) > 0) || !date} onClick={save}>
        บันทึก
      </button>
      <button className="link" onClick={() => setOpen(false)}>
        ยกเลิก
      </button>
      {error && <div className="error-text small">{error}</div>}
    </div>
  );
}

function tradeLabel(t: FundTrade): string {
  if (t.funding === "switch")
    return t.side === "sell" ? `สับเปลี่ยนออก ${t.fund_code} → ${t.switch_fund_code}` : `สับเปลี่ยนเข้า ${t.fund_code} ← ${t.switch_fund_code}`;
  return `${t.side === "buy" ? "ซื้อ" : "ขาย"} ${t.fund_code}`;
}

function moneyLabel(t: FundTrade): string {
  switch (t.funding) {
    case "switch":
      return "ไม่มีการโอน";
    case "held_before":
      return "ถือมาก่อนเริ่มใช้ระบบ";
    default:
      if (t.transaction_id === null) return "การโอนถูกลบไปแล้ว";
      return `${t.side === "buy" ? "โอนจาก" : "โอนเข้า"} ${t.other_account}`;
  }
}

function History({ items, accountOf, onDelete }: {
  items: FundTrade[];
  /** fund_id -> account name, when trades of several accounts are shown */
  accountOf?: Map<number, string>;
  onDelete: (t: FundTrade) => void;
}) {
  return (
    <div className="table-wrap">
      <table className="movements">
        <thead>
          <tr>
            <th>วันที่</th>
            <th>รายการ</th>
            <th className="num">หน่วย @ NAV</th>
            <th className="num">จำนวนเงิน</th>
            <th>เงินมาจาก / ไปที่</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 && (
            <tr>
              <td colSpan={6} className="empty">
                ยังไม่มีรายการ
              </td>
            </tr>
          )}
          {items.map((t) => (
            <tr key={t.id}>
              <td className="m-date nowrap">{fmtWhen(t.trade_date, false)}</td>
              <td className="m-desc">
                {tradeLabel(t)}
                {accountOf && <div className="muted small">{accountOf.get(t.fund_id)}</div>}
                {t.note && <div className="muted small">{t.note}</div>}
              </td>
              <td className="num m-units muted">
                {fmtUnits(t.units)} @ {t.nav.toFixed(4)}
              </td>
              <td className={`num m-amount ${t.side === "buy" ? "" : "ok-text"}`}>{fmtMoney(t.amount)}</td>
              <td className={`m-cash small ${t.transaction_id === null && t.funding.endsWith("transfer") ? "warn-text" : "muted"}`}>
                {moneyLabel(t)}
                {t.transfer_status === "pending" && <span className="badge badge-warn">รอตรวจ</span>}
              </td>
              <td className="m-act">
                <button className="link small" onClick={() => onDelete(t)}>
                  ลบ
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type AccountTransfer = UnlinkedTransfer & { accountId: number; accountName: string };

function Unlinked({ items, several, onRecord }: { items: AccountTransfer[]; several: boolean; onRecord: (u: AccountTransfer) => void }) {
  if (items.length === 0) return null;
  return (
    <div className="transfers fund-unlinked">
      <h2>เงินโอนที่ยังไม่ได้บันทึกว่าซื้อ/ขายกองทุนอะไร ({items.length})</h2>
      <p className="muted small">บัญชีกองทุนไม่มีเงินค้าง การโอนเข้าแต่ละครั้งควรเป็นการซื้อ และการโอนออกควรมาจากการขาย</p>
      <ul className="subs">
        {items.map((u) => (
          <li key={u.transaction_id}>
            <span>
              {fmtWhen(u.occurred_at, u.has_time)} · {u.amount > 0 ? `โอนเข้าจาก ${u.other_account}` : `โอนออกไป ${u.other_account}`}{" "}
              <b>{fmtMoney(Math.abs(u.amount))}</b>
              {u.status === "pending" && <span className="badge badge-warn">รอตรวจ</span>}
              {several && <span className="muted"> · {u.accountName}</span>}
              {u.description && <span className="muted"> · {u.description}</span>}
            </span>
            <button className="link" onClick={() => onRecord(u)}>
              {u.amount > 0 ? "บันทึกการซื้อ" : "บันทึกการขาย"}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

const ALL = "all";

/** Fund accounts (accounts of type investment): funds held, buys paid by a transfer from the bank, sells, switches, NAV.
 * A fund account holds no cash: every trade says where its money came from or went. Shows all fund accounts
 * together, or one picked from the toolbar. */
export function FundsPage({ accounts }: { accounts: Account[] }) {
  const [ports, setPorts] = useState<FundAccount[] | null>(null);
  const [view, setView] = useState<number | typeof ALL>(ALL);
  const [history, setHistory] = useState<FundTrade[]>([]);
  const [unlinked, setUnlinked] = useState<AccountTransfer[]>([]);
  const [prefill, setPrefill] = useState<Prefill | null>(null);
  const [showHidden, setShowHidden] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [navNote, setNavNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [fetching, setFetching] = useState(false);
  const [chartVersion, setChartVersion] = useState(0);

  const shown = (ports ?? []).filter((p) => view === ALL || p.account_id === view);
  const several = shown.length > 1;

  const load = () => {
    setPrefill(null);
    api.fundPortfolio().then((p) => {
      setPorts(p);
      // the account picked may have been the only one shown before another was added
      setView((cur) => (cur !== ALL && !p.some((a) => a.account_id === cur) ? ALL : cur));
    }, (e: Error) => setError(e.message));
  };
  /** NAV history from Finnomena now (it also runs every day by itself): backfills, then moves the latest NAVs. */
  const syncNavs = async () => {
    setFetching(true);
    try {
      const r = await api.syncFundNavs();
      setNavNote(
        r.failed.length > 0
          ? { ok: false, text: `ดึง NAV ไม่ได้: ${r.failed.map((f) => `${f.code} (${f.error})`).join(", ")}` }
          : { ok: true, text: `ดึง NAV แล้ว ${r.codes} กองทุน (บันทึกใหม่ ${r.navs_saved} วัน)` },
      );
      load();
      setChartVersion((v) => v + 1);
    } catch (e) {
      if (!navOff(e)) setNavNote({ ok: false, text: `ดึง NAV ไม่ได้: ${(e as Error).message}` });
    } finally {
      setFetching(false);
    }
  };
  /** Latest NAVs from Finnomena; quiet = on opening the page, only failures are shown. */
  const refreshNavs = async (quiet: boolean) => {
    setFetching(true);
    try {
      const r = await api.refreshFundNavs();
      setNavNote(
        r.failed.length > 0
          ? { ok: false, text: `ดึง NAV ไม่ได้: ${r.failed.map((f) => `${f.code} (${f.error})`).join(", ")}` }
          : quiet
            ? null
            : { ok: true, text: `อัปเดต NAV แล้ว ${r.updated.length} กองทุน` },
      );
      if (r.updated.length > 0) load();
    } catch (e) {
      if (!navOff(e)) setNavNote({ ok: false, text: `ดึง NAV ไม่ได้: ${(e as Error).message}` });
    } finally {
      setFetching(false);
    }
  };
  useEffect(() => {
    load();
    refreshNavs(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (ports === null) return;
    const accts = ports.filter((p) => view === ALL || p.account_id === view);
    let live = true;
    Promise.all(accts.map((p) => api.fundHistory(p.account_id))).then((hs) => {
      if (!live) return;
      setHistory(hs.flatMap((h) => h.items).sort((a, b) => b.trade_date.localeCompare(a.trade_date) || b.id - a.id));
      setUnlinked(
        hs
          .flatMap((h, i) => h.unlinked.map((u) => ({ ...u, accountId: accts[i].account_id, accountName: accts[i].name })))
          .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at)),
      );
    }, (e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [view, ports]);

  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const deleteTrade = (t: FundTrade) => {
    const extra =
      t.funding === "switch"
        ? " (ลบทั้งสองฝั่งของการสับเปลี่ยน)"
        : t.funding === "new_transfer" && t.transaction_id
          ? " การโอนที่บันทึกพร้อมกันจะถูกลบด้วย"
          : t.funding === "linked_transfer"
            ? " การโอนเดิมยังอยู่ และจะกลับไปรอบันทึกการซื้อขาย"
            : "";
    if (confirm(`ลบ${tradeLabel(t)} ${fmtMoney(t.amount)}?${extra}`)) run(() => api.deleteFundTrade(t.id));
  };
  const record = (u: AccountTransfer) => {
    setPrefill({ side: u.amount > 0 ? "buy" : "sell", accountId: u.accountId, amount: String(Math.abs(u.amount)), date: bangkokDate(u.occurred_at), transactionId: u.transaction_id });
    document.querySelector(".trade-form")?.scrollIntoView({ behavior: "smooth" });
  };

  if (ports === null) return <section>{error ? <div className="message error">{error}</div> : <p className="muted">กำลังโหลด…</p>}</section>;
  if (ports.length === 0)
    return (
      <section>
        <h2>ซื้อกองทุน</h2>
        <p className="muted small">
          ยังไม่มีบัญชีกองทุน บันทึกการซื้อครั้งแรกได้เลย: ตั้งชื่อบัญชีกองทุน (เช่น หนึ่งบัญชีต่อ บลจ.) ใส่รหัสกองทุน ยอดเงิน และบัญชีที่โอนเงินไปซื้อ
          ถ้ามีกองทุนอยู่ก่อนแล้ว เลือกแท็บ "ยอดที่มีอยู่แล้ว"
        </p>
        {error && <div className="message error">{error}</div>}
        <FundTradeForm ports={[]} accounts={accounts} onSaved={load} />
      </section>
    );

  const funds = shown.flatMap((p) => p.funds).filter((f) => showHidden || f.is_active || f.units > 0);
  const accountOf = new Map(shown.flatMap((p) => p.funds.map((f) => [f.id, p.name] as const)));
  const sum = (k: "market_value" | "net_deposits" | "cost" | "unrealized" | "realized") => shown.reduce((t, p) => t + p[k], 0);
  const total = { market_value: sum("market_value"), net_deposits: sum("net_deposits"), cost: sum("cost"), unrealized: sum("unrealized"), realized: sum("realized") };

  return (
    <section>
      <div className="toolbar">
        <h2>กองทุน</h2>
        {ports.length > 1 && (
          <select value={view} onChange={(e) => setView(e.target.value === ALL ? ALL : Number(e.target.value))} aria-label="บัญชีกองทุน">
            <option value={ALL}>ทุกบัญชี ({ports.length})</option>
            {ports.map((p) => (
              <option key={p.account_id} value={p.account_id}>
                {p.name}
                {p.is_active ? "" : " (ปิด)"}
              </option>
            ))}
          </select>
        )}
        {ports.length === 1 && <span className="muted">{ports[0].name}</span>}
        <div className="spacer" />
        <button onClick={syncNavs} disabled={fetching} title="ดึง NAV ย้อนหลังและล่าสุด (ระบบดึงให้อัตโนมัติทุกวัน 08:00 และ 20:00)">
          {fetching ? "กำลังดึง NAV…" : "ดึง NAV ล่าสุด"}
        </button>
        <label className="small muted">
          <input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} /> แสดงกองทุนที่ซ่อน
        </label>
      </div>
      <p className="muted small">
        เงินที่โอนเข้าบัญชีกองทุนเป็นการโอนระหว่างบัญชีตัวเอง ไม่นับเป็นรายจ่าย ต้นทุนคิดแบบเฉลี่ย มูลค่า = จำนวนหน่วย × NAV ล่าสุด
        ซึ่งดึงจาก Finnomena ตามรหัสกองทุนทุกครั้งที่เปิดหน้านี้ (หรือ NAV ของรายการซื้อขายล่าสุดถ้าใหม่กว่า)
      </p>
      {error && <div className="message error">{error}</div>}
      {navNote && <div className={navNote.ok ? "message ok" : "message warn"}>{navNote.text}</div>}

      {shown.length > 0 && (
        <>
          <div className="cards">
            <div className="total-card">
              <div className="muted small">มูลค่ารวม</div>
              <div className="total">{fmtMoney(total.market_value)}</div>
              <div className="muted small">เงินโอนเข้าสุทธิ {fmtMoney(total.net_deposits)}</div>
            </div>
            <div className="total-card">
              <div className="muted small">ต้นทุน</div>
              <div className="total">{fmtMoney(total.cost)}</div>
            </div>
            <div className="total-card">
              <div className="muted small">กำไร/ขาดทุนยังไม่รับรู้</div>
              <div className="total">
                <Gain amount={total.unrealized} base={total.cost || undefined} />
              </div>
            </div>
            <div className="total-card">
              <div className="muted small">กำไร/ขาดทุนที่รับรู้แล้ว (จากการขาย)</div>
              <div className="total">
                <Gain amount={total.realized} />
              </div>
            </div>
          </div>

          <h2>มูลค่าตามเวลา</h2>
          <FundValueChart key={chartVersion} accountIds={shown.map((p) => p.account_id)} />

          <Unlinked items={unlinked} several={several} onRecord={record} />

          <h2>ซื้อ/ขายกองทุน</h2>
          <FundTradeForm
            key={prefill?.transactionId ?? ""}
            ports={ports}
            accounts={accounts}
            accountId={view === ALL ? null : view}
            prefill={prefill}
            onSaved={load}
          />

          <h2>กองทุนที่ถืออยู่</h2>

          <div className="table-wrap">
            <table className="funds">
              <thead>
                <tr>
                  <th>กองทุน</th>
                  <th className="num">หน่วย</th>
                  <th className="num">ต้นทุน (เฉลี่ย/หน่วย)</th>
                  <th className="num">NAV</th>
                  <th className="num">มูลค่า</th>
                  <th className="num">กำไร/ขาดทุน</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {funds.length === 0 && (
                  <tr>
                    <td colSpan={7} className="empty">
                      ยังไม่มีกองทุน บันทึกการซื้อหรือยอดที่มีอยู่แล้วด้านบน
                    </td>
                  </tr>
                )}
                {funds.map((f) => (
                  <tr key={f.id} className={f.is_active ? "" : "inactive"}>
                    <td className="f-name">
                      <b>{f.code}</b>
                      {f.name && <div className="muted small">{f.name}</div>}
                      {several && <div className="muted small">บัญชี {accountOf.get(f.id)}</div>}
                      {f.realized !== 0 && (
                        <div className="small">
                          รับรู้แล้ว <Gain amount={f.realized} />
                        </div>
                      )}
                    </td>
                    <td className="num f-units">{fmtUnits(f.units)}</td>
                    <td className="num f-cost">
                      {fmtMoney(f.cost)}
                      {f.avg_cost !== null && <div className="muted small">{f.avg_cost.toFixed(4)}</div>}
                    </td>
                    <td className="num f-nav">
                      {f.nav !== null ? f.nav.toFixed(4) : "—"}
                      {f.nav_date && (
                        <div className="muted small">
                          {fmtWhen(f.nav_date, false)}{" "}
                          {f.nav_source === "finnomena" ? (
                            <a href={finnomenaUrl(f.code)} target="_blank" rel="noreferrer">
                              Finnomena
                            </a>
                          ) : f.nav_source === "trade" ? (
                            "(จากรายการ)"
                          ) : (
                            "(ใส่เอง)"
                          )}
                        </div>
                      )}
                      <NavEditor fund={f} onSaved={load} />
                    </td>
                    <td className="num f-value">{f.market_value !== null ? fmtMoney(f.market_value) : "—"}</td>
                    <td className="num f-gain">
                      <Gain amount={f.unrealized} base={f.cost || undefined} />
                    </td>
                    <td className="f-act">
                      {f.trades === 0 ? (
                        <button className="link small" onClick={() => run(() => api.deleteFund(f.id))}>
                          ลบ
                        </button>
                      ) : (
                        <button className="link small" onClick={() => run(() => api.updateFund(f.id, { is_active: !f.is_active }))}>
                          {f.is_active ? "ซ่อน" : "แสดง"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h2>ประวัติการซื้อขาย</h2>
          <History items={history} accountOf={several ? accountOf : undefined} onDelete={deleteTrade} />
        </>
      )}
    </section>
  );
}

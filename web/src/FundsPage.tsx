import { useEffect, useState } from "react";
import { api } from "./api";
import { fmtMoney, fmtUnits, fmtWhen } from "./format";
import type { Account, FundAccount, FundPosition, FundTrade, UnlinkedTransfer } from "./types";

function todayInBangkok(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Bangkok" }).format(new Date());
}

const bangkokDate = (iso: string) => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Bangkok" }).format(new Date(iso));

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

function AddFund({ accountId, onSaved }: { accountId: number; onSaved: () => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await api.createFund({ account_id: accountId, code: code.trim(), name: name.trim() || null });
      setCode("");
      setName("");
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <form className="opening add-fund" onSubmit={add}>
      <input placeholder="รหัสกองทุน เช่น K-USA-A(A)" value={code} onChange={(e) => setCode(e.target.value)} aria-label="รหัสกองทุน" />
      <input placeholder="ชื่อ (ไม่บังคับ)" value={name} onChange={(e) => setName(e.target.value)} aria-label="ชื่อกองทุน" />
      <button type="submit" disabled={!code.trim()}>
        เพิ่มกองทุน
      </button>
      {error && <span className="error-text small">{error}</span>}
    </form>
  );
}

type Side = "buy" | "sell" | "switch";
/** Starts the trade form from a transfer that no trade points at yet. */
export interface Prefill {
  side: "buy" | "sell";
  amount: string;
  date: string;
  transactionId: number;
}

const HELD = "held";

function TradeForm({ port, accounts, unlinked, prefill, onSaved }: {
  port: FundAccount;
  accounts: Account[];
  unlinked: UnlinkedTransfer[];
  prefill: Prefill | null;
  onSaved: () => void;
}) {
  const [side, setSide] = useState<Side>(prefill?.side ?? "buy");
  const [fundId, setFundId] = useState<number | "">("");
  const [toFundId, setToFundId] = useState<number | "">("");
  const [date, setDate] = useState(prefill?.date ?? todayInBangkok());
  const [amount, setAmount] = useState(prefill?.amount ?? "");
  const [units, setUnits] = useState("");
  const [unitsIn, setUnitsIn] = useState("");
  const [nav, setNav] = useState("");
  // "acc:<id>" = create the transfer, "txn:<id>" = an existing transfer, "held" = held before the ledger
  const [money, setMoney] = useState(prefill ? `txn:${prefill.transactionId}` : "");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const active = port.funds.filter((f) => f.is_active);
  const funds = side === "buy" ? active : port.funds.filter((f) => f.units > 0);
  const others = accounts.filter((a) => a.is_active && a.id !== port.account_id);
  const transfers = unlinked.filter((u) => (side === "buy" ? u.amount > 0 : u.amount < 0));
  const navHint = Number(amount) > 0 && Number(units) > 0 ? (Number(amount) / Number(units)).toFixed(4) : "";
  const held = port.funds.find((f) => f.id === fundId)?.units;
  const valid =
    fundId !== "" &&
    Number(amount) > 0 &&
    Number(units) > 0 &&
    date !== "" &&
    (side === "switch" ? toFundId !== "" && toFundId !== fundId && Number(unitsIn) > 0 : money !== "");

  const pickTransfer = (value: string) => {
    setMoney(value);
    // a linked transfer must be the same amount: start from it
    const t = unlinked.find((u) => `txn:${u.transaction_id}` === value);
    if (t) {
      setAmount(String(Math.abs(t.amount)));
      setDate(bangkokDate(t.occurred_at));
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setSaving(true);
    setError(null);
    try {
      if (side === "switch") {
        await api.addFundSwitch({
          from_fund_id: Number(fundId),
          to_fund_id: Number(toFundId),
          trade_date: date,
          amount,
          units_out: units,
          units_in: unitsIn,
          note: note.trim() || null,
        });
      } else {
        const [kind, id] = money.split(":");
        await api.addFundTrade({
          fund_id: Number(fundId),
          side,
          trade_date: date,
          amount,
          units,
          nav: nav || null,
          counter_account_id: kind === "acc" ? Number(id) : null,
          transaction_id: kind === "txn" ? Number(id) : null,
          held_before: money === HELD,
          note: note.trim() || null,
        });
      }
      setAmount("");
      setUnits("");
      setUnitsIn("");
      setNav("");
      setNote("");
      setMoney("");
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="form-grid trade-form">
      <div className="tabs full" role="radiogroup">
        {(["buy", "sell", "switch"] as const).map((s) => (
          <button
            type="button"
            key={s}
            role="radio"
            aria-checked={side === s}
            className={side === s ? "tab active" : "tab"}
            onClick={() => {
              setSide(s);
              setMoney("");
            }}
          >
            {{ buy: "ซื้อ", sell: "ขาย", switch: "สับเปลี่ยน" }[s]}
          </button>
        ))}
      </div>
      <label>
        {side === "switch" ? "จากกองทุน" : "กองทุน"}
        <select value={fundId} onChange={(e) => setFundId(e.target.value ? Number(e.target.value) : "")} required>
          <option value="">— เลือกกองทุน —</option>
          {funds.map((f) => (
            <option key={f.id} value={f.id}>
              {f.code}
              {f.name ? ` · ${f.name}` : ""}
            </option>
          ))}
        </select>
      </label>
      {side === "switch" ? (
        <label>
          ไปกองทุน
          <select value={toFundId} onChange={(e) => setToFundId(e.target.value ? Number(e.target.value) : "")} required>
            <option value="">— เลือกกองทุน —</option>
            {active.filter((f) => f.id !== fundId).map((f) => (
              <option key={f.id} value={f.id}>
                {f.code}
                {f.name ? ` · ${f.name}` : ""}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <label>
          {side === "buy" ? "เงินที่ใช้ซื้อมาจาก" : "เงินที่ขายได้เข้า"}
          <select value={money} onChange={(e) => pickTransfer(e.target.value)} required>
            <option value="">— เลือก —</option>
            <optgroup label="บันทึกการโอนให้ด้วย">
              {others.map((a) => (
                <option key={a.id} value={`acc:${a.id}`}>
                  {side === "buy" ? "โอนจาก" : "โอนเข้า"} {a.name}
                </option>
              ))}
            </optgroup>
            {transfers.length > 0 && (
              <optgroup label="รายการโอนที่มีอยู่แล้ว">
                {transfers.map((u) => (
                  <option key={u.transaction_id} value={`txn:${u.transaction_id}`}>
                    {fmtWhen(u.occurred_at, u.has_time)} · {u.other_account} · {fmtMoney(Math.abs(u.amount))}
                  </option>
                ))}
              </optgroup>
            )}
            {side === "buy" && <option value={HELD}>ถือมาก่อนเริ่มใช้ระบบ (ไม่มีการโอน)</option>}
          </select>
        </label>
      )}
      <label>
        วันที่ทำรายการ
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
      </label>
      <label>
        {side === "buy" ? "จำนวนเงินที่ซื้อ (บาท)" : side === "sell" ? "เงินที่ได้จากการขาย (บาท)" : "มูลค่าที่สับเปลี่ยน (บาท)"}
        <input
          inputMode="decimal"
          value={amount}
          disabled={money.startsWith("txn:")}
          onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))}
          placeholder="0.00"
          required
        />
      </label>
      <label>
        {side === "switch" ? "หน่วยที่ขายออก" : "จำนวนหน่วย"}
        <input inputMode="decimal" value={units} onChange={(e) => setUnits(e.target.value.replace(/[^\d.]/g, ""))} placeholder="0.0000" required />
        {side !== "buy" && held !== undefined && <span className="small">ถืออยู่ {fmtUnits(held)} หน่วย</span>}
      </label>
      {side === "switch" ? (
        <label>
          หน่วยที่ได้รับ
          <input inputMode="decimal" value={unitsIn} onChange={(e) => setUnitsIn(e.target.value.replace(/[^\d.]/g, ""))} placeholder="0.0000" required />
        </label>
      ) : (
        <label>
          NAV ต่อหน่วย
          <input inputMode="decimal" value={nav} onChange={(e) => setNav(e.target.value.replace(/[^\d.]/g, ""))} placeholder={navHint || "ไม่บังคับ"} />
        </label>
      )}
      <label className="full">
        บันทึกเพิ่มเติม
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="ไม่บังคับ" />
      </label>
      <p className="full muted small">
        {side === "switch"
          ? "สับเปลี่ยนในบัญชีเดียวกัน ไม่มีเงินเข้าหรือออกจากบัญชี NAV ของแต่ละฝั่งคำนวณจากมูลค่า ÷ หน่วย"
          : "บัญชีกองทุนไม่มีเงินค้าง เงินที่ใช้ซื้อต้องโอนมาจากบัญชีอื่น และเงินที่ขายได้ต้องออกไปบัญชีอื่น ถ้าการโอนนั้นมาจากอีเมลธนาคารแล้ว ให้เลือกจาก \"รายการโอนที่มีอยู่แล้ว\" จะได้ไม่บันทึกซ้ำ"}
      </p>
      <div className="full actions">
        <button className="primary" type="submit" disabled={!valid || saving}>
          {saving ? "กำลังบันทึก…" : { buy: "บันทึกการซื้อ", sell: "บันทึกการขาย", switch: "บันทึกการสับเปลี่ยน" }[side]}
        </button>
        {error && <span className="error-text small">{error}</span>}
      </div>
    </form>
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

function History({ items, onDelete }: { items: FundTrade[]; onDelete: (t: FundTrade) => void }) {
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

function Unlinked({ items, onRecord }: { items: UnlinkedTransfer[]; onRecord: (u: UnlinkedTransfer) => void }) {
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

/** Fund accounts (accounts of type investment): funds held, buys paid by a transfer from the bank, sells, switches, NAV.
 * A fund account holds no cash: every trade says where its money came from or went. */
export function FundsPage({ accounts }: { accounts: Account[] }) {
  const [ports, setPorts] = useState<FundAccount[] | null>(null);
  const [accountId, setAccountId] = useState<number | null>(null);
  const [history, setHistory] = useState<FundTrade[]>([]);
  const [unlinked, setUnlinked] = useState<UnlinkedTransfer[]>([]);
  const [prefill, setPrefill] = useState<Prefill | null>(null);
  const [showHidden, setShowHidden] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const port = ports?.find((p) => p.account_id === accountId) ?? null;

  const load = () => {
    setPrefill(null);
    api.fundPortfolio().then((p) => {
      setPorts(p);
      setAccountId((cur) => cur ?? p.find((a) => a.is_active)?.account_id ?? p[0]?.account_id ?? null);
    }, (e: Error) => setError(e.message));
  };
  useEffect(load, []);
  useEffect(() => {
    if (accountId === null) return;
    api.fundHistory(accountId).then((h) => {
      setHistory(h.items);
      setUnlinked(h.unlinked);
    }, (e: Error) => setError(e.message));
  }, [accountId, ports]);

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
  const record = (u: UnlinkedTransfer) => {
    setPrefill({ side: u.amount > 0 ? "buy" : "sell", amount: String(Math.abs(u.amount)), date: bangkokDate(u.occurred_at), transactionId: u.transaction_id });
    document.querySelector(".trade-form")?.scrollIntoView({ behavior: "smooth" });
  };

  if (ports === null) return <section>{error ? <div className="message error">{error}</div> : <p className="muted">กำลังโหลด…</p>}</section>;
  if (ports.length === 0)
    return (
      <section>
        <h2>กองทุน</h2>
        <p className="muted">
          ยังไม่มีบัญชีกองทุน ไปที่ <a href="#settings">ตั้งค่า</a> → บัญชี แล้วเพิ่มบัญชีประเภท "ลงทุน" (เช่น หนึ่งบัญชีต่อ บลจ.)
        </p>
      </section>
    );

  const funds = (port?.funds ?? []).filter((f) => showHidden || f.is_active || f.units > 0);

  return (
    <section>
      <div className="toolbar">
        <h2>กองทุน</h2>
        {ports.length > 1 && (
          <select value={accountId ?? ""} onChange={(e) => setAccountId(Number(e.target.value))} aria-label="บัญชีกองทุน">
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
        <label className="small muted">
          <input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} /> แสดงกองทุนที่ซ่อน
        </label>
      </div>
      <p className="muted small">
        เงินที่โอนเข้าบัญชีกองทุนเป็นการโอนระหว่างบัญชีตัวเอง ไม่นับเป็นรายจ่าย ต้นทุนคิดแบบเฉลี่ย มูลค่าใช้ NAV ล่าสุดที่บันทึก
        (หรือ NAV ของรายการซื้อขายล่าสุดถ้าใหม่กว่า)
      </p>
      {error && <div className="message error">{error}</div>}

      {port && (
        <>
          <div className="cards">
            <div className="total-card">
              <div className="muted small">มูลค่ารวม</div>
              <div className="total">{fmtMoney(port.market_value)}</div>
              <div className="muted small">เงินโอนเข้าสุทธิ {fmtMoney(port.net_deposits)}</div>
            </div>
            <div className="total-card">
              <div className="muted small">ต้นทุน</div>
              <div className="total">{fmtMoney(port.cost)}</div>
            </div>
            <div className="total-card">
              <div className="muted small">กำไร/ขาดทุนยังไม่รับรู้</div>
              <div className="total">
                <Gain amount={port.unrealized} base={port.cost || undefined} />
              </div>
            </div>
            <div className="total-card">
              <div className="muted small">กำไร/ขาดทุนที่รับรู้แล้ว (จากการขาย)</div>
              <div className="total">
                <Gain amount={port.realized} />
              </div>
            </div>
          </div>

          <Unlinked items={unlinked} onRecord={record} />

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
                      ยังไม่มีกองทุน เพิ่มรหัสกองทุนด้านล่าง
                    </td>
                  </tr>
                )}
                {funds.map((f) => (
                  <tr key={f.id} className={f.is_active ? "" : "inactive"}>
                    <td className="f-name">
                      <b>{f.code}</b>
                      {f.name && <div className="muted small">{f.name}</div>}
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
                          {fmtWhen(f.nav_date, false)}
                          {f.nav_source === "trade" ? " (จากรายการ)" : ""}
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
          <AddFund accountId={port.account_id} onSaved={load} />

          <h2>บันทึกการซื้อ/ขาย</h2>
          <TradeForm
            key={`${port.account_id}-${prefill?.transactionId ?? ""}`}
            port={port}
            accounts={accounts}
            unlinked={unlinked}
            prefill={prefill}
            onSaved={load}
          />

          <h2>ประวัติการซื้อขาย</h2>
          <History items={history} onDelete={deleteTrade} />
        </>
      )}
    </section>
  );
}

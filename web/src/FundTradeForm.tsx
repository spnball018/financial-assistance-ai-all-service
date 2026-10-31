import { useEffect, useState } from "react";
import { api, ApiError } from "./api";
import { fmtMoney, fmtUnits, fmtWhen } from "./format";
import type { Account, FundAccount, TradeNav, UnlinkedTransfer } from "./types";

export function todayInBangkok(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Bangkok" }).format(new Date());
}

export const bangkokDate = (iso: string) => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Bangkok" }).format(new Date(iso));

export const finnomenaUrl = (code: string) => `https://www.finnomena.com/fund/${encodeURIComponent(code)}`;

/** 503 = fetching NAVs is turned off on the server: not an error worth showing. */
export const navOff = (e: unknown) => e instanceof ApiError && e.status === 503;

/** A fund's NAV on the trade date from Finnomena (null while loading, when unknown, or when fetching is off). */
function useTradeNav(code: string, date: string): { nav: TradeNav | null; error: string | null } {
  const [res, setRes] = useState<{ nav: TradeNav | null; error: string | null }>({ nav: null, error: null });
  useEffect(() => {
    setRes({ nav: null, error: null });
    if (!code.trim() || !date) return;
    let live = true;
    // wait for the user to stop typing the code
    const timer = setTimeout(() => {
      api.fundNav(code.trim(), date).then(
        (nav) => live && setRes({ nav, error: null }),
        (e: Error) => live && setRes({ nav: null, error: navOff(e) ? null : e.message }),
      );
    }, 400);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [code, date]);
  return res;
}

/** "NAV on the date from Finnomena", and units worked out from the amount (or the amount from units) to fill in. */
function NavHint({ label, tn, amount, units, onUnits, onAmount }: {
  label?: string;
  tn: { nav: TradeNav | null; error: string | null };
  amount: string;
  units: string;
  onUnits: (units: string, nav: number) => void;
  onAmount?: (amount: string) => void;
}) {
  if (tn.error) return <p className="full small warn-text nav-hint">{label}ดึง NAV ไม่ได้: {tn.error}</p>;
  if (!tn.nav) return null;
  const { nav, date, code } = tn.nav;
  const calcUnits = Number(amount) > 0 ? (Number(amount) / nav).toFixed(4) : null;
  const calcAmount = !calcUnits && Number(units) > 0 ? (Number(units) * nav).toFixed(2) : null;
  return (
    <p className="full small nav-hint">
      {label}NAV {code} วันที่ {fmtWhen(date, false)} จาก{" "}
      <a href={finnomenaUrl(code)} target="_blank" rel="noreferrer">
        Finnomena
      </a>
      : <b>{nav.toFixed(4)}</b>
      {calcUnits && calcUnits !== Number(units).toFixed(4) && (
        <>
          {" "}· {fmtMoney(Number(amount))} ÷ NAV = {fmtUnits(Number(calcUnits))} หน่วย{" "}
          <button type="button" className="link small" onClick={() => onUnits(calcUnits, nav)}>
            ใช้จำนวนนี้
          </button>
        </>
      )}
      {calcAmount && onAmount && (
        <>
          {" "}· {fmtUnits(Number(units))} หน่วย × NAV = {fmtMoney(Number(calcAmount))}{" "}
          <button type="button" className="link small" onClick={() => onAmount(calcAmount)}>
            ใช้ยอดนี้
          </button>
        </>
      )}
    </p>
  );
}

/** held = units the user already had before starting the ledger (a buy with no transfer). */
type Side = "buy" | "sell" | "switch" | "held";
const SIDE_LABEL: Record<Side, string> = { buy: "ซื้อ", sell: "ขาย", switch: "สับเปลี่ยน", held: "ยอดที่มีอยู่แล้ว" };

/** Starts the trade form from a transfer that no trade points at yet. */
export interface Prefill {
  side: "buy" | "sell";
  accountId: number;
  amount: string;
  date: string;
  transactionId: number;
}

const NEW = "new";
const digits = (v: string) => v.replace(/[^\d.]/g, "");

/** Buy, sell, switch, or units already held, in one form: the fund account (or a new one), the fund by its code
 * (a code not in the account yet is added and looked up on Finnomena), and where the money came from or went. */
export function FundTradeForm({ ports, accounts, accountId, prefill, onSaved }: {
  ports: FundAccount[];
  accounts: Account[];
  /** the fund account to start with (the one the page shows) */
  accountId?: number | null;
  prefill?: Prefill | null;
  onSaved: () => void;
}) {
  const firstAccount = prefill?.accountId ?? accountId ?? ports.find((p) => p.is_active)?.account_id;
  const [side, setSide] = useState<Side>(prefill?.side ?? "buy");
  const [acct, setAcct] = useState<number | typeof NEW>(firstAccount ?? NEW);
  const [newAcct, setNewAcct] = useState("");
  const [code, setCode] = useState("");
  const [fundId, setFundId] = useState<number | "">("");
  const [toFundId, setToFundId] = useState<number | "">("");
  const [date, setDate] = useState(prefill?.date ?? todayInBangkok());
  const [amount, setAmount] = useState(prefill?.amount ?? "");
  const [units, setUnits] = useState("");
  const [unitsIn, setUnitsIn] = useState("");
  const [nav, setNav] = useState("");
  // "acc:<id>" = create the transfer, "txn:<id>" = an existing transfer
  const [money, setMoney] = useState(prefill ? `txn:${prefill.transactionId}` : "");
  const [note, setNote] = useState("");
  const [unlinked, setUnlinked] = useState<UnlinkedTransfer[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    if (accountId != null && !prefill) setAcct(accountId);
  }, [accountId, prefill]);
  useEffect(() => {
    setUnlinked([]);
    if (acct === NEW) return;
    api.fundHistory(acct).then((h) => setUnlinked(h.unlinked), () => {});
  }, [acct, ports]);

  const port = acct === NEW ? null : (ports.find((p) => p.account_id === acct) ?? null);
  const byCode = side === "buy" || side === "held";
  const fund = byCode ? port?.funds.find((f) => f.code.toUpperCase() === code.trim().toUpperCase()) : port?.funds.find((f) => f.id === fundId);
  const others = accounts.filter((a) => a.is_active && a.type !== "investment");
  const transfers = unlinked.filter((u) => (side === "sell" ? u.amount < 0 : u.amount > 0));
  const navFrom = useTradeNav(byCode ? code : (fund?.code ?? ""), date);
  const navTo = useTradeNav(side === "switch" ? (port?.funds.find((f) => f.id === toFundId)?.code ?? "") : "", date);

  const valid =
    (acct === NEW ? newAcct.trim() !== "" : true) &&
    (byCode ? code.trim() !== "" : fundId !== "") &&
    Number(amount) > 0 &&
    Number(units) > 0 &&
    date !== "" &&
    (side === "switch" ? toFundId !== "" && toFundId !== fundId && Number(unitsIn) > 0 : side === "held" || money !== "");

  const changeSide = (s: Side) => {
    // only a buy can open a new fund account
    if ((s === "sell" || s === "switch") && acct === NEW && ports.length > 0) setAcct(ports[0].account_id);
    setSide(s);
    setMoney("");
    setFundId("");
    setDone(null);
  };

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
    setDone(null);
    try {
      let accountId = acct;
      if (accountId === NEW) {
        accountId = (await api.createAccount({ name: newAcct.trim(), type: "investment", institution: null, masked_ref: null, purpose: "กองทุน" })).id;
        setAcct(accountId);
        setNewAcct("");
      }
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
          ...(byCode && !fund ? { fund_id: null, account_id: accountId, fund_code: code.trim() } : { fund_id: fund!.id }),
          side: side === "sell" ? "sell" : "buy",
          trade_date: date,
          amount,
          units,
          nav: nav || null,
          counter_account_id: kind === "acc" ? Number(id) : null,
          transaction_id: kind === "txn" ? Number(id) : null,
          held_before: side === "held",
          note: note.trim() || null,
        });
      }
      const fundCode = byCode ? code.trim() : (fund?.code ?? "");
      setDone(`บันทึก${SIDE_LABEL[side]} ${fundCode} ${fmtUnits(Number(units))} หน่วย ${fmtMoney(Number(amount))} แล้ว`);
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
        {(["buy", "sell", "switch", "held"] as const).map((s) => (
          <button type="button" key={s} role="radio" aria-checked={side === s} className={side === s ? "tab active" : "tab"} onClick={() => changeSide(s)}>
            {SIDE_LABEL[s]}
          </button>
        ))}
      </div>
      <label>
        บัญชีกองทุน (บลจ.)
        <select value={acct} onChange={(e) => setAcct(e.target.value === NEW ? NEW : Number(e.target.value))}>
          {ports.map((p) => (
            <option key={p.account_id} value={p.account_id}>
              {p.name}
              {p.is_active ? "" : " (ปิด)"}
            </option>
          ))}
          {(side === "buy" || side === "held") && <option value={NEW}>+ บัญชีกองทุนใหม่…</option>}
        </select>
      </label>
      {acct === NEW ? (
        <label>
          ชื่อบัญชีกองทุนใหม่
          <input value={newAcct} onChange={(e) => setNewAcct(e.target.value)} placeholder="เช่น บัวหลวง, KAsset" required />
        </label>
      ) : (
        <span className="form-grid-gap" />
      )}

      {byCode ? (
        <label>
          รหัสกองทุน
          <input
            list="fund-codes"
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            placeholder="เช่น B-CHINAARMF"
            autoCapitalize="characters"
            required
          />
          <datalist id="fund-codes">
            {port?.funds.filter((f) => f.is_active).map((f) => (
              <option key={f.id} value={f.code}>
                {f.name ?? ""}
              </option>
            ))}
          </datalist>
          <span className="small muted">
            {fund ? (fund.name ?? "") : code.trim() ? "กองทุนใหม่: ระบบจะเพิ่มให้และดึงชื่อจาก Finnomena" : ""}
          </span>
        </label>
      ) : (
        <label>
          {side === "switch" ? "จากกองทุน" : "กองทุน"}
          <select value={fundId} onChange={(e) => setFundId(e.target.value ? Number(e.target.value) : "")} required>
            <option value="">— เลือกกองทุน —</option>
            {(port?.funds ?? []).filter((f) => f.units > 0).map((f) => (
              <option key={f.id} value={f.id}>
                {f.code} · ถือ {fmtUnits(f.units)} หน่วย
              </option>
            ))}
          </select>
        </label>
      )}

      {side === "switch" ? (
        <label>
          ไปกองทุน
          <select value={toFundId} onChange={(e) => setToFundId(e.target.value ? Number(e.target.value) : "")} required>
            <option value="">— เลือกกองทุน —</option>
            {(port?.funds ?? []).filter((f) => f.is_active && f.id !== fundId).map((f) => (
              <option key={f.id} value={f.id}>
                {f.code}
                {f.name ? ` · ${f.name}` : ""}
              </option>
            ))}
          </select>
        </label>
      ) : side === "held" ? (
        <span className="form-grid-gap" />
      ) : (
        <label>
          {side === "buy" ? "โอนเงินซื้อจากบัญชี" : "เงินที่ขายได้เข้าบัญชี"}
          <select value={money} onChange={(e) => pickTransfer(e.target.value)} required>
            <option value="">— เลือก —</option>
            <optgroup label="บันทึกการโอนให้ด้วย">
              {others.map((a) => (
                <option key={a.id} value={`acc:${a.id}`}>
                  {a.name}
                </option>
              ))}
            </optgroup>
            {transfers.length > 0 && (
              <optgroup label="รายการโอนที่มีอยู่แล้ว (เช่น จากอีเมลธนาคาร)">
                {transfers.map((u) => (
                  <option key={u.transaction_id} value={`txn:${u.transaction_id}`}>
                    {fmtWhen(u.occurred_at, u.has_time)} · {u.other_account} · {fmtMoney(Math.abs(u.amount))}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </label>
      )}

      <label>
        {side === "held" ? "วันที่ซื้อ (หรือวันที่นับยอด)" : "วันที่ทำรายการ"}
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
      </label>
      <label>
        {{ buy: "จำนวนเงินที่ซื้อ (บาท)", sell: "เงินที่ได้จากการขาย (บาท)", switch: "มูลค่าที่สับเปลี่ยน (บาท)", held: "ต้นทุนรวม (บาท)" }[side]}
        <input inputMode="decimal" value={amount} disabled={money.startsWith("txn:")} onChange={(e) => setAmount(digits(e.target.value))} placeholder="0.00" required />
      </label>
      <label>
        {{ buy: "จำนวนหน่วยที่ได้", sell: "จำนวนหน่วยที่ขาย", switch: "หน่วยที่ขายออก", held: "จำนวนหน่วยที่ถืออยู่" }[side]}
        <input inputMode="decimal" value={units} onChange={(e) => setUnits(digits(e.target.value))} placeholder="0.0000" required />
      </label>
      {side === "switch" ? (
        <label>
          หน่วยที่ได้รับ
          <input inputMode="decimal" value={unitsIn} onChange={(e) => setUnitsIn(digits(e.target.value))} placeholder="0.0000" required />
        </label>
      ) : (
        <label>
          NAV ต่อหน่วย
          <input inputMode="decimal" value={nav} onChange={(e) => setNav(digits(e.target.value))} placeholder="ไม่บังคับ: ยอดเงิน ÷ หน่วย" />
        </label>
      )}
      {side === "switch" ? (
        <>
          <NavHint label="ขายออก: " tn={navFrom} amount={amount} units={units} onUnits={(u) => setUnits(u)} />
          <NavHint label="ได้รับ: " tn={navTo} amount={amount} units={unitsIn} onUnits={(u) => setUnitsIn(u)} />
        </>
      ) : (
        <NavHint
          tn={navFrom}
          amount={amount}
          units={units}
          onUnits={(u, n) => {
            setUnits(u);
            setNav(n.toFixed(4));
          }}
          onAmount={money.startsWith("txn:") ? undefined : setAmount}
        />
      )}
      <label className="full">
        บันทึกเพิ่มเติม
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="ไม่บังคับ" />
      </label>
      <p className="full muted small">
        {
          {
            buy: "เงินซื้อเป็นการโอนจากบัญชีของคุณเข้าบัญชีกองทุน (ไม่นับเป็นรายจ่าย) ถ้าการโอนนี้มาจากอีเมลธนาคารแล้ว ให้เลือกจาก \"รายการโอนที่มีอยู่แล้ว\" จะได้ไม่บันทึกซ้ำ จำนวนหน่วยที่ได้จริงดูจากใบยืนยันการซื้อ (ค่าธรรมเนียมทำให้ต่างจากที่คำนวณได้)",
            sell: "เงินที่ขายได้โอนออกจากบัญชีกองทุนไปบัญชีที่เลือก กำไร/ขาดทุนคิดจากต้นทุนเฉลี่ย",
            switch: "สับเปลี่ยนระหว่างกองทุนในบัญชีเดียวกัน ไม่มีเงินเข้าหรือออกจากบัญชี",
            held: "หน่วยที่ถืออยู่ก่อนเริ่มใช้ระบบ ไม่มีการโอนเงิน ถ้าไม่รู้ต้นทุนจริง ใส่จำนวนหน่วยแล้วกด \"ใช้ยอดนี้\" เพื่อใช้มูลค่าตาม NAV ของวันที่นั้นเป็นต้นทุน",
          }[side]
        }
      </p>
      <div className="full actions">
        <button className="primary" type="submit" disabled={!valid || saving}>
          {saving ? "กำลังบันทึก…" : `บันทึก${SIDE_LABEL[side]}`}
        </button>
        {done && <span className="ok-text small">{done}</span>}
        {error && <span className="error-text small">{error}</span>}
      </div>
    </form>
  );
}

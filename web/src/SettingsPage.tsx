import { useEffect, useState } from "react";
import { api } from "./api";
import { EmailSourcesSection, KtcPasswordSection, RulesSection } from "./SettingsExtras";
import type { Account, AccountInput, AccountType } from "./types";

const TYPE_LABEL: Record<AccountType, string> = {
  bank: "ธนาคาร",
  credit_card: "บัตรเครดิต",
  ewallet: "e-wallet",
  cash: "เงินสด",
  investment: "ลงทุน",
};

const PLACEHOLDER = { name: "ชื่อ", institution: "สถาบัน", masked_ref: "เลขท้าย", purpose: "ใช้ทำอะไร" } as const;

const EMPTY: AccountInput = { name: "", type: "bank", institution: null, masked_ref: null, purpose: null };

function AccountRow({ account, onSaved }: { account: Account; onSaved: (a: Account) => void }) {
  const [draft, setDraft] = useState(account);
  const [error, setError] = useState<string | null>(null);
  const changed = (Object.keys(draft) as (keyof Account)[]).filter((k) => draft[k] !== account[k]);

  useEffect(() => setDraft(account), [account]);

  const save = async () => {
    setError(null);
    try {
      onSaved(await api.updateAccount(account.id, Object.fromEntries(changed.map((k) => [k, draft[k]]))));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const field = (k: "name" | "institution" | "masked_ref" | "purpose") => (
    <input
      value={draft[k] ?? ""}
      onChange={(e) => setDraft({ ...draft, [k]: e.target.value || (k === "name" ? "" : null) })}
      placeholder={PLACEHOLDER[k]}
      aria-label={PLACEHOLDER[k]}
    />
  );

  return (
    <tr className={account.is_active ? undefined : "inactive"}>
      <td className="s-name">{field("name")}</td>
      <td className="s-type">
        <select value={draft.type} onChange={(e) => setDraft({ ...draft, type: e.target.value as AccountType })}>
          {Object.entries(TYPE_LABEL).map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
      </td>
      <td className="s-inst">{field("institution")}</td>
      <td className="s-ref">{field("masked_ref")}</td>
      <td className="s-purpose">{field("purpose")}</td>
      <td className="s-active">
        <label className="small">
          <input
            type="checkbox"
            checked={draft.is_active}
            onChange={(e) => setDraft({ ...draft, is_active: e.target.checked })}
          />{" "}
          ใช้งาน
        </label>
      </td>
      <td className="s-save nowrap">
        <button className="primary" disabled={changed.length === 0} onClick={save}>
          บันทึก
        </button>
        {changed.length > 0 && (
          <button className="link" onClick={() => setDraft(account)}>
            ยกเลิก
          </button>
        )}
        {error && <div className="error-text small">{error}</div>}
      </td>
    </tr>
  );
}

export function SettingsPage() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [draft, setDraft] = useState<AccountInput>(EMPTY);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.accounts().then(setAccounts, (e: Error) => setError(e.message));
  }, []);

  const add = async () => {
    setError(null);
    try {
      const a = await api.createAccount(draft);
      setAccounts((prev) => [...prev, a]);
      setDraft(EMPTY);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <section>
      <h2>บัญชีของฉัน</h2>
      <p className="muted small">
        <b>เลขท้าย</b> ใช้จับคู่ว่าการโอนจาก KBank เข้าบัญชีไหนของคุณ (ไม่นับเป็นรายจ่าย) ใส่แค่เลขท้ายอย่างน้อย 4 ตัว
        เช่น <code>7722</code> หรือ <code>x5204</code> ห้ามใส่เลขบัญชีเต็ม · บัญชีลบไม่ได้ ใช้ปิด "ใช้งาน" แทน
      </p>
      {error && <div className="message error">{error}</div>}
      <div className="table-wrap">
        <table className="settings">
          <thead>
            <tr>
              <th>ชื่อ</th>
              <th>ประเภท</th>
              <th>สถาบัน</th>
              <th>เลขท้าย</th>
              <th>ใช้ทำอะไร</th>
              <th></th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {accounts.map((a) => (
              <AccountRow
                key={a.id}
                account={a}
                onSaved={(saved) => setAccounts((prev) => prev.map((x) => (x.id === saved.id ? saved : x)))}
              />
            ))}
            <tr className="new-row">
              <td className="s-name">
                <input
                  placeholder="ชื่อบัญชีใหม่"
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                />
              </td>
              <td className="s-type">
                <select value={draft.type} onChange={(e) => setDraft({ ...draft, type: e.target.value as AccountType })}>
                  {Object.entries(TYPE_LABEL).map(([v, l]) => (
                    <option key={v} value={v}>
                      {l}
                    </option>
                  ))}
                </select>
              </td>
              <td className="s-inst">
                <input
                  placeholder="สถาบัน"
                  value={draft.institution ?? ""}
                  onChange={(e) => setDraft({ ...draft, institution: e.target.value || null })}
                />
              </td>
              <td className="s-ref">
                <input
                  placeholder="เลขท้าย"
                  value={draft.masked_ref ?? ""}
                  onChange={(e) => setDraft({ ...draft, masked_ref: e.target.value || null })}
                />
              </td>
              <td className="s-purpose">
                <input
                  placeholder="ใช้ทำอะไร"
                  value={draft.purpose ?? ""}
                  onChange={(e) => setDraft({ ...draft, purpose: e.target.value || null })}
                />
              </td>
              <td></td>
              <td className="s-save">
                <button className="primary" disabled={!draft.name.trim()} onClick={add}>
                  เพิ่มบัญชี
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <EmailSourcesSection accounts={accounts} />
      <KtcPasswordSection />
      <RulesSection />
    </section>
  );
}

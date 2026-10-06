import { useEffect, useState } from "react";
import { api } from "./api";
import { fmtWhen } from "./format";
import type { Account, EmailSource, MerchantRule } from "./types";

const SOURCE_LABEL: Record<EmailSource["source"], string> = {
  kbank_email: "KBank (อีเมล K PLUS)",
  ktc_pdf: "บัตร KTC (PDF ในอีเมล)",
  shopee_email: "Shopee (อีเมลคำสั่งซื้อ)",
  lazada_email: "Lazada (อีเมลคำสั่งซื้อ)",
};

function SourceRow({ src, accounts, onSaved }: { src: EmailSource; accounts: Account[]; onSaved: (s: EmailSource) => void }) {
  const [draft, setDraft] = useState(src);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setDraft(src), [src]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(src);
  const save = async () => {
    setError(null);
    try {
      onSaved(await api.updateEmailSource(draft));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <tr className={src.is_active ? undefined : "inactive"}>
      <td className="s-name">{SOURCE_LABEL[src.source]}</td>
      <td className="s-inst">
        <input
          placeholder="Gmail label"
          value={draft.gmail_label ?? ""}
          onChange={(e) => setDraft({ ...draft, gmail_label: e.target.value || null })}
        />
      </td>
      <td className="s-ref">
        <input
          placeholder="ผู้ส่ง (ไม่บังคับ)"
          value={draft.sender_filter ?? ""}
          onChange={(e) => setDraft({ ...draft, sender_filter: e.target.value || null })}
        />
      </td>
      <td className="s-type">
        <select
          value={draft.account_id ?? ""}
          onChange={(e) => setDraft({ ...draft, account_id: e.target.value ? Number(e.target.value) : null })}
        >
          <option value="">— บัญชี —</option>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      </td>
      <td className="s-active">
        <label className="small">
          <input type="checkbox" checked={draft.is_active} onChange={(e) => setDraft({ ...draft, is_active: e.target.checked })} />{" "}
          เปิด
        </label>
      </td>
      <td className="s-save">
        <button className="primary" disabled={!dirty} onClick={save}>
          บันทึก
        </button>
        {error && <div className="error-text small">{error}</div>}
      </td>
    </tr>
  );
}

export function EmailSourcesSection({ accounts }: { accounts: Account[] }) {
  const [sources, setSources] = useState<EmailSource[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.emailSources().then(setSources, (e: Error) => setError(e.message));
  }, []);
  return (
    <section className="settings-section">
      <h2>แหล่งอีเมล</h2>
      <p className="muted small">
        Gmail label ที่ระบบดึงอีเมลของแต่ละแหล่ง (กด Refresh ในหน้าตรวจรายการเพื่อดึง) แหล่งที่ยังไม่มีตัวแกะข้อมูลจะถูกข้าม
        และแจ้งในผลการดึง
      </p>
      {error && <div className="message error">{error}</div>}
      <div className="table-wrap">
        <table className="settings">
          <thead>
            <tr>
              <th>แหล่ง</th>
              <th>Gmail label</th>
              <th>ผู้ส่ง</th>
              <th>บัญชี</th>
              <th></th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {sources.map((s) => (
              <SourceRow
                key={s.source}
                src={s}
                accounts={accounts}
                onSaved={(saved) => setSources((prev) => prev.map((x) => (x.source === saved.source ? saved : x)))}
              />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function RulesSection() {
  const [rules, setRules] = useState<MerchantRule[]>([]);
  const [error, setError] = useState<string | null>(null);
  const load = () => api.rules().then(setRules, (e: Error) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  const forget = async (r: MerchantRule) => {
    if (!window.confirm(`ลืมกฎของ "${r.merchant_key}" ใช่ไหม? รายการเดิมไม่เปลี่ยน แต่ครั้งหน้าจะไม่ถูกจัดอัตโนมัติ`)) return;
    try {
      await api.deleteRule(r.merchant_key);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const describe = (r: MerchantRule) =>
    r.action === "category" ? `หมวด: ${r.category_name}` : r.action === "transfer" ? `โอน → ${r.account_name}` : "ไม่ต้องจำ (ถามทุกครั้ง)";

  return (
    <section className="settings-section">
      <h2>กฎที่ระบบจำไว้ ({rules.length})</h2>
      <p className="muted small">
        เกิดจากการยืนยันรายการ ผู้รับที่มี <code>|1234</code> ต่อท้ายคือกฎการโอนที่จำแยกตามเลขท้ายบัญชีปลายทาง
        แก้กฎได้โดยเปลี่ยนหมวดของรายการแล้วยืนยันใหม่ หรือลบกฎทิ้ง
      </p>
      {error && <div className="message error">{error}</div>}
      {rules.length === 0 ? (
        <p className="muted">ยังไม่มีกฎ ยืนยันรายการในหน้าตรวจรายการแล้วระบบจะเริ่มจำ</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ผู้รับ</th>
                <th>กฎ</th>
                <th className="num">ใช้ไป</th>
                <th>ยืนยันล่าสุด</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rules.map((r) => (
                <tr key={r.merchant_key}>
                  <td className="c-merchant">{r.merchant_key}</td>
                  <td className="c-cat">
                    <span className={r.action === "transfer" ? "badge badge-transfer" : r.action === "ask" ? "badge badge-none" : "badge badge-memory"}>
                      {describe(r)}
                    </span>
                  </td>
                  <td className="c-amount num">{r.hit_count}</td>
                  <td className="c-date muted small">{fmtWhen(r.last_confirmed_at, true)}</td>
                  <td className="c-act">
                    <button className="link" onClick={() => forget(r)}>
                      ลบ
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function KtcPasswordSection() {
  const [status, setStatus] = useState<{ is_set: boolean; updated_at: string | null } | null>(null);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const load = () => api.ktcPasswordStatus().then(setStatus, (e: Error) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  const save = async () => {
    setError(null);
    setSaved(false);
    try {
      await api.setKtcPassword(password);
      setPassword("");  // never keep it around in the page
      setSaved(true);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const remove = async () => {
    if (!window.confirm("ลบรหัส PDF ของ KTC? การ sync จะอ่านบิลบัตร KTC ไม่ได้จนกว่าจะตั้งใหม่")) return;
    await api.deleteKtcPassword().catch((e: Error) => setError(e.message));
    await load();
  };

  return (
    <section className="settings-section">
      <h2>รหัส PDF บิลบัตร KTC</h2>
      <p className="muted small">
        e-Statement ของ KTC ล็อกรหัสไว้ ระบบใช้รหัสนี้เปิดอ่านตอน sync เก็บแบบเข้ารหัส และไม่มีหน้าไหนแสดงรหัสกลับมา
      </p>
      <div className="opening">
        <span className={status?.is_set ? "badge badge-memory" : "badge badge-none"}>
          {status === null ? "…" : status.is_set ? `ตั้งแล้ว (${status.updated_at ? fmtWhen(status.updated_at, true) : ""})` : "ยังไม่ได้ตั้ง"}
        </span>
        <input
          type="password"
          autoComplete="off"
          placeholder={status?.is_set ? "ใส่รหัสใหม่เพื่อเปลี่ยน" : "รหัส PDF"}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-label="รหัส PDF ของ KTC"
        />
        <button className="primary" disabled={!password} onClick={save}>
          บันทึก
        </button>
        {status?.is_set && (
          <button className="link" onClick={remove}>
            ลบ
          </button>
        )}
        {saved && <span className="ok-text small">บันทึกแล้ว</span>}
        {error && <span className="error-text small">{error}</span>}
      </div>
    </section>
  );
}

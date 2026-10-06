import { useEffect, useState } from "react";
import { api } from "./api";
import { fmtWhen } from "./format";
import type { ApiToken, CreatedApiToken, User } from "./types";

const EXPIRY_OPTIONS: { label: string; days: number | null }[] = [
  { label: "30 วัน", days: 30 },
  { label: "90 วัน", days: 90 },
  { label: "1 ปี", days: 365 },
  { label: "ไม่หมดอายุ", days: null },
];

function tokenState(t: ApiToken): string {
  if (t.revoked_at) return "ยกเลิกแล้ว";
  if (t.expires_at && new Date(t.expires_at) < new Date()) return "หมดอายุ";
  return "ใช้งานได้";
}

export function ApiTokensSection() {
  const [tokens, setTokens] = useState<ApiToken[]>([]);
  const [name, setName] = useState("");
  const [days, setDays] = useState<number | null>(90);
  const [created, setCreated] = useState<CreatedApiToken | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = () => api.tokens().then(setTokens, (e: Error) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  const create = async () => {
    setError(null);
    setCopied(false);
    try {
      setCreated(await api.createToken(name.trim(), days));
      setName("");
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const revoke = async (t: ApiToken) => {
    if (!confirm(`ยกเลิก token "${t.name}"? โปรแกรมที่ใช้ token นี้จะเรียก API ไม่ได้อีก`)) return;
    try {
      await api.revokeToken(t.id);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const copy = async () => {
    if (!created) return;
    await navigator.clipboard.writeText(created.token);
    setCopied(true);
  };

  return (
    <section className="settings-section">
      <h2>API token</h2>
      <p className="muted small">
        ใช้เรียก API จากโปรแกรมอื่น (เช่น Bruno หรือสคริปต์) ด้วย header <code>Authorization: Bearer &lt;token&gt;</code>{" "}
        token ทำได้ทุกอย่างแทนคุณ ยกเว้นสร้าง token และจัดการผู้ใช้
      </p>
      {error && <div className="message error">{error}</div>}
      {created && (
        <div className="message ok token-once" role="status">
          <div>
            token ใหม่ "{created.name}" (แสดงครั้งเดียว เก็บไว้ในที่ปลอดภัย):
            <code className="token-value">{created.token}</code>
          </div>
          <div className="token-actions">
            <button onClick={copy}>{copied ? "คัดลอกแล้ว" : "คัดลอก"}</button>
            <button className="link" onClick={() => setCreated(null)} aria-label="ปิด">
              ✕
            </button>
          </div>
        </div>
      )}
      <div className="toolbar">
        <input placeholder="ชื่อ token เช่น bruno" value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
        <select value={days ?? ""} onChange={(e) => setDays(e.target.value ? Number(e.target.value) : null)}>
          {EXPIRY_OPTIONS.map((o) => (
            <option key={o.label} value={o.days ?? ""}>
              {o.label}
            </option>
          ))}
        </select>
        <button className="primary" disabled={!name.trim()} onClick={create}>
          สร้าง token
        </button>
      </div>
      {tokens.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ชื่อ</th>
                <th>token</th>
                <th>สร้างเมื่อ</th>
                <th>ใช้ล่าสุด</th>
                <th>หมดอายุ</th>
                <th>สถานะ</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {tokens.map((t) => (
                <tr key={t.id} className={tokenState(t) === "ใช้งานได้" ? undefined : "inactive"}>
                  <td>{t.name}</td>
                  <td className="muted small">
                    <code>{t.token_prefix}…</code>
                  </td>
                  <td className="muted small">{fmtWhen(t.created_at, true)}</td>
                  <td className="muted small">{t.last_used_at ? fmtWhen(t.last_used_at, true) : "—"}</td>
                  <td className="muted small">{t.expires_at ? fmtWhen(t.expires_at, false) : "ไม่หมดอายุ"}</td>
                  <td className="small">{tokenState(t)}</td>
                  <td>
                    {!t.revoked_at && (
                      <button className="link" onClick={() => revoke(t)}>
                        ยกเลิก
                      </button>
                    )}
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

export function UsersSection({ meId }: { meId: number }) {
  const [users, setUsers] = useState<User[]>([]);
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const load = () => api.users().then(setUsers, (e: Error) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <section className="settings-section">
      <h2>ผู้ใช้</h2>
      <p className="muted small">
        เพิ่มสมาชิกด้วยอีเมล Google ของเขา แต่ละคนเห็นเฉพาะข้อมูลของตัวเอง ปิดการใช้งานแล้วจะออกจากระบบทันที (ข้อมูลยังอยู่)
      </p>
      {error && <div className="message error">{error}</div>}
      <div className="toolbar">
        <input type="email" placeholder="อีเมล Google" value={email} onChange={(e) => setEmail(e.target.value)} />
        <button className="primary" disabled={!email.includes("@")} onClick={() => run(async () => {
          await api.addUser(email.trim());
          setEmail("");
        })}>
          เพิ่มสมาชิก
        </button>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>อีเมล</th>
              <th>ชื่อ</th>
              <th>สิทธิ์</th>
              <th>เข้าล่าสุด</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className={u.is_active ? undefined : "inactive"}>
                <td>{u.email}</td>
                <td>{u.name ?? <span className="muted">—</span>}</td>
                <td>
                  <select
                    value={u.role}
                    disabled={u.id === meId}
                    onChange={(e) => run(() => api.updateUser(u.id, { role: e.target.value as User["role"] }))}
                  >
                    <option value="member">สมาชิก</option>
                    <option value="admin">ผู้ดูแล</option>
                  </select>
                </td>
                <td className="muted small">{u.last_login_at ? fmtWhen(u.last_login_at, true) : "ยังไม่เคยเข้า"}</td>
                <td>
                  {u.id !== meId && (
                    <button className="link" onClick={() => run(() => api.updateUser(u.id, { is_active: !u.is_active }))}>
                      {u.is_active ? "ปิดการใช้งาน" : "เปิดการใช้งาน"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

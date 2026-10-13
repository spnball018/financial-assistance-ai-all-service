import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { fmtMoney } from "./format";
import { shrinkImage } from "./image";
import type { Account, WalletApplyResult, WalletEntry, WalletEntryKind, WalletScreenshot } from "./types";

const KIND_LABEL: Record<WalletEntryKind, string> = {
  payment: "จ่ายเงิน",
  top_up: "เติมเงิน / ชำระบัตร",
  transfer_out: "โอนออก",
  transfer_in: "รับโอน",
  refund: "คืนเงิน",
  cashback: "เงินคืน",
  other: "อื่นๆ",
};
const TOPUP_LABEL: Record<WalletApplyResult["top_ups"][number]["status"], string> = {
  linked: "จับคู่กับรายการธนาคารแล้ว (เปลี่ยนเป็นโอนเข้าบัญชีนี้)",
  already: "จับคู่ไว้แล้ว",
  not_found: "ไม่พบรายการธนาคารที่ยอดและเวลาตรง",
  ambiguous: "พบรายการธนาคารที่ตรงหลายรายการ ไม่ได้จับคู่",
  confirmed: "รายการธนาคารยืนยันหรือเลือกหมวดไว้แล้ว ไม่ได้เปลี่ยน",
};

type Row = WalletEntry & { include: boolean };
type Shot = {
  key: string;
  name: string;
  preview: string;
  shot: WalletScreenshot | null;
  rows: Row[];
  error: string | null;
  saving: boolean;
  result: WalletApplyResult | null;
};

/** Upload screenshots of a wallet app's history, check what was read, then record it. */
export function WalletImportPanel({ accounts, onClose, onSaved }: {
  accounts: Account[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const wallets = accounts.filter((a) => (a.type === "ewallet" || a.type === "credit_card") && a.is_active);
  const [accountId, setAccountId] = useState<number | null>(
    (wallets.find((a) => /true ?money/i.test(a.institution ?? a.name)) ?? wallets[0])?.id ?? null,
  );
  const [shots, setShots] = useState<Shot[]>([]);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const patch = (key: string, change: Partial<Shot>) =>
    setShots((list) => {
      const next = list.map((s) => (s.key === key ? { ...s, ...change } : s));
      // the same image uploaded twice comes back as the same screenshot: keep one card
      const id = change.shot?.id;
      return id === undefined ? next : next.filter((s) => s.key === key || s.shot?.id !== id);
    });

  // screenshots read earlier but not recorded yet (e.g. the page was reloaded): pick up where we left off
  useEffect(() => {
    if (accountId === null) return;
    api.walletScreenshots(accountId).then((list) => {
      const pending = list.filter((s) => s.applied_at === null);
      setShots((cur) => [
        ...cur.filter((s) => s.shot === null || s.shot.account_id === accountId),
        ...pending
          .filter((p) => !cur.some((s) => s.shot?.id === p.id))
          .map((p) => ({
            key: `saved-${p.id}`, name: `รูปที่อัปโหลดไว้ #${p.id}`, preview: `/api/wallet-screenshots/${p.id}/image`,
            shot: null, rows: [], error: null, saving: false, result: null, ...fromReading(p),
          })),
      ]);
    }, () => undefined);
  }, [accountId]);

  const discard = async (s: Shot) => {
    if (s.shot && !s.result) {
      if (!window.confirm("ทิ้งรูปนี้? รายการในรูปจะไม่ถูกบันทึก")) return;
      try {
        await api.discardWalletScreenshot(s.shot.id);
      } catch (e) {
        patch(s.key, { error: (e as Error).message });
        return;
      }
    }
    setShots((list) => list.filter((x) => x.key !== s.key));
  };

  const fromReading = (shot: WalletScreenshot): Partial<Shot> =>
    shot.ocr_status === "done"
      ? {
          shot,
          // a row whose date is not on this screen is usually on the previous screenshot
          rows: (shot.ocr_result?.entries ?? []).map((e) => ({ ...e, include: e.date_shown && e.amount > 0 })),
          error: null,
        }
      : { shot, rows: [], error: `อ่านรูปไม่สำเร็จ (${shot.ocr_error ?? "ไม่ทราบสาเหตุ"})` };

  const onFiles = async (files: FileList | null) => {
    if (!files || accountId === null) return;
    const added: Shot[] = [...files].map((f, i) => ({
      key: `${Date.now()}-${i}`, name: f.name, preview: URL.createObjectURL(f), shot: null, rows: [], error: null,
      saving: false, result: null,
    }));
    setShots((list) => [...list, ...added]);
    if (input.current) input.current.value = "";
    for (const [i, f] of [...files].entries()) {
      try {
        patch(added[i].key, fromReading(await api.uploadWalletScreenshot(accountId, await shrinkImage(f))));
      } catch (e) {
        patch(added[i].key, { error: (e as Error).message });
      }
    }
  };

  const reread = async (s: Shot) => {
    if (!s.shot) return;
    patch(s.key, { error: null });
    try {
      patch(s.key, fromReading(await api.rereadWalletScreenshot(s.shot.id)));
    } catch (e) {
      patch(s.key, { error: (e as Error).message });
    }
  };

  const save = async (s: Shot) => {
    if (!s.shot) return;
    patch(s.key, { saving: true, error: null });
    try {
      const entries = s.rows.filter((r) => r.include).map(({ include: _, ...e }) => e);
      patch(s.key, { result: await api.applyWalletScreenshot(s.shot.id, entries), saving: false });
      onSaved();
    } catch (e) {
      patch(s.key, { error: (e as Error).message, saving: false });
    }
  };

  const edit = (s: Shot, i: number, change: Partial<Row>) =>
    patch(s.key, { rows: s.rows.map((r, j) => (j === i ? { ...r, ...change } : r)) });

  return (
    <div className="overlay" onClick={onClose}>
      <div className="panel wide" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="panel-head">
          <div>
            <div className="merchant">นำเข้ารายการจากรูปหน้าจอแอป (wallet / บัตรเครดิต)</div>
            <div className="muted small">
              ถ่ายรูปหน้าประวัติรายการในแอป ให้แต่ละรูปซ้อนกันเล็กน้อย รายการเดิมจะไม่ถูกบันทึกซ้ำ ·
              การเติมเงินหรือชำระบัตรจะจับคู่กับรายการธนาคารแล้วเปลี่ยนเป็นการโอนเข้าบัญชีนี้
            </div>
          </div>
          <div className="spacer" />
          <button className="link" onClick={onClose} aria-label="ปิด">
            ✕
          </button>
        </div>
        {wallets.length === 0 ? (
          <p className="muted">ยังไม่มีบัญชีประเภท e-wallet หรือบัตรเครดิต เพิ่มได้ในหน้าตั้งค่า</p>
        ) : (
          <div className="toolbar">
            <label className="small">
              บัญชี{" "}
              <select value={accountId ?? ""} onChange={(e) => setAccountId(Number(e.target.value))}>
                {wallets.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </label>
            <button className="primary" onClick={() => input.current?.click()} disabled={accountId === null}>
              เลือกรูป
            </button>
            <input
              ref={input}
              type="file"
              accept="image/jpeg,image/png,image/webp,image/heic,image/heif"
              multiple
              hidden
              onChange={(e) => onFiles(e.target.files)}
            />
          </div>
        )}
        {shots.map((s) => (
          <div key={s.key} className="wallet-shot">
            <img src={s.preview} alt={s.name} />
            <div className="wallet-shot-body">
              {!s.shot && !s.error && <p className="muted">กำลังอ่านรูป…</p>}
              {s.error && (
                <div className="message error">
                  {s.error}{" "}
                  {s.shot && (
                    <button className="link" onClick={() => reread(s)}>
                      อ่านใหม่
                    </button>
                  )}
                </div>
              )}
              {s.rows.length > 0 && (
                <table className="wallet-entries">
                  <thead>
                    <tr>
                      <th></th>
                      <th>เวลา</th>
                      <th>รายการ</th>
                      <th>ประเภท</th>
                      <th className="num">ยอด</th>
                    </tr>
                  </thead>
                  <tbody>
                    {s.rows.map((r, i) => (
                      <tr key={i} className={[r.include ? "" : "muted", r.uncertain ? "uncertain" : ""].join(" ").trim() || undefined}>
                        <td>
                          <input type="checkbox" aria-label="บันทึกรายการนี้" checked={r.include}
                            disabled={!!s.result} onChange={(e) => edit(s, i, { include: e.target.checked })} />
                        </td>
                        <td>
                          <input value={r.occurred_at} size={16} disabled={!!s.result}
                            onChange={(e) => edit(s, i, { occurred_at: e.target.value, date_shown: true })} />
                          {!r.date_shown && <div className="small warn-text">ไม่เห็นวันที่ในรูปนี้ (น่าจะอยู่ในรูปก่อนหน้า)</div>}
                        </td>
                        <td>
                          <input value={r.title} disabled={!!s.result} onChange={(e) => edit(s, i, { title: e.target.value })} />
                        </td>
                        <td>
                          <select value={r.kind} disabled={!!s.result}
                            onChange={(e) => {
                              const kind = e.target.value as WalletEntryKind;
                              const into = ["top_up", "transfer_in", "refund", "cashback"].includes(kind);
                              edit(s, i, { kind, direction: into ? "in" : kind === "other" ? r.direction : "out" });
                            }}>
                            {Object.entries(KIND_LABEL).map(([k, label]) => (
                              <option key={k} value={k}>
                                {label}
                              </option>
                            ))}
                          </select>
                          {r.kind === "other" && (
                            <select value={r.direction} disabled={!!s.result}
                              onChange={(e) => edit(s, i, { direction: e.target.value as "in" | "out" })}>
                              <option value="out">เงินออก</option>
                              <option value="in">เงินเข้า</option>
                            </select>
                          )}
                        </td>
                        <td className="num">
                          {r.direction === "out" ? "−" : "+"}
                          <input type="number" min="0" step="0.01" value={r.amount} disabled={!!s.result}
                            onChange={(e) => edit(s, i, { amount: Number(e.target.value), uncertain: false })} />
                          {r.uncertain && <div className="small warn-text">อ่านไม่ชัด ตรวจยอด/เวลา</div>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {s.shot?.ocr_status === "done" && s.rows.length === 0 && <p className="muted">ไม่พบรายการในรูปนี้</p>}
              {s.shot?.ocr_result?.balance != null && (
                <p className="muted small">ยอดคงเหลือในรูป {fmtMoney(s.shot.ocr_result.balance)}</p>
              )}
              <div className="toolbar">
                {s.rows.length > 0 && !s.result && (
                  <button className="primary" disabled={s.saving} onClick={() => save(s)}>
                    {s.saving ? "กำลังบันทึก…" : `บันทึก ${s.rows.filter((r) => r.include).length} รายการ`}
                  </button>
                )}
                {(s.shot || s.error) && (
                  <button className="link" onClick={() => discard(s)}>
                    {s.result ? "ซ่อน" : "ทิ้งรูปนี้"}
                  </button>
                )}
              </div>
              {s.result && (
                <div className="message ok">
                  บันทึกใหม่ {s.result.new} รายการ (รอตรวจ){s.result.duplicate > 0 && ` · มีอยู่แล้ว ${s.result.duplicate}`}
                  {s.result.top_ups.map((t, i) => (
                    <div key={i} className="small">
                      เติมเงิน {fmtMoney(t.amount)}: {TOPUP_LABEL[t.status]}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

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

const RETRY_REASON: Record<string, string> = {
  rate_limit: "AI ถูกจำกัดจำนวนครั้ง",
  server: "บริการ AI ขัดข้องชั่วคราว",
  timeout: "AI ตอบช้าเกิน 60 วินาที",
};
const OCR_ERROR: Record<string, string> = {
  Stalled: "ค้างนานเกินไป เซิร์ฟเวอร์อาจรีสตาร์ตระหว่างอ่าน",
  ReadTimeout: "AI ตอบช้าเกินไปทุกครั้งที่ลอง",
  ConnectTimeout: "ติดต่อ AI ไม่ได้",
  TimeoutException: "AI ตอบช้าเกินไปทุกครั้งที่ลอง",
  ServerError: "บริการ AI ขัดข้อง",
  ClientError: "AI ปฏิเสธคำขอ (โควตาหมดหรือ key ไม่ถูกต้อง)",
  ValueError: "AI ไม่ส่งผลที่อ่านได้",
  ValidationError: "AI ส่งผลที่อ่านไม่ได้",
};

type Row = WalletEntry & { include: boolean };
type Shot = {
  key: string;
  name: string;
  preview: string;
  phase: "shrinking" | "uploading" | null; // in the browser, before the server has the image
  bytes: number | null; // what is being uploaded, after shrinking
  phaseAt: number; // when the browser-side phase started (Date.now())
  shot: WalletScreenshot | null;
  rows: Row[];
  error: string | null;
  saving: boolean;
  result: WalletApplyResult | null;
};

const fromReading = (shot: WalletScreenshot): Partial<Shot> =>
  shot.ocr_status === "done"
    ? {
        shot,
        // a row whose date is not on this screen is usually on the previous screenshot
        rows: (shot.ocr_result?.entries ?? []).map((e) => ({ ...e, include: e.date_shown && e.amount > 0 })),
        error: null,
      }
    : shot.ocr_status === "pending"
      ? { shot, rows: [], error: null }
      : { shot, rows: [], error: `อ่านรูปไม่สำเร็จ: ${OCR_ERROR[shot.ocr_error ?? ""] ?? shot.ocr_error ?? "ไม่ทราบสาเหตุ"}` };

// a photo the browser could not shrink is sent as it is only when small; a big one through the tunnel is slow
const MAX_UNSHRUNK = 3 * 1024 * 1024;
const fmtSize = (n: number) => (n < 1024 * 1024 ? `${Math.round(n / 1024)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);

const STEPS = ["กำลังย่อรูป", "กำลังส่งรูป", "รอ OCR", "OCR กำลังอ่าน", "บันทึกผล"];

/** Where a screenshot that is not read yet is: the step (index into STEPS) and a detail line,
 * e.g. step 3, "ครั้งที่ 2/4 · 25 วินาที". */
function progress(s: Shot, now: number): { step: number; detail: string } | null {
  const secs = (since: number) => `${Math.max(0, Math.round((now - since) / 1000))} วินาที`;
  if (s.phase === "shrinking") return { step: 0, detail: secs(s.phaseAt) };
  if (s.phase === "uploading")
    return { step: 1, detail: `${s.bytes === null ? "" : `${fmtSize(s.bytes)} · `}${secs(s.phaseAt)}` };
  const p = s.shot?.ocr_status === "pending" ? s.shot.ocr_progress : null;
  if (!p) return null;
  const elapsed = secs(Date.parse(p.since));
  const tries = (n?: number) => (p.attempts && p.attempts > 1 && n && n > 1 ? `ครั้งที่ ${n}/${p.attempts} · ` : "");
  switch (p.stage) {
    case "queued":
      return { step: 2, detail: `รอคิว อ่านพร้อมกันได้ 2 รูป · ${elapsed}` };
    case "reading":
      return { step: 3, detail: `${tries(p.attempt)}${elapsed}` };
    case "retry":
      return { step: 3, detail: `${RETRY_REASON[p.reason ?? ""] ?? "อ่านไม่สำเร็จ"} รอ ${p.wait} วินาทีแล้วลองใหม่ `
        + `(ครั้งที่ ${(p.attempt ?? 0) + 1}/${p.attempts}) · ${elapsed}` };
    default:
      return { step: 4, detail: elapsed };
  }
}

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
  const [now, setNow] = useState(Date.now());
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

  // readings run on the server: poll the ones still pending, and tick the elapsed time
  const pendingIds = shots.filter((s) => s.shot?.ocr_status === "pending").map((s) => s.shot!.id).join(",");
  const busy = pendingIds !== "" || shots.some((s) => s.phase !== null);
  useEffect(() => {
    if (!busy) return;
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(tick);
  }, [busy]);
  useEffect(() => {
    if (pendingIds === "") return;
    const failures = new Map<number, number>(); // consecutive failed polls per screenshot
    const poll = window.setInterval(() => {
      for (const id of pendingIds.split(",").map(Number)) {
        api.walletScreenshot(id).then(
          (shot) => {
            failures.delete(id);
            setShots((list) => list.map((s) => (s.shot?.id === id && !s.result ? { ...s, ...fromReading(shot) } : s)));
          },
          (e: Error) => {
            failures.set(id, (failures.get(id) ?? 0) + 1);
            // ~15 s of failures: say so instead of looking busy forever (the reading may still finish)
            if (failures.get(id) === 10)
              setShots((list) => list.map((s) => (s.shot?.id === id
                ? { ...s, error: `ติดต่อเซิร์ฟเวอร์ไม่ได้ (${e.message}) ลองกด อ่านใหม่ หรือเปิดหน้านี้ใหม่` } : s)));
          },
        );
      }
    }, 1500);
    return () => window.clearInterval(poll);
  }, [pendingIds]);

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
            phase: null, bytes: null, phaseAt: 0, shot: null, rows: [], error: null, saving: false, result: null, ...fromReading(p),
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

  const onFiles = async (files: FileList | null) => {
    if (!files || accountId === null) return;
    const added: Shot[] = [...files].map((f, i) => ({
      key: `${Date.now()}-${i}`, name: f.name, preview: URL.createObjectURL(f), phase: "shrinking", bytes: null, phaseAt: Date.now(), shot: null,
      rows: [], error: null, saving: false, result: null,
    }));
    setShots((list) => [...list, ...added]);
    if (input.current) input.current.value = "";
    // uploads are quick (the server reads in the background), so one at a time is fine
    for (const [i, f] of [...files].entries()) {
      try {
        const small = await shrinkImage(f);
        if (small === f && f.size > MAX_UNSHRUNK) {
          patch(added[i].key, { phase: null,
            error: `ย่อรูปไม่ได้ (รูป ${fmtSize(f.size)} เบราว์เซอร์อาจเปิดไฟล์แบบนี้ไม่ได้ เช่น HEIC) ลองแคปหน้าจอเป็น PNG/JPG แทน` });
          continue;
        }
        patch(added[i].key, { phase: "uploading", bytes: small.size, phaseAt: Date.now() });
        patch(added[i].key, { phase: null, ...fromReading(await api.uploadWalletScreenshot(accountId, small)) });
      } catch (e) {
        const err = e as Error;
        const msg = err.name === "TimeoutError" || err.name === "AbortError"
          ? "อัปโหลดไม่เสร็จใน 60 วินาที ลองเลือกรูปใหม่" : err.message;
        patch(added[i].key, { phase: null, error: `อัปโหลดไม่สำเร็จ: ${msg}` });
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
        {shots.length > 0 && (
          <p className="muted small">
            อ่านเสร็จ {shots.filter((s) => s.shot?.ocr_status === "done").length}/{shots.length} รูป
            {busy && " · อ่านต่อเบื้องหลังแม้ปิดหน้านี้ เปิดกลับมาจะเห็นผล"}
          </p>
        )}
        {shots.map((s) => (
          <div key={s.key} className="wallet-shot">
            <img src={s.preview} alt={s.name} />
            <div className="wallet-shot-body">
              {(() => {
                const p = progress(s, now);
                return p && (
                  <div className="steps" role="status">
                    <ol>
                      {STEPS.map((label, i) => (
                        <li key={label} className={i < p.step ? "done" : i === p.step ? "current" : undefined}>
                          {i < p.step ? "✓ " : i === p.step ? <span className="spinner" aria-hidden="true" /> : null} {label}
                        </li>
                      ))}
                    </ol>
                    <div className="muted small">{p.detail}</div>
                  </div>
                );
              })()}
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

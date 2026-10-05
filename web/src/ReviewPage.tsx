import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "./api";
import { CategorySelect } from "./CategorySelect";
import { CHANNEL_LABEL, fmtMoney, fmtWhen } from "./format";
import type { Category, Status, Txn } from "./types";

const PAGE = 200;

function SourceBadge({ t, uncategorizedId }: { t: Txn; uncategorizedId?: number }) {
  if (t.category_id === null) return <span className="badge badge-none">ยังไม่มีหมวด</span>;
  if (t.category_id === uncategorizedId) return <span className="badge badge-warn">ไม่แน่ใจ</span>;
  switch (t.category_source) {
    case "ai":
      return (
        <span className="badge badge-ai" title={t.ai_reason ?? ""}>
          AI {t.ai_confidence !== null ? `${Math.round(t.ai_confidence * 100)}%` : ""}
        </span>
      );
    case "memory":
      return <span className="badge badge-memory">จำไว้</span>;
    case "user":
      return <span className="badge badge-user">เลือกเอง</span>;
    case "manual":
      return <span className="badge badge-user">บันทึกเอง</span>;
    default:
      return null;
  }
}

export function ReviewPage({ categories }: { categories: Category[] }) {
  const [status, setStatus] = useState<Status>("pending");
  const [month, setMonth] = useState("");
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<Txn[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const uncategorizedId = useMemo(
    () => categories.find((c) => c.parent_id === null && c.name_en === "Uncategorized")?.id,
    [categories],
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.transactions({ status, month: month || undefined, limit: PAGE, offset });
      setItems(r.items);
      setTotal(r.total);
      setSelected(new Set());
    } catch (e) {
      setMessage({ kind: "error", text: `โหลดรายการไม่สำเร็จ: ${(e as Error).message}` });
    } finally {
      setLoading(false);
    }
  }, [status, month, offset]);

  useEffect(() => {
    load();
  }, [load]);

  const replace = (updated: Txn) => {
    const swap = (list: Txn[]): Txn[] =>
      list.map((t) => (t.id === updated.id ? { ...updated, children: t.children } : { ...t, children: swap(t.children) }));
    setItems((prev) => swap(prev));
  };

  const setCategory = async (t: Txn, categoryId: number) => {
    try {
      replace(await api.patch(t.id, { category_id: categoryId }));
    } catch (e) {
      setMessage({ kind: "error", text: `เปลี่ยนหมวดไม่สำเร็จ: ${(e as Error).message}` });
    }
  };

  const ignore = async (t: Txn) => {
    try {
      await api.patch(t.id, { status: t.status === "ignored" ? "pending" : "ignored" });
      await load();
    } catch (e) {
      setMessage({ kind: "error", text: `ทำรายการไม่สำเร็จ: ${(e as Error).message}` });
    }
  };

  const confirm = async (all: boolean) => {
    if (all && !window.confirm(`ยืนยันทุกรายการที่รอตรวจ (${total} รายการ) ใช่ไหม?`)) return;
    try {
      const r = await api.confirm(all ? { all: true } : { ids: [...selected] });
      setMessage({
        kind: "ok",
        text:
          `ยืนยันแล้ว ${r.confirmed} รายการ` +
          (r.remembered ? ` · จำหมวดของ ${r.remembered} ร้าน` : "") +
          (r.applied_to_pending ? ` · อัปเดตหมวดให้รายการอื่นของร้านเดียวกัน ${r.applied_to_pending} รายการ` : ""),
      });
      await load();
    } catch (e) {
      setMessage({ kind: "error", text: `ยืนยันไม่สำเร็จ: ${(e as Error).message}` });
    }
  };

  const refresh = async () => {
    setSyncing(true);
    setMessage(null);
    try {
      const r = await api.sync();
      const added = Object.values(r.counts).reduce((s, c) => s + c.new, 0);
      const errors = Object.entries(r.counts).filter(([, c]) => c.error).map(([k, c]) => `${k}: ${c.error}`);
      setMessage({
        kind: errors.length ? "error" : "ok",
        text: `ดึงอีเมลแล้ว: รายการใหม่ ${added} รายการ` + (errors.length ? ` · มีปัญหา ${errors.join(", ")}` : ""),
      });
      await load();
    } catch (e) {
      const text = e instanceof ApiError && e.status === 409 ? "กำลังดึงอีเมลอยู่แล้ว รอสักครู่" : (e as Error).message;
      setMessage({ kind: "error", text });
    } finally {
      setSyncing(false);
    }
  };

  const toggle = (set: Set<number>, id: number) => {
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  };
  const allChecked = items.length > 0 && items.every((t) => selected.has(t.id));
  const selectedTotal = items.filter((t) => selected.has(t.id)).reduce((s, t) => s + t.amount, 0);

  const renderRow = (t: Txn, depth: number) => {
    const isLeaf = t.children.length === 0;
    const editable = t.status !== "confirmed" || isLeaf; // confirmed leaves can still be re-categorized
    return (
      <Fragment key={t.id}>
        <tr className={depth ? "child-row" : selected.has(t.id) ? "selected" : undefined}>
          <td className="c-check">
            {depth === 0 && status === "pending" && (
              <input
                type="checkbox"
                aria-label="เลือก"
                checked={selected.has(t.id)}
                onChange={() => setSelected((s) => toggle(s, t.id))}
              />
            )}
          </td>
          <td className="c-date nowrap muted">{fmtWhen(t.occurred_at, t.has_time)}</td>
          <td className="c-merchant" style={{ paddingLeft: 8 + depth * 20 }}>
            {!isLeaf && (
              <button className="link" onClick={() => setExpanded((s) => toggle(s, t.id))}>
                {expanded.has(t.id) ? "▾" : "▸"}
              </button>
            )}{" "}
            <span className="merchant">{t.merchant_raw ?? "—"}</span>
            {t.needs_review && <span className="badge badge-warn">ยอดลูกไม่ตรง</span>}
            <div className="muted small">{CHANNEL_LABEL[t.channel ?? ""] ?? t.channel ?? t.node_type}</div>
          </td>
          <td className="c-amount num">{fmtMoney(t.amount)}</td>
          <td className="c-cat">
            {isLeaf && t.kind === "expense" ? (
              <CategorySelect
                categories={categories}
                value={t.category_id}
                disabled={!editable}
                onChange={(id) => setCategory(t, id)}
              />
            ) : (
              <span className="muted small">{t.kind === "transfer" ? "โอนระหว่างบัญชี" : "ดูรายการย่อย"}</span>
            )}
          </td>
          <td className="c-src">{isLeaf && t.kind === "expense" && <SourceBadge t={t} uncategorizedId={uncategorizedId} />}</td>
          <td className="c-act nowrap">
            {depth === 0 && t.status !== "confirmed" && (
              <button className="link" onClick={() => ignore(t)}>
                {t.status === "ignored" ? "กู้คืน" : "ไม่นับ"}
              </button>
            )}
          </td>
        </tr>
        {expanded.has(t.id) && t.children.map((c) => renderRow(c, depth + 1))}
      </Fragment>
    );
  };

  return (
    <section>
      <div className="toolbar">
        <div className="tabs" role="tablist">
          {(["pending", "confirmed", "ignored"] as Status[]).map((s) => (
            <button
              key={s}
              role="tab"
              aria-selected={status === s}
              className={status === s ? "tab active" : "tab"}
              onClick={() => {
                setStatus(s);
                setOffset(0);
              }}
            >
              {{ pending: "รอตรวจ", confirmed: "ยืนยันแล้ว", ignored: "ไม่นับ" }[s]}
            </button>
          ))}
        </div>
        <label className="muted small">
          เดือน{" "}
          <input
            type="month"
            value={month}
            onChange={(e) => {
              setMonth(e.target.value);
              setOffset(0);
            }}
          />
        </label>
        <div className="spacer" />
        <button onClick={refresh} disabled={syncing}>
          {syncing ? "กำลังดึงอีเมล…" : "Refresh"}
        </button>
        {status === "pending" && (
          <>
            <button className="primary" disabled={selected.size === 0} onClick={() => confirm(false)}>
              ยืนยันที่เลือก ({selected.size})
            </button>
            <button disabled={total === 0} onClick={() => confirm(true)}>
              ยืนยันทั้งหมด
            </button>
          </>
        )}
      </div>

      {syncing && <p className="muted small">ครั้งแรกอาจใช้หลายนาที เพราะต้องดึงอีเมลย้อนหลัง 3 เดือน</p>}
      {message && (
        <div className={`message ${message.kind}`} role="status">
          {message.text}
          <button className="link" onClick={() => setMessage(null)} aria-label="ปิด">
            ✕
          </button>
        </div>
      )}

      <div className="muted small summary-line">
        {loading ? "กำลังโหลด…" : `${total} รายการ`}
        {selected.size > 0 && ` · เลือก ${selected.size} รายการ รวม ${fmtMoney(selectedTotal)}`}
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>
                {status === "pending" && (
                  <input
                    type="checkbox"
                    aria-label="เลือกทั้งหน้า"
                    checked={allChecked}
                    onChange={() => setSelected(allChecked ? new Set() : new Set(items.map((t) => t.id)))}
                  />
                )}
              </th>
              <th>วันที่</th>
              <th>ร้าน / ผู้รับ</th>
              <th className="num">จำนวนเงิน</th>
              <th>หมวด</th>
              <th>ที่มา</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {items.map((t) => renderRow(t, 0))}
            {!loading && items.length === 0 && (
              <tr>
                <td colSpan={7} className="empty">
                  {status === "pending" ? "ไม่มีรายการรอตรวจ กด Refresh เพื่อดึงอีเมลใหม่" : "ไม่มีรายการ"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {total > PAGE && (
        <div className="pager">
          <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
            ก่อนหน้า
          </button>
          <span className="muted small">
            {offset + 1}–{Math.min(offset + PAGE, total)} จาก {total}
          </span>
          <button disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
            ถัดไป
          </button>
        </div>
      )}
    </section>
  );
}

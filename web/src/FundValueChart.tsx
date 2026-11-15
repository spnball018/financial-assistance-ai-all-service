import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { fmtMoney } from "./format";
import type { FundValueHistory, FundValuePoint } from "./types";

type Range = "3m" | "1y" | "all";
const RANGES: { key: Range; label: string; months: number | null }[] = [
  { key: "3m", label: "3 เดือน", months: 3 },
  { key: "1y", label: "1 ปี", months: 12 },
  { key: "all", label: "ทั้งหมด", months: null },
];
const H = 240;
const PAD = { top: 12, right: 12, bottom: 28, left: 64 };
const DAY_FMT = new Intl.DateTimeFormat("th-TH", { day: "numeric", month: "short", year: "2-digit" });
const AXIS_FMT = new Intl.DateTimeFormat("th-TH", { month: "short", year: "2-digit" });
const compact = new Intl.NumberFormat("th-TH", { notation: "compact", maximumFractionDigits: 1 });

function sinceFor(range: Range): string | undefined {
  const months = RANGES.find((r) => r.key === range)!.months;
  if (months === null) return undefined;
  const d = new Date();
  d.setMonth(d.getMonth() - months);
  return d.toISOString().slice(0, 10);
}

/** Several accounts' daily values on one line: each account keeps its last value on days it has no point. */
function combine(histories: FundValueHistory[]): FundValuePoint[] {
  const dates = [...new Set(histories.flatMap((h) => h.points.map((p) => p.date)))].sort();
  const at = histories.map(() => 0);
  const last: (FundValuePoint | null)[] = histories.map(() => null);
  return dates.map((date) => {
    let value = 0;
    let cost = 0;
    histories.forEach((h, i) => {
      while (at[i] < h.points.length && h.points[at[i]].date <= date) last[i] = h.points[at[i]++];
      value += last[i]?.value ?? 0;
      cost += last[i]?.cost ?? 0;
    });
    return { date, value, cost, by_fund: {} };
  });
}

/** Value of the shown fund accounts over time against what was paid for them, from the daily NAV history. */
export function FundValueChart({ accountIds }: { accountIds: number[] }) {
  const [range, setRange] = useState<Range>("1y");
  const [points, setPoints] = useState<FundValuePoint[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [width, setWidth] = useState(640);
  const [hover, setHover] = useState<number | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const key = accountIds.join(",");

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(280, Math.floor(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (accountIds.length === 0) return;
    let live = true;
    setPoints(null);
    setError(null);
    const interval = range === "all" ? "week" : "day";
    Promise.all(accountIds.map((id) => api.fundValueHistory(id, sinceFor(range), interval))).then(
      (hs) => live && setPoints(combine(hs)),
      (e: Error) => live && setError(e.message),
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, range]);

  const geo = useMemo(() => {
    if (!points || points.length < 2) return null;
    const t = points.map((p) => Date.parse(p.date));
    const [t0, t1] = [t[0], t[t.length - 1]];
    const vals = points.flatMap((p) => [p.value, p.cost]);
    const lo = Math.min(...vals) * 0.98;
    const hi = Math.max(...vals) * 1.02;
    const iw = width - PAD.left - PAD.right;
    const ih = H - PAD.top - PAD.bottom;
    const x = (ms: number) => PAD.left + ((ms - t0) / (t1 - t0 || 1)) * iw;
    const y = (v: number) => PAD.top + (1 - (v - lo) / (hi - lo || 1)) * ih;
    const path = (k: "value" | "cost") => points.map((p, i) => `${i ? "L" : "M"}${x(t[i]).toFixed(1)},${y(p[k]).toFixed(1)}`).join("");
    const yTicks = Array.from({ length: 4 }, (_, i) => lo + ((hi - lo) * i) / 3);
    const xTicks = Array.from({ length: Math.min(5, points.length) }, (_, i) => t0 + ((t1 - t0) * i) / Math.max(1, Math.min(5, points.length) - 1));
    return { t, x, y, value: path("value"), cost: path("cost"), yTicks, xTicks };
  }, [points, width]);

  if (accountIds.length === 0) return null;
  const last = points && points.length > 0 ? points[points.length - 1] : null;
  const first = points && points.length > 0 ? points[0] : null;
  const h = hover !== null && points ? points[hover] : null;

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    if (!geo || !points) return;
    const px = e.clientX - e.currentTarget.getBoundingClientRect().left + PAD.left;
    let best = 0;
    for (let i = 1; i < points.length; i++) if (Math.abs(geo.x(geo.t[i]) - px) < Math.abs(geo.x(geo.t[best]) - px)) best = i;
    setHover(best);
  };

  return (
    <div className="value-chart" ref={box}>
      <div className="toolbar">
        <div className="tabs" role="tablist" aria-label="ช่วงเวลา">
          {RANGES.map((r) => (
            <button key={r.key} role="tab" aria-selected={range === r.key} className={range === r.key ? "tab active" : "tab"}
              onClick={() => setRange(r.key)}>
              {r.label}
            </button>
          ))}
        </div>
        <div className="spacer" />
        <div className="legend small">
          <span><i className="swatch value" /> มูลค่า</span>
          <span><i className="swatch cost" /> ต้นทุน</span>
        </div>
      </div>
      {error && <div className="message error">{error}</div>}
      {points === null && !error && <p className="muted small">กำลังโหลด…</p>}
      {points !== null && points.length < 2 && (
        <p className="muted small">ยังไม่มีข้อมูล NAV ย้อนหลังพอจะวาดกราฟ ระบบดึง NAV ทุกวัน หรือกด "ดึง NAV ย้อนหลัง" ด้านบน</p>
      )}
      {geo && points && (
        <>
          <p className="small">
            ล่าสุด <b>{fmtMoney(last!.value)}</b> · ต้นทุน {fmtMoney(last!.cost)} ·{" "}
            <span className={last!.value - last!.cost < 0 ? "neg" : ""}>
              {last!.value - last!.cost >= 0 ? "กำไร" : "ขาดทุน"} {fmtMoney(Math.abs(last!.value - last!.cost))}
            </span>
            <span className="muted"> · ตั้งแต่ {DAY_FMT.format(new Date(first!.date))}</span>
          </p>
          <svg width={width} height={H} role="img"
            aria-label={`มูลค่ากองทุน ${points.length} จุด ล่าสุด ${fmtMoney(last!.value)} ต้นทุน ${fmtMoney(last!.cost)}`}>
            {geo.yTicks.map((v) => (
              <g key={v}>
                <line className="grid" x1={PAD.left} x2={width - PAD.right} y1={geo.y(v)} y2={geo.y(v)} />
                <text className="axis" x={PAD.left - 6} y={geo.y(v)} dy="0.32em" textAnchor="end">{compact.format(v)}</text>
              </g>
            ))}
            {geo.xTicks.map((ms, i) => (
              <text key={ms} className="axis" x={geo.x(ms)} y={H - 8}
                textAnchor={i === 0 ? "start" : i === geo.xTicks.length - 1 ? "end" : "middle"}>
                {AXIS_FMT.format(new Date(ms))}
              </text>
            ))}
            <path className="line cost" d={geo.cost} />
            <path className="line value" d={geo.value} />
            {h && hover !== null && (
              <g>
                <line className="crosshair" x1={geo.x(geo.t[hover])} x2={geo.x(geo.t[hover])} y1={PAD.top} y2={H - PAD.bottom} />
                <circle className="dot cost" cx={geo.x(geo.t[hover])} cy={geo.y(h.cost)} r={4} />
                <circle className="dot value" cx={geo.x(geo.t[hover])} cy={geo.y(h.value)} r={4} />
              </g>
            )}
            <rect x={PAD.left} y={PAD.top} width={width - PAD.left - PAD.right} height={H - PAD.top - PAD.bottom}
              fill="transparent" onPointerMove={onMove} onPointerLeave={() => setHover(null)} />
          </svg>
          {h && hover !== null && (
            <div className="chart-tip small" style={{ left: Math.min(Math.max(geo.x(geo.t[hover]) - 80, 0), width - 170) }}>
              <div className="muted">{DAY_FMT.format(new Date(h.date))}</div>
              <div><i className="swatch value" /> มูลค่า {fmtMoney(h.value)}</div>
              <div><i className="swatch cost" /> ต้นทุน {fmtMoney(h.cost)}</div>
              <div className={h.value - h.cost < 0 ? "neg" : ""}>
                {h.value - h.cost >= 0 ? "กำไร" : "ขาดทุน"} {fmtMoney(Math.abs(h.value - h.cost))}
              </div>
            </div>
          )}
          <details className="small">
            <summary>ดูเป็นตาราง</summary>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>วันที่</th><th className="num">มูลค่า</th><th className="num">ต้นทุน</th></tr>
                </thead>
                <tbody>
                  {[...points].reverse().slice(0, 60).map((p) => (
                    <tr key={p.date}>
                      <td>{DAY_FMT.format(new Date(p.date))}</td>
                      <td className="num">{fmtMoney(p.value)}</td>
                      <td className="num">{fmtMoney(p.cost)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </div>
  );
}

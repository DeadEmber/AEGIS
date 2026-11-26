"use client";

import { useMemo, useState } from "react";

export interface Series { name: string; color: string; points: { t: number; v: number }[]; dashed?: boolean }

/** Minimal dependency-free SVG line chart with hover read-out. */
export function LineChart({ series, height = 220, format = (v: number) => v.toFixed(2), label }: {
  series: Series[]; height?: number; format?: (v: number) => string; label: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 1000;
  const H = height;
  const pad = { l: 8, r: 8, t: 10, b: 18 };
  const all = series.flatMap((s) => s.points);
  const dom = useMemo(() => {
    if (!all.length) return null;
    const t0 = Math.min(...all.map((p) => p.t));
    const t1 = Math.max(...all.map((p) => p.t));
    let v0 = Math.min(...all.map((p) => p.v));
    let v1 = Math.max(...all.map((p) => p.v));
    if (v0 === v1) { v0 -= 1; v1 += 1; }
    const m = (v1 - v0) * 0.08;
    return { t0, t1: t1 === t0 ? t0 + 1 : t1, v0: v0 - m, v1: v1 + m };
  }, [all]);
  if (!dom) return <div className="message">Sin puntos todavía.</div>;
  const x = (t: number) => pad.l + ((t - dom.t0) / (dom.t1 - dom.t0)) * (W - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - (v - dom.v0) / (dom.v1 - dom.v0)) * (H - pad.t - pad.b);
  const base = series[0]?.points ?? [];
  const hp = hover !== null ? base[hover] : null;

  return (
    <figure style={{ margin: 0 }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label={label}
           onMouseLeave={() => setHover(null)}
           onMouseMove={(e) => {
             const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
             const t = dom.t0 + ((e.clientX - r.left) / r.width) * (dom.t1 - dom.t0);
             let best = 0;
             base.forEach((p, i) => { if (Math.abs(p.t - t) < Math.abs(base[best].t - t)) best = i; });
             setHover(base.length ? best : null);
           }}>
        {[0.25, 0.5, 0.75].map((f) => (
          <line key={f} x1={pad.l} x2={W - pad.r} y1={pad.t + f * (H - pad.t - pad.b)} y2={pad.t + f * (H - pad.t - pad.b)}
                stroke="var(--rule)" strokeWidth={1} />
        ))}
        {[dom.v1 - (dom.v1 - dom.v0) * 0.08, dom.v0 + (dom.v1 - dom.v0) * 0.08].map((v) => (
          <text key={v} x={W - pad.r} y={y(v) - 4} textAnchor="end" fontSize={15} fill="var(--muted)"
                style={{ fontFeatureSettings: '"tnum"' }}>{format(v)}</text>
        ))}
        {series.map((s) => (
          <polyline key={s.name} fill="none" stroke={s.color} strokeWidth={s.dashed ? 1.25 : 2}
                    strokeDasharray={s.dashed ? "5 4" : undefined} vectorEffect="non-scaling-stroke"
                    points={s.points.map((p) => `${x(p.t)},${y(p.v)}`).join(" ")} />
        ))}
        {hp ? <line x1={x(hp.t)} x2={x(hp.t)} y1={pad.t} y2={H - pad.b} stroke="var(--rule-strong)" /> : null}
      </svg>
      <figcaption className="legend">
        {series.map((s) => (
          <span key={s.name}><i style={{ background: s.color }} />{s.name}
            {hp ? <> {format(s.points[hover ?? 0]?.v ?? NaN)}</> : null}</span>
        ))}
        {hp ? <span>{new Date(hp.t).toLocaleString("es-ES", { dateStyle: "short", timeStyle: "short" })}</span> : null}
      </figcaption>
    </figure>
  );
}

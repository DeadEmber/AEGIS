"use client";

import Link from "next/link";

import { mult, toNum } from "@/lib/format";

/**
 * The portfolio as a field of tickets: one cell per position, coloured by NET liquidable multiple on a
 * log scale (blue = above cost, orange = below, grey = unvalued). Shows mass diversification and the
 * asymmetric tail at a glance.
 */
export function cellColor(m: number | null): string {
  if (m === null) return "var(--rule-strong)";
  const l = Math.log2(Math.max(m, 1 / 64));
  if (l >= 0) {
    const a = Math.min(1, 0.25 + l / 5); // x1 -> faint, x32 -> full
    return `color-mix(in srgb, var(--gain) ${Math.round(a * 100)}%, var(--panel))`;
  }
  const a = Math.min(1, 0.25 + -l / 4);
  return `color-mix(in srgb, var(--loss) ${Math.round(a * 100)}%, var(--panel))`;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function TicketField({ positions }: { positions: any[] }) {
  const cells = positions.map((p) => {
    const liq = toNum(p.last_liquidation_eur);
    const inv = toNum(p.invested_eur_total);
    const rec = toNum(p.realized_proceeds_eur) ?? 0;
    const m = liq !== null && inv ? (liq + rec) / inv : null;
    return { id: p.id, token: p.token_id, symbol: p.symbol || "?", m, status: p.status };
  }).sort((a, b) => (b.m ?? -1) - (a.m ?? -1));
  return (
    <>
      <div className="field" aria-label="Posiciones abiertas por multiplicador neto">
        {cells.map((c) => (
          <Link key={c.id} href={`/token?id=${c.token}`} className="cell" style={{ background: cellColor(c.m) }}
                title={`${c.symbol}: ${c.m === null ? "sin valorar" : mult(c.m)} neto (${c.status})`}
                aria-label={`${c.symbol} ${c.m === null ? "sin valorar" : mult(c.m)}`} />
        ))}
      </div>
      <div className="legend">
        <span><i style={{ background: cellColor(0.1) }} />x0,1</span>
        <span><i style={{ background: cellColor(0.5) }} />x0,5</span>
        <span><i style={{ background: cellColor(1) }} />x1</span>
        <span><i style={{ background: cellColor(4) }} />x4</span>
        <span><i style={{ background: cellColor(32) }} />x32 o más</span>
        <span><i style={{ background: cellColor(null) }} />sin valorar</span>
      </div>
    </>
  );
}

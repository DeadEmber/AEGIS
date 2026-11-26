"use client";

import Link from "next/link";
import { useState } from "react";

import { Guard, PageHead, Panel, Pill, Table, stateTone } from "@/components/ui";
import { useApi } from "@/lib/api";
import { eur, mult, price, signClass, signedEur, toNum, when } from "@/lib/format";

export default function Portfolio() {
  const [status, setStatus] = useState("open");
  const pos = useApi(`/portfolio/positions?status=${status}&limit=5000`);
  return (
    <>
      <PageHead title="Posiciones" sub="Cada posición con su coste (incluidas comisiones de compra), su valor liquidable estimado y su relación con el valor de mercado (RPR)." />
      <div className="toolbar">
        <label>Mostrar{" "}
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="open">abiertas</option>
            <option value="closed">cerradas</option>
          </select>
        </label>
      </div>
      <Panel>
        <Guard state={pos} empty={(d) => !d.positions?.length}>
          {(d) => (
            <Table head={["Token", "Estado", "Bucket", "Entrada", "Coste", "Invertido", "Cobrado", "Liquidable", "Neto", "RPR", "P&L realizado", "Pico", "Mercado"]}
                   numeric={[4, 5, 6, 7, 8, 9, 10, 11]}>
              {d.positions.map((p: Record<string, any>) => { // eslint-disable-line @typescript-eslint/no-explicit-any
                const liq = toNum(p.last_liquidation_eur) ?? 0;
                const net = toNum(p.invested_eur_total) ? (liq + (toNum(p.realized_proceeds_eur) ?? 0)) / Number(p.invested_eur_total) : null;
                return (
                  <tr key={p.id}>
                    <td><Link href={`/token?id=${p.token_id}`}>{p.symbol || p.address?.slice(0, 6)}</Link></td>
                    <td><Pill tone={p.status === "CLOSED" ? "" : p.status === "OPEN" ? "pass" : "hot"}>{p.status}</Pill></td>
                    <td className="muted">{p.bucket}</td>
                    <td>{when(p.opened_at)}</td>
                    <td className="num">{eur(p.cost_basis_eur, 4)}</td>
                    <td className="num">{eur(p.invested_eur_total, 4)}</td>
                    <td className="num">{eur(p.realized_proceeds_eur, 4)}</td>
                    <td className="num">{p.status === "CLOSED" ? "—" : eur(p.last_liquidation_eur, 4)}</td>
                    <td className={`num ${net !== null && net >= 1 ? "gain" : "loss"}`}>{mult(net)}</td>
                    <td className="num">{p.rpr ? Number(p.rpr).toFixed(3) : "—"}</td>
                    <td className={`num ${signClass(p.realized_pnl_eur)}`}>{signedEur(p.realized_pnl_eur, 4)}</td>
                    <td className="num">{price(p.peak_price_eur)}</td>
                    <td><Pill tone={stateTone(p.market_state)}>{p.market_state}</Pill></td>
                  </tr>
                );
              })}
            </Table>
          )}
        </Guard>
      </Panel>
    </>
  );
}

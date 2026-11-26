"use client";

import Link from "next/link";

import { Guard, PageHead, Panel, Pill, Table, stateTone } from "@/components/ui";
import { useApi } from "@/lib/api";
import { compact, eur, mult, pct, price, score, toNum, when } from "@/lib/format";

export default function PeakRadar() {
  const r = useApi("/peak-radar");
  return (
    <>
      <PageHead title="Peak radar" sub="Explosiones activas y riesgo de agotamiento. PRS es un score heurístico, no una probabilidad: sirve para ordenar la atención y como baseline de los modelos." />
      <Guard state={r}>
        {(d) => (
          <div className="grid">
            <Panel title="Posiciones abiertas" primary>
              {d.positions.length ? (
                <Table head={["Token", "Estado", "Posición", "Neto", "PRS", "DQI", "Liquidable", "Pico", "Recuperado"]} numeric={[3, 4, 5, 6, 7]}>
                  {d.positions.map((p: Record<string, any>) => { // eslint-disable-line @typescript-eslint/no-explicit-any
                    const net = toNum(p.invested_eur_total)
                      ? ((toNum(p.last_liquidation_eur) ?? 0) + (toNum(p.realized_proceeds_eur) ?? 0)) / Number(p.invested_eur_total) : null;
                    return (
                      <tr key={p.id}>
                        <td><Link href={`/token?id=${p.token_id}`}>{p.symbol}</Link></td>
                        <td><Pill tone={stateTone(p.market_state)}>{p.market_state}</Pill></td>
                        <td className="muted">{p.status}</td>
                        <td className="num">{mult(net)}</td>
                        <td className={`num ${(toNum(p.scores?.PRS) ?? 0) >= 0.55 ? "alert" : ""}`}>{score(p.scores?.PRS)}</td>
                        <td className="num">{score(p.scores?.DQI)}</td>
                        <td className="num">{eur(p.last_liquidation_eur, 4)}</td>
                        <td className="num">{price(p.peak_price_eur)}</td>
                        <td>{p.capital_recovered ? <Pill tone="ok">capital recuperado</Pill> : "—"}</td>
                      </tr>
                    );
                  })}
                </Table>
              ) : <div className="message">Ninguna posición abierta.</div>}
            </Panel>
            <Panel title="Mercado caliente (sin posición necesariamente)">
              <Table head={["Token", "Estado", "Desde", "Precio", "Desde ATH", "1 h", "Liquidez", "Olas", "PRS", "EPI_h"]} numeric={[3, 4, 5, 6, 7, 8, 9]}>
                {d.market.map((m: Record<string, any>) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
                  <tr key={m.token_id}>
                    <td><Link href={`/token?id=${m.token_id}`}>{m.symbol}</Link></td>
                    <td><Pill tone={stateTone(m.state)}>{m.state}</Pill></td>
                    <td>{when(m.since)}</td>
                    <td className="num">{price(m.price_usd)}</td>
                    <td className="num">{pct(m.drawdown_from_ath === null ? null : -Number(m.drawdown_from_ath), 0)}</td>
                    <td className="num">{pct(m.price_change_h1, 0, false)}</td>
                    <td className="num">{compact(m.liquidity_usd)}</td>
                    <td className="num">{m.explosion_count}</td>
                    <td className="num">{score(m.scores?.PRS)}</td>
                    <td className="num">{score(m.scores?.EPI_h)}</td>
                  </tr>
                ))}
              </Table>
            </Panel>
            <Panel title="Decisiones de salida recientes">
              <Table head={["Hora", "Token", "Acción", "Fracción", "Motivo", "Resultado"]} numeric={[3]}>
                {d.exit_decisions.map((x: Record<string, any>) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
                  <tr key={x.id}>
                    <td>{when(x.decided_at)}</td>
                    <td>{x.symbol}</td>
                    <td><Pill tone="hot">{x.action}</Pill></td>
                    <td className="num">{x.fraction ? `${(Number(x.fraction) * 100).toFixed(1)} %` : "—"}</td>
                    <td><span className="reason">{(x.reasons || []).join("; ")}</span></td>
                    <td className="muted">{x.outcome}</td>
                  </tr>
                ))}
              </Table>
            </Panel>
          </div>
        )}
      </Guard>
    </>
  );
}

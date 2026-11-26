"use client";

import Link from "next/link";

import { Guard, PageHead, Panel, Pill, Table, stateTone } from "@/components/ui";
import { useApi } from "@/lib/api";
import { compact, mult, pct, price, score, when } from "@/lib/format";

export default function SecondWave() {
  const r = useApi("/second-wave", 60_000);
  return (
    <>
      <PageHead title="Segundas olas" sub="Antiguos ganadores y tokens con explosiones previas. No hace falta volver al máximo: un rebote desde el mínimo posterior al ATH ya puede ser rentable si la estructura del mercado sobrevive." />
      <Guard state={r} empty={(d) => !d.tokens?.length}>
        {(d) => (
          <div className="grid">
            <Panel primary>
              <Table head={["Token", "Estado", "Entradas", "Olas", "Última salida", "Desde ATH", "Rebote desde mínimo", "Liquidez", "Liquidez en la salida", "SWP_h", "SSI"]}
                     numeric={[2, 3, 5, 6, 7, 8, 9, 10]}>
                {d.tokens.map((t: Record<string, any>) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
                  <tr key={t.token_id}>
                    <td><Link href={`/token?id=${t.token_id}`}>{t.symbol}</Link></td>
                    <td><Pill tone={stateTone(t.state)}>{t.state}</Pill></td>
                    <td className="num">{t.entries}</td>
                    <td className="num">{t.explosion_count}</td>
                    <td>{when(t.last_exit_at)}</td>
                    <td className="num">{pct(t.drawdown_from_ath === null ? null : -Number(t.drawdown_from_ath), 0)}</td>
                    <td className="num">{mult(t.rebound_from_low)}</td>
                    <td className="num">{compact(t.liquidity_usd)}</td>
                    <td className="num">{compact(t.liquidity_at_exit_usd)}</td>
                    <td className="num">{score(t.scores?.SWP_h)}</td>
                    <td className="num">{score(t.scores?.SSI)}</td>
                  </tr>
                ))}
              </Table>
            </Panel>
            <Panel title="Episodios de explosión detectados">
              <Table head={["Inicio", "Token", "Ola", "Precio base USD", "Pico USD", "Liquidez en el pico"]} numeric={[2, 3, 4, 5]}>
                {d.explosion_events.map((e: Record<string, any>) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
                  <tr key={e.id}>
                    <td>{when(e.started_at)}</td>
                    <td><Link href={`/token?id=${e.token_id}`}>{e.token_id}</Link></td>
                    <td className="num">{e.wave_number}</td>
                    <td className="num">{price(e.base_price_usd)}</td>
                    <td className="num">{price(e.peak_price_usd)}</td>
                    <td className="num">{compact(e.peak_liquidity_usd)}</td>
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

"use client";

import Link from "next/link";
import { useState } from "react";

import { Guard, PageHead, Panel, Pill, Table, stateTone, verdictTone } from "@/components/ui";
import { useApi } from "@/lib/api";
import { ago, compact, pct, price, score, short } from "@/lib/format";

export default function Discovery() {
  const [state, setState] = useState("");
  const [elig, setElig] = useState("");
  const q = `/discovery/tokens?limit=1000${state ? `&state=${state}` : ""}${elig ? `&eligible=${elig}` : ""}`;
  const t = useApi(q, 60_000);
  return (
    <>
      <PageHead title="Descubrimiento" sub="Universo monitorizado con su estado, filtros experimentales y veredicto de seguridad. Los motivos de rechazo son los reales de cada filtro." />
      <Guard state={t}>
        {(d) => (
          <>
            <div className="toolbar">
              <label>Estado{" "}
                <select value={state} onChange={(e) => setState(e.target.value)}>
                  <option value="">todos</option>
                  {Object.entries(d.state_counts || {}).map(([k, v]) => <option key={k} value={k}>{k} ({String(v)})</option>)}
                </select>
              </label>
              <label>Elegibilidad{" "}
                <select value={elig} onChange={(e) => setElig(e.target.value)}>
                  <option value="">todas</option>
                  <option value="true">elegibles</option>
                  <option value="false">no elegibles</option>
                </select>
              </label>
              <span className="muted">{d.tokens.length} tokens</span>
            </div>
            <Panel>
              <Table head={["Token", "Mint", "Estado", "Seguridad", "Precio USD", "Liquidez", "FDV", "Cap.", "Vol. 24 h", "1 h", "24 h", "EPI_h", "DQI", "MRI", "Filtros fallidos", "Visto"]}
                     numeric={[4, 5, 6, 7, 8, 9, 10, 11, 12, 13]}>
                {d.tokens.map((r: Record<string, any>) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
                  <tr key={r.id}>
                    <td><Link href={`/token?id=${r.id}`}>{r.symbol || "?"}</Link></td>
                    <td className="muted" title={r.address}>{short(r.address)}</td>
                    <td><Pill tone={stateTone(r.market_state)}>{r.market_state}</Pill></td>
                    <td><Pill tone={verdictTone(r.security_verdict)}>{r.security_verdict}</Pill></td>
                    <td className="num">{price(r.price_usd)}</td>
                    <td className="num">{compact(r.liquidity_usd)}</td>
                    <td className="num">{compact(r.fdv_usd)}</td>
                    <td className="num">{r.market_cap_usd ? compact(r.market_cap_usd) : <span className="muted" title="capitalización circulante no disponible">n/d</span>}</td>
                    <td className="num">{compact(r.volume_h24_usd)}</td>
                    <td className="num">{pct(r.price_change_h1, 0, false)}</td>
                    <td className="num">{pct(r.price_change_h24, 0, false)}</td>
                    <td className="num">{score(r.scores?.EPI_h)}</td>
                    <td className="num">{score(r.scores?.DQI)}</td>
                    <td className="num">{score(r.scores?.MRI)}</td>
                    <td><span className="reason">{(r.eligibility?.failed || []).join(", ") || (r.eligibility?.eligible ? "ninguno" : "—")}</span></td>
                    <td className="muted">{ago(r.observed_at)}</td>
                  </tr>
                ))}
              </Table>
            </Panel>
          </>
        )}
      </Guard>
    </>
  );
}

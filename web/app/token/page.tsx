"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { LineChart } from "@/components/LineChart";
import { Guard, PageHead, Panel, Pill, Table, stateTone, verdictTone } from "@/components/ui";
import { useApi } from "@/lib/api";
import { compact, price, when } from "@/lib/format";

function TokenView() {
  const id = useSearchParams().get("id");
  const t = useApi(id ? `/tokens/${id}` : null, 60_000);
  if (!id) return <div className="message">Elige un token desde Descubrimiento o Posiciones.</div>;
  return (
    <Guard state={t}>
      {(d) => {
        const tok = d.token;
        const sec = d.security?.[0];
        return (
          <>
            <PageHead title={`${tok.symbol || "?"} ${tok.name ? `(${tok.name})` : ""}`}
                      sub={`${tok.network}:${tok.address}. Primera observación ${when(tok.first_seen_at)}.`} />
            <div className="grid cols-3">
              <Panel title="Precio observado (USD)" className="span-2" primary>
                <LineChart label="Precio" format={price} series={[{
                  name: "Precio", color: "var(--gain)",
                  points: d.snapshots.filter((s: Record<string, string>) => s.price_usd)
                    .map((s: Record<string, string>) => ({ t: Date.parse(s.observed_at), v: Number(s.price_usd) })),
                }]} />
              </Panel>
              <Panel title="Ficha">
                <Table head={["Campo", "Valor"]}>
                  <tr><td>Estado</td><td><Pill tone={stateTone(tok.market_state)}>{tok.market_state}</Pill></td></tr>
                  <tr><td>Seguridad</td><td><Pill tone={verdictTone(sec?.verdict)}>{sec?.verdict || "UNKNOWN"}</Pill></td></tr>
                  <tr><td>Programa</td><td>{tok.token_program || "—"}</td></tr>
                  <tr><td>Launchpad</td><td>{tok.launchpad || "—"}</td></tr>
                  <tr><td>ATH observado</td><td>{price(tok.ath_price_usd)}</td></tr>
                  <tr><td>Olas</td><td>{tok.explosion_count}</td></tr>
                  <tr><td>Entradas</td><td>{tok.entries}</td></tr>
                  <tr><td>Universos</td><td className="muted">{(tok.universes || []).join(", ") || "—"}</td></tr>
                </Table>
              </Panel>
            </div>
            <div className="grid cols-2" style={{ marginTop: 16 }}>
              <Panel title="Hallazgos de seguridad (última evaluación)">
                {sec ? (
                  <Table head={["Comprobación", "Estado", "Evidencia", "Detalle"]}>
                    {sec.findings.map((f: Record<string, any>) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
                      <tr key={f.check}><td>{f.check}{f.critical ? " (crítica)" : ""}</td>
                        <td><Pill tone={f.status === "CRITICAL" ? "block" : f.status === "RISK" ? "risk" : f.status === "PASS" ? "pass" : ""}>{f.status}</Pill></td>
                        <td className="muted">{f.evidence}</td><td><span className="reason">{f.message}</span></td></tr>
                    ))}
                  </Table>
                ) : <div className="message">Sin evaluación de seguridad: sin ella no se puede comprar.</div>}
              </Panel>
              <Panel title="Indicadores (último vector)">
                {d.features ? (
                  <Table head={["Indicador", "Valor"]} numeric={[1]}>
                    {Object.entries(d.features.values).map(([k, v]) => (
                      <tr key={k}><td><code>{k}</code></td><td className="num">{Number(v).toPrecision(4)}</td></tr>
                    ))}
                  </Table>
                ) : <div className="message">Sin indicadores persistidos.</div>}
                {d.features?.missing?.length ? <p className="note">Ausentes: {d.features.missing.join(", ")}</p> : null}
              </Panel>
              <Panel title="Pools">
                <Table head={["Pool", "DEX", "Tipo", "Lado", "Principal"]}>
                  {d.pools.map((p: Record<string, any>) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
                    <tr key={p.id}><td className="muted">{p.address.slice(0, 8)}…</td><td>{p.dex}</td><td>{p.pool_type}</td>
                      <td>{p.token_side}</td><td>{p.is_primary ? "sí" : ""}</td></tr>
                  ))}
                </Table>
                <p className="note">Liquidez última: {compact(d.snapshots.at(-1)?.liquidity_usd)} USD</p>
              </Panel>
              <Panel title="Transiciones de estado">
                <Table head={["Desde", "De", "A"]}>
                  {d.states.map((s: Record<string, string>) => (
                    <tr key={s.id}><td>{when(s.valid_from)}</td><td className="muted">{s.previous_state}</td>
                      <td><Pill tone={stateTone(s.state)}>{s.state}</Pill></td></tr>
                  ))}
                </Table>
              </Panel>
            </div>
          </>
        );
      }}
    </Guard>
  );
}

export default function TokenPage() {
  return <Suspense fallback={<div className="message">Cargando…</div>}><TokenView /></Suspense>;
}

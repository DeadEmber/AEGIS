"use client";

import { Guard, PageHead, Panel, Pill, Table } from "@/components/ui";
import { useApi } from "@/lib/api";
import { eur, signClass, when } from "@/lib/format";

const ACCOUNTS: Record<string, string> = {
  "cash:investment": "Caja de inversión (aportaciones)",
  "cash:recycled": "Capital reciclado",
  "cash:reserve:profit": "Reserva de beneficios",
  "cash:reserve:tax": "Provisión fiscal estimada",
  "asset:positions": "Posiciones al coste",
  "asset:deposits": "Depósitos de renta recuperables",
  "equity:contributions": "Aportaciones (patrimonio)",
  "equity:withdrawals": "Retiradas",
  "pnl:realized": "P&L realizado (haber = ganancia)",
  "expense:network": "Gastos de red no capitalizados",
  "expense:technology": "Costes tecnológicos",
  "expense:tax_paid": "Impuestos pagados",
};

const EFF: [string, string, "eur" | "num" | "days" | "ratio" | "s"][] = [
  ["contributions_total_eur", "Aportaciones externas", "eur"],
  ["purchases_total_eur", "Capital desplegado en compras", "eur"],
  ["purchases_from_contributions_eur", "…financiado con aportaciones", "eur"],
  ["purchases_from_recycled_eur", "…financiado con capital reciclado", "eur"],
  ["investments_funded_by_recycled", "Inversiones financiadas con reciclado (total o parcial)", "num"],
  ["capital_recycling_ratio", "Capital Recycling Ratio (reinvertido / aportado)", "ratio"],
  ["capital_available_for_reinvestment_eur", "Capital disponible para reinversión", "eur"],
  ["recycled_pending_settlement_eur", "Cobros pendientes de liquidación", "eur"],
  ["avg_committed_capital_cost_eur", "Capital medio comprometido (coste + depósitos)", "eur"],
  ["avg_committed_capital_liquidable_eur", "Capital medio comprometido (liquidable)", "eur"],
  ["capital_turnover", "Capital Turnover (compras / capital medio)", "ratio"],
  ["avg_holding_period_days_realized", "Average Holding Period (vendido, ponderado por coste)", "days"],
  ["avg_age_open_lots_days", "Antigüedad media de lotes abiertos (censurada)", "days"],
  ["mean_days_to_capital_recovery", "Tiempo medio hasta recuperar el capital", "days"],
  ["avg_partial_recovery_fraction", "Fracción media de capital ya recuperada", "ratio"],
  ["avg_decision_to_execution_s", "Decisión → ejecución", "s"],
  ["avg_execution_to_availability_s", "Ejecución → capital disponible", "s"],
];

const KINDS: Record<string, string> = {
  time_to_expansion: "Hasta una expansión", explosion_duration: "Duración de la explosión",
  time_to_exhaustion: "Hasta el agotamiento", time_to_collapse: "Hasta el colapso", time_to_recovery: "Hasta la recuperación",
  time_to_second_wave: "Hasta la segunda ola", investment_duration: "Duración de la inversión",
  time_to_capital_recovery: "Hasta recuperar el capital",
};

function fmtEff(v: unknown, kind: string): string {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  if (kind === "eur") return eur(v, 2);
  if (kind === "days") return `${n.toFixed(2)} d`;
  if (kind === "s") return `${n.toFixed(1)} s`;
  if (kind === "ratio") return n.toFixed(3);
  return String(v);
}

function Efficiency() {
  const e = useApi("/capital/efficiency", 120_000);
  return (
    <Guard state={e}>
      {(d) => (
        <>
          <Panel title="Eficiencia temporal del capital"
                 note="Métricas descriptivas calculadas con marcas temporales inmutables. Las aportaciones externas y la reinversión interna se separan a partir de los apuntes de cada compra.">
            <Table head={["Métrica", "Valor"]} numeric={[1]}>
              {EFF.map(([k, label, kind]) => (
                <tr key={k}><td>{label}</td><td className="num">{fmtEff(d.efficiency[k], kind)}</td></tr>
              ))}
            </Table>
          </Panel>
          <Panel title="Duraciones observadas (Kaplan–Meier)"
                 note={`Sin eventos suficientes no se publica ninguna duración estimada. El ECV (Expected Capital Velocity) es experimental y no está disponible hasta que exista un modelo validado o suficientes posiciones cerradas. Uso en selección: ${d.ecv.enabled_in_selection ? "activado (experimental)" : "desactivado"}.`}>
            <Table head={["Duración", "Episodios", "Obs.", "Estado", "Mediana", "IC 95 %"]} numeric={[1, 2, 4]}>
              {Object.entries(d.temporal_estimates).map(([k, v]: [string, any]) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
                <tr key={k}>
                  <td>{KINDS[k] || k}</td>
                  <td className="num">{d.episode_counts[k]?.episodes ?? 0}</td>
                  <td className="num">{d.episode_counts[k]?.observed ?? 0}</td>
                  <td><Pill tone={v.status === "empirical_baseline" ? "ok" : ""}>
                    {v.status === "empirical_baseline" ? "base empírica" : v.status === "insufficient_data" ? "datos insuficientes" : "no disponible"}
                  </Pill></td>
                  <td className="num">{v.median_hours == null ? "—" : `${Number(v.median_hours).toFixed(1)} h`}</td>
                  <td className="muted">{v.ci_low_hours == null ? "—" : `${Number(v.ci_low_hours).toFixed(1)}–${Number(v.ci_high_hours).toFixed(1)} h`}</td>
                </tr>
              ))}
            </Table>
          </Panel>
        </>
      )}
    </Guard>
  );
}

export default function Capital() {
  const c = useApi("/capital", 60_000);
  return (
    <>
      <PageHead title="Capital" sub="Libro de partida doble en EUR. Cada transacción suma cero y la caja nunca puede quedar en negativo. La conciliación se recalcula en cada ciclo." />
      <Guard state={c}>
        {(d) => (
          <div className="grid cols-2">
            <Panel title="Saldos por cuenta" primary>
              <Table head={["Cuenta", "Saldo"]} numeric={[1]}>
                {Object.entries(d.balances).map(([k, v]) => (
                  <tr key={k}><td>{ACCOUNTS[k] || k} <span className="muted">{k}</span></td>
                    <td className="num">{eur(v, 4)}</td></tr>
                ))}
              </Table>
            </Panel>
            <Panel title={<>Conciliación <Pill tone={d.reconciliation.ok ? "ok" : "loss"}>{d.reconciliation.ok ? "correcta" : "fallida"}</Pill></>}>
              <Table head={["Comprobación", "Resultado", "Detalle"]}>
                {Object.entries(d.reconciliation.checks).map(([k, v]: [string, any]) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
                  <tr key={k}><td>{k}</td><td><Pill tone={v.ok ? "ok" : "loss"}>{v.ok ? "ok" : "error"}</Pill></td>
                    <td><span className="reason">{Object.entries(v).filter(([kk]) => kk !== "ok").map(([kk, vv]) => `${kk}=${String(vv).slice(0, 14)}`).join(", ")}</span></td></tr>
                ))}
              </Table>
            </Panel>
            <Panel title="Aportaciones">
              <Table head={["Periodo", "Fecha", "Importe"]} numeric={[2]}>
                {d.contributions.map((r: Record<string, string>) => (
                  <tr key={r.period}><td>{r.period}</td><td>{when(r.occurred_at)}</td><td className="num">{eur(r.amount_eur)}</td></tr>
                ))}
              </Table>
              <p className="note">Posiciones por bucket: {Object.entries(d.positions_by_bucket || {}).map(([k, v]) => `${k} ${v}`).join(", ") || "ninguna"}</p>
            </Panel>
            <Panel title="Estimación fiscal (IRPF, base del ahorro)" note="Estimación para provisionar, no es una declaración. Ver docs/RISK_MANAGEMENT.md.">
              <Table head={["Año", "Impuesto estimado"]} numeric={[1]}>
                {Object.entries(d.tax_estimate_by_year || {}).map(([y, v]) => (
                  <tr key={y}><td>{y}</td><td className="num">{eur(v)}</td></tr>
                ))}
              </Table>
            </Panel>
            <Efficiency />
            <Panel title="Movimientos del libro" className="span-2">
              <Table head={["Fecha", "Tipo", "Descripción", "Apuntes"]}>
                {d.transactions.map((t: Record<string, any>) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
                  <tr key={t.id}>
                    <td>{when(t.occurred_at)}</td><td><Pill>{t.kind}</Pill></td><td>{t.description}</td>
                    <td>{t.postings.map((p: Record<string, string>) => (
                      <div key={p.account} className={signClass(p.amount_eur)}>{p.account} {eur(p.amount_eur, 4)}</div>
                    ))}</td>
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

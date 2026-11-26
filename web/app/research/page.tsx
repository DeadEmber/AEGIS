"use client";

import Link from "next/link";

import { Guard, PageHead, Panel, Pill, Table } from "@/components/ui";
import { useApi } from "@/lib/api";
import { eur, mult, pct, share, when } from "@/lib/format";

const COHORTS: Record<string, string> = {
  rejected: "Rechazados por filtros o seguridad",
  not_selected: "Elegibles no comprados (presupuesto o ranking)",
  reentry_discarded: "Reentradas descartadas",
  order_rejected: "Orden rechazada en ejecución",
  after_exit: "Unidades vendidas al cerrar la posición",
  after_partial_sell: "Unidades vendidas en ventas parciales",
  reentry_blocked_residual: "Segunda ola con residual abierto (hipótesis E3)",
};

const STATUS: Record<string, [string, string]> = {
  observed: ["observado", "ok"], unknown: ["sin dato fresco", "risk"], pending: ["censurado", ""],
};

function Experiments() {
  const ex = useApi("/research/experiments", 600_000);
  return (
    <Guard state={ex} empty={(d) => !d.experiments?.length}>
      {(d) => (
        <div className="grid">
          {d.experiments.map((e: any) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
            <Panel key={e.id} title={`${e.id} — ${e.title}`}
                   note={`Generado ${when(e.generated_at)} · AEGIS ${e.aegis_version}${e.test_window ? ` · ventana ${e.test_window.map((t: string) => t.slice(0, 10)).join(" → ")}` : ""}. Datos reales point-in-time de periodos ya explorados (no es evidencia independiente). Valores netos de comisiones medidas y costes; nada de esto es una previsión.`}>
              <Table head={["Variante", "Dataset", "Patrimonio liquidable", "Aportado", "Compras", "Ventas",
                            "Retorno medio por ticket", "Cerradas con ganancia", "Llegan a x2", "Monetizadas ≥ x2",
                            "Captura del pico", "Efectivo ocioso"]}
                     numeric={[2, 3, 4, 5, 6, 7, 8, 9, 10, 11]}>
                {e.rows.map((r: any, i: number) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
                  <tr key={e.id + i}>
                    <td>{r.label}{r.owner_reported_eur != null ? <span className="muted"> (externo: {eur(r.owner_reported_eur)})</span> : null}</td>
                    <td className="muted">{r.dataset ?? "pumpfun_corpus"}</td>
                    <td className="num">{eur(r.net_worth_liquidable_eur)}</td>
                    <td className="num">{eur(r.contributions_eur)}</td>
                    <td className="num">{r.buys ?? "—"}</td><td className="num">{r.sells ?? "—"}</td>
                    <td className="num">{pct(r.mean_return_per_ticket)}</td>
                    <td className="num">{share(r.win_rate_closed)}</td>
                    <td className="num">{r.peak_ge_x2 ?? "—"}</td>
                    <td className="num">{r.peak_ge_x2_realised_ge_x2 ?? "—"}</td>
                    <td className="num">{mult(r.median_peak_capture)}</td>
                    <td className="num">{share(r.cash_idle_share)}</td>
                  </tr>
                ))}
              </Table>
            </Panel>
          ))}
        </div>
      )}
    </Guard>
  );
}

export default function Research() {
  const sh = useApi("/shadow", 120_000);
  return (
    <>
      <PageHead title="Laboratorio" sub="Aprendizaje contrafactual por horizontes. Cada horizonte se evalúa solo con una observación fresca dentro de su ventana: si no la hay, queda como «sin dato fresco»; si aún no se ha alcanzado, como «censurado». Nunca forma parte del patrimonio." />
      <div className="banner">
        Fase 2: backtests sobre datos reales point-in-time de periodos cortos ya explorados (Pumpfun Corpus
        19/06–15/07/2026 y Launch Tape 10/10/2026). No son una muestra independiente ni una previsión: ver
        docs/research/PHASE2_RESULTS.md y docs/BACKTEST_METHODOLOGY.md. No existe aún un backtest 2024–hoy.
      </div>
      <h2 className="section">Experimentos históricos (fase 2)</h2>
      <Experiments />
      <h2 className="section">Cartera sombra (paper trading)</h2>
      <Guard state={sh} empty={(d) => !d.positions?.length}>
        {(d) => (
          <div className="grid">
            <Panel title="Resultados por cohorte y horizonte" primary
                   note="Bruto: multiplicador de precio. Liquidable: venta estimada de la cantidad simulada con la liquidez de ese momento, tras impacto y comisiones.">
              <Table head={["Cohorte", "Horizonte", "Observados", "Sin dato fresco", "Censurados", "Mediana bruta", "Bruto ≥ x2", "Mediana liquidable", "Liquidable ≥ x2", "Liquidable < x0,5"]}
                     numeric={[2, 3, 4, 5, 6, 7, 8, 9]}>
                {Object.entries(d.summary).flatMap(([cohort, hz]: [string, any]) => // eslint-disable-line @typescript-eslint/no-explicit-any
                  d.horizons.map((h: string) => {
                    const g = hz[h] || {};
                    return (
                      <tr key={cohort + h}>
                        <td>{COHORTS[cohort] || cohort}</td><td>{h}</td>
                        <td className="num">{g.observed ?? 0}</td><td className="num">{g.unknown ?? 0}</td>
                        <td className="num">{g.censored ?? 0}</td>
                        <td className="num">{mult(g.gross_median)}</td><td className="num">{g.gross_ge_x2 ?? 0}</td>
                        <td className="num">{mult(g.liquidable_median)}</td><td className="num">{g.liquidable_ge_x2 ?? 0}</td>
                        <td className="num">{g.liquidable_lt_half ?? 0}</td>
                      </tr>
                    );
                  }))}
              </Table>
            </Panel>
            <Panel title="Seguimientos individuales">
              <Table head={["Apertura", "Token", "Cohorte", "Motivo", "Pico a fecha", ...d.horizons]} numeric={[4]}>
                {d.positions.slice(0, 300).map((p: Record<string, any>) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
                  <tr key={p.id}>
                    <td>{when(p.opened_at)}</td>
                    <td><Link href={`/token?id=${p.token_id}`}>{p.symbol}</Link></td>
                    <td className="muted">{p.cohort}</td>
                    <td><span className="reason">{p.reason}</span></td>
                    <td className="num">{mult(p.peak_multiple_to_date)}</td>
                    {d.horizons.map((h: string) => {
                      const v = p.horizons?.[h] || { status: "pending" };
                      const [label, tone] = STATUS[v.status] || [v.status, ""];
                      return <td key={h}>{v.status === "observed" ? mult(v.gross_multiple) : <Pill tone={tone}>{label}</Pill>}</td>;
                    })}
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

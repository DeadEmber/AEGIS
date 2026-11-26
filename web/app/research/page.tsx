"use client";

import Link from "next/link";

import { Guard, PageHead, Panel, Table } from "@/components/ui";
import { useApi } from "@/lib/api";
import { mult, when } from "@/lib/format";

const COHORTS: Record<string, string> = {
  rejected: "Rechazados por filtros o seguridad",
  not_selected: "Elegibles no comprados (presupuesto o ranking)",
  order_rejected: "Orden rechazada en ejecución",
  after_exit: "Tras nuestra salida total",
};

export default function Research() {
  const sh = useApi("/shadow", 120_000);
  return (
    <>
      <PageHead title="Laboratorio" sub="Aprendizaje contrafactual: qué habría pasado con lo que no compramos y con lo que vendimos. Son multiplicadores de precio brutos, sin impacto ni liquidez, y nunca forman parte del patrimonio." />
      <div className="banner">
        El backtest 2024–hoy sobre un universo histórico real todavía no existe: las APIs gratuitas solo
        sirven pools vivos (sesgo de supervivencia). Ver docs/BACKTEST_METHODOLOGY.md. La comparación de
        estrategias disponible (<code>research.compare</code>) solo valida la mecánica sobre un mercado simulado.
      </div>
      <Guard state={sh} empty={(d) => !d.positions?.length}>
        {(d) => (
          <div className="grid">
            <Panel title="Cohortes" primary>
              <Table head={["Cohorte", "Seguimientos", "Pico ≥ x2", "Pico ≥ x10", "Ahora < x0,5"]} numeric={[1, 2, 3, 4]}>
                {Object.entries(d.cohorts).map(([k, v]: [string, any]) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
                  <tr key={k}>
                    <td>{COHORTS[k] || k}</td><td className="num">{v.n}</td>
                    <td className="num">{v.peak_x2} ({((100 * v.peak_x2) / v.n).toFixed(0)} %)</td>
                    <td className="num">{v.peak_x10} ({((100 * v.peak_x10) / v.n).toFixed(0)} %)</td>
                    <td className="num">{v.below_half} ({((100 * v.below_half) / v.n).toFixed(0)} %)</td>
                  </tr>
                ))}
              </Table>
              <p className="note">Horizonte de seguimiento: 14 días. Los picos son posteriores a la decisión y nunca
                se usan como variables en tiempo real.</p>
            </Panel>
            <Panel title="Seguimientos individuales">
              <Table head={["Apertura", "Token", "Cohorte", "Motivo", "Pico", "Actual", "Cerrado"]} numeric={[4, 5]}>
                {d.positions.slice(0, 300).map((p: Record<string, any>) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
                  <tr key={p.id}>
                    <td>{when(p.opened_at)}</td>
                    <td><Link href={`/token?id=${p.token_id}`}>{p.symbol}</Link></td>
                    <td className="muted">{p.cohort}</td>
                    <td><span className="reason">{p.reason}</span></td>
                    <td className="num">{mult(p.peak_multiple)}</td>
                    <td className="num">{mult(p.current_multiple)}</td>
                    <td className="muted">{p.closed_at ? when(p.closed_at) : "abierto"}</td>
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

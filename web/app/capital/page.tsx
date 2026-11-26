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
                    <td className="muted">{Object.entries(v).filter(([kk]) => kk !== "ok").map(([kk, vv]) => `${kk}=${String(vv).slice(0, 14)}`).join(", ")}</td></tr>
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

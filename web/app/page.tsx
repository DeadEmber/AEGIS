"use client";

import Link from "next/link";

import { LineChart } from "@/components/LineChart";
import { TicketField } from "@/components/TicketField";
import { Guard, Panel, Pill, Stat, Table } from "@/components/ui";
import { useApi } from "@/lib/api";
import { ago, eur, pct, signClass, signedEur, toNum, when } from "@/lib/format";

export default function Overview() {
  const ov = useApi("/overview");
  const hist = useApi("/portfolio/history?limit=3000", 60_000);
  const pos = useApi("/portfolio/positions?status=open&limit=5000", 60_000);
  const dec = useApi("/decisions?limit=12");

  return (
    <Guard state={ov} empty={(d) => !d.snapshot}>
      {(d) => {
        const s = d.snapshot;
        const k = d.kpi ?? {};
        const b = d.balances ?? {};
        const stale = s && Date.now() - new Date(s.as_of).getTime() > 15 * 60_000;
        const reserve = (toNum(b["cash:reserve:profit"]) ?? 0) + (toNum(b["cash:reserve:tax"]) ?? 0);
        return (
          <>
            {stale ? (
              <div className="banner" role="alert">
                El último cálculo de patrimonio es de {when(s.as_of)} ({ago(s.as_of)}). El worker puede estar
                detenido. Comprueba la página Sistema.
              </div>
            ) : null}
            <div className="grid cols-4">
              <Panel className="span-2" primary>
                <div className="stat">
                  <div className="label">Resultado frente a lo aportado (objetivo)</div>
                  <div className={`hero-figure ${signClass(s.objective_eur)}`}>{signedEur(s.objective_eur)}</div>
                  <div className="sub">
                    Patrimonio liquidable {eur(s.net_worth_liquidable_eur)} + retirado {eur(s.withdrawals_total_eur)} −
                    {" "}aportado {eur(s.contributions_total_eur)}
                  </div>
                </div>
              </Panel>
              <Panel>
                <Stat label="Rentabilidad sobre aportaciones" value={pct(k.net_portfolio_return, 2)}
                      tone={signClass(k.net_portfolio_return)}
                      sub={<>TWR {pct(k.twr, 2)}, drawdown máx. {k.max_drawdown_twr ? pct(-Number(k.max_drawdown_twr), 1) : "—"}</>} />
              </Panel>
              <Panel>
                <Stat label="XIRR" value={k.xirr === null || k.xirr === undefined ? "—" : pct(k.xirr, 1)}
                      sub={k.xirr ? "anualizada" : "disponible tras 30 días de historia"} />
              </Panel>
            </div>

            <div className="grid cols-4" style={{ marginTop: 16 }}>
              <Panel><Stat label="Caja de inversión" value={eur(b["cash:investment"])} sub="de aportaciones" /></Panel>
              <Panel><Stat label="Capital reciclado" value={eur(b["cash:recycled"])}
                           sub={`reciclado acumulado ${eur(d.capital_recycled_total_eur)}`} /></Panel>
              <Panel><Stat label="Reservas" value={eur(reserve)}
                           sub={`fiscal ${eur(b["cash:reserve:tax"])}, no reinvertible`} /></Panel>
              <Panel><Stat label="Posiciones (liquidable / mercado)" value={eur(s.positions_liquidation_eur)}
                           sub={<>{eur(s.positions_mark_eur)} a mercado, depósitos {eur(s.deposits_eur)}</>} /></Panel>
            </div>

            <div className="grid cols-3" style={{ marginTop: 16 }}>
              <Panel title="Patrimonio liquidable frente a aportaciones" className="span-2"
                     note="El valor liquidable descuenta impacto y comisiones de salida. No es el valor de mercado.">
                <Guard state={hist} empty={(h) => !h.points?.length}>
                  {(h) => (
                    <LineChart label="Evolución patrimonial" format={(v) => eur(v)} series={[
                      { name: "Patrimonio liquidable", color: "var(--gain)",
                        points: h.points.map((p: Record<string, string>) => ({ t: Date.parse(p.as_of), v: Number(p.net_worth_liquidable_eur) })) },
                      { name: "Aportado", color: "var(--muted)", dashed: true,
                        points: h.points.map((p: Record<string, string>) => ({ t: Date.parse(p.as_of), v: Number(p.contributions_total_eur) })) },
                    ]} />
                  )}
                </Guard>
              </Panel>
              <Panel title={`Campo de tickets (${s.open_positions} posiciones abiertas)`}
                     note={<>Cada celda es una posición, coloreada por su multiplicador neto liquidable.
                       {s.stale_positions ? ` ${s.stale_positions} con datos antiguos (valoradas con descuento).` : ""}
                       {s.unvalued_positions ? ` ${s.unvalued_positions} sin valorar.` : ""}</>}>
                <Guard state={pos} empty={(p) => !p.positions?.length}>
                  {(p) => <TicketField positions={p.positions} />}
                </Guard>
              </Panel>
            </div>

            <div className="grid cols-3" style={{ marginTop: 16 }}>
              <Panel title="Últimas decisiones" className="span-2">
                <Guard state={dec} empty={(x) => !x.decisions?.length}>
                  {(x) => (
                    <Table head={["Hora", "Token", "Acción", "Motivo", "Resultado"]}>
                      {x.decisions.map((r: Record<string, any>) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
                        <tr key={r.id}>
                          <td>{when(r.decided_at)}</td>
                          <td>{r.token_id ? <Link href={`/token?id=${r.token_id}`}>{r.symbol || r.token_id}</Link> : "—"}</td>
                          <td><Pill tone={r.action.includes("SELL") ? "hot" : r.action === "BUY" || r.action === "REENTRY" ? "pass" : ""}>{r.action}</Pill></td>
                          <td><span className="reason">{(r.reasons || []).join("; ")}</span></td>
                          <td className="muted">{r.outcome}</td>
                        </tr>
                      ))}
                    </Table>
                  )}
                </Guard>
              </Panel>
              <Panel title="Actividad">
                <Table head={["Métrica", "Valor"]} numeric={[1]}>
                  <tr><td>Tokens monitorizados</td><td className="num">{d.counts.tokens_tracked}</td></tr>
                  <tr><td>Compras simuladas</td><td className="num">{d.counts.buys}</td></tr>
                  <tr><td>Ventas simuladas</td><td className="num">{d.counts.sells}</td></tr>
                  <tr><td>Órdenes rechazadas</td><td className="num">{d.counts.rejected_orders}</td></tr>
                  <tr><td>P&amp;L realizado</td><td className={`num ${signClass(s.realized_pnl_total_eur)}`}>{signedEur(s.realized_pnl_total_eur)}</td></tr>
                  <tr><td>Costes explícitos</td><td className="num">{eur(s.costs_total_eur, 4)}</td></tr>
                  <tr><td>Impuesto estimado del año</td><td className="num">{eur(s.est_tax_realized_ytd_eur)}</td></tr>
                </Table>
              </Panel>
            </div>
          </>
        );
      }}
    </Guard>
  );
}

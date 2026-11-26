"use client";

import { Guard, PageHead, Panel, Pill, Table } from "@/components/ui";
import { useApi } from "@/lib/api";
import { ago, when } from "@/lib/format";

export default function System() {
  const s = useApi("/system", 20_000);
  return (
    <>
      <PageHead title="Sistema" sub="Fuentes de datos, worker, cola de monitorización y volumen almacenado." />
      <Guard state={s}>
        {(d) => (
          <div className="grid cols-2">
            <Panel title="Fuentes de datos" primary className="span-2">
              <Table head={["Fuente", "Estado", "Llamadas", "Errores", "Límite (429)", "Latencia media", "Último OK", "Último error"]} numeric={[2, 3, 4, 5]}>
                {d.sources.map((r: Record<string, any>) => { // eslint-disable-line @typescript-eslint/no-explicit-any
                  const failing = r.consecutive_errors > 0;
                  return (
                    <tr key={r.source}>
                      <td>{r.source}</td>
                      <td><Pill tone={failing ? "loss" : r.calls_total ? "ok" : ""}>{failing ? `${r.consecutive_errors} errores seguidos` : r.calls_total ? "ok" : "sin uso"}</Pill></td>
                      <td className="num">{r.calls_total}</td><td className="num">{r.errors_total}</td>
                      <td className="num">{r.rate_limited_total}</td>
                      <td className="num">{r.latency_ms_ewma ? `${Number(r.latency_ms_ewma).toFixed(0)} ms` : "—"}</td>
                      <td>{ago(r.last_ok_at)}</td>
                      <td className="muted"><span className="reason">{r.last_error || "—"}</span></td>
                    </tr>
                  );
                })}
              </Table>
              <p className="note">Las métricas se acumulan desde el último arranque del worker.</p>
            </Panel>
            <Panel title="Worker">
              {d.workers.length ? (
                <Table head={["Id", "Estado", "Ciclos", "Último latido", "Versión"]}>
                  {d.workers.map((w: Record<string, any>) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
                    <tr key={w.worker_id}><td>{w.worker_id}</td><td><Pill tone="ok">{w.status}</Pill></td><td>{w.cycles}</td>
                      <td>{when(w.last_beat_at)} ({ago(w.last_beat_at)})</td><td>{w.version}</td></tr>
                  ))}
                </Table>
              ) : <div className="message">Ningún worker ha arrancado todavía. Lanza <code>aegis run</code>.</div>}
            </Panel>
            <Panel title="Cola de monitorización">
              <Table head={["Clase", "Tokens"]} numeric={[1]}>
                {Object.entries(d.scheduler).map(([k, v]) => <tr key={k}><td>{k}</td><td className="num">{String(v)}</td></tr>)}
              </Table>
            </Panel>
            <Panel title="Filas almacenadas">
              <Table head={["Tabla", "Filas"]} numeric={[1]}>
                {Object.entries(d.table_counts).map(([k, v]) => <tr key={k}><td>{k}</td><td className="num">{String(v)}</td></tr>)}
              </Table>
            </Panel>
            <Panel title="Eventos recientes">
              <Table head={["Fecha", "Tipo", "Mensaje"]}>
                {d.events.slice(0, 40).map((e: Record<string, any>) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
                  <tr key={e.id}><td>{when(e.occurred_at)}</td>
                    <td><Pill tone={e.severity === "error" ? "loss" : e.severity === "warn" ? "risk" : ""}>{e.kind}</Pill></td>
                    <td className="muted">{e.message}{e.details?.errors?.length ? ` (${e.details.errors.length} errores)` : ""}</td></tr>
                ))}
              </Table>
            </Panel>
          </div>
        )}
      </Guard>
    </>
  );
}

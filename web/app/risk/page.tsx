"use client";

import { useState } from "react";

import { Guard, PageHead, Panel, Pill, Table } from "@/components/ui";
import { API_BASE, useApi } from "@/lib/api";
import { when } from "@/lib/format";

function KillSwitch({ active, onDone }: { active: boolean; onDone: () => void }) {
  const [token, setToken] = useState("");
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const submit = async () => {
    setMsg(null);
    const res = await fetch(`${API_BASE}/control/kill-switch`, {
      method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ on: !active, reason }),
    }).catch((e) => ({ ok: false, status: 0, statusText: String(e) }) as Response);
    if (res.ok) { setMsg(active ? "Kill switch desactivado." : "Kill switch activado."); onDone(); }
    else if (res.status === 403) setMsg("Los controles están desactivados: define AEGIS_API_CONTROL_TOKEN en la API.");
    else if (res.status === 401) setMsg("Token incorrecto.");
    else setMsg(`No se pudo cambiar el kill switch (${res.status || "sin conexión"}).`);
  };
  return (
    <form className="toolbar" onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <input type="password" placeholder="Token de control" value={token} onChange={(e) => setToken(e.target.value)}
             aria-label="Token de control" required />
      <input placeholder="Motivo" value={reason} onChange={(e) => setReason(e.target.value)} aria-label="Motivo" required />
      <button type="submit">{active ? "Desactivar kill switch" : "Activar kill switch"}</button>
      {msg ? <span role="status" className="muted">{msg}</span> : null}
    </form>
  );
}

export default function Risk() {
  const r = useApi("/risk");
  return (
    <>
      <PageHead title="Riesgo y control" sub="Interruptor de emergencia, límites inmutables, veredictos de seguridad y eventos. El aprendizaje no puede modificar nada de esta página." />
      <Guard state={r}>
        {(d) => {
          const flags: Record<string, any> = Object.fromEntries(d.flags.map((f: Record<string, string>) => [f.key, f])); // eslint-disable-line @typescript-eslint/no-explicit-any
          const active = flags.kill_switch?.value === "on";
          return (
            <div className="grid cols-2">
              <Panel title={<>Kill switch <Pill tone={active ? "loss" : "ok"}>{active ? "activo" : "inactivo"}</Pill></>} primary
                     note="Con el kill switch activo el worker sigue observando y valorando, pero no emite órdenes.">
                <KillSwitch active={active} onDone={r.reload} />
                {flags.kill_switch ? <p className="note">Último cambio {when(flags.kill_switch.updated_at)} por {flags.kill_switch.updated_by}: {flags.kill_switch.reason}</p> : null}
              </Panel>
              <Panel title="Límites duros">
                <Table head={["Límite", "Valor"]}>
                  {Object.entries(d.limits).map(([k, v]) => <tr key={k}><td>{k}</td><td>{String(v)}</td></tr>)}
                </Table>
              </Panel>
              <Panel title="Veredictos de seguridad">
                <Table head={["Veredicto", "Evaluaciones"]} numeric={[1]}>
                  {Object.entries(d.assessment_verdicts).map(([k, v]) => (
                    <tr key={k}><td><Pill tone={k === "BLOCK" ? "block" : k === "RISK" ? "risk" : "pass"}>{k}</Pill></td><td className="num">{String(v)}</td></tr>
                  ))}
                </Table>
              </Panel>
              <Panel title="Eventos de riesgo">
                {d.risk_events.length ? (
                  <Table head={["Fecha", "Tipo", "Severidad", "Detalle"]}>
                    {d.risk_events.map((e: Record<string, any>) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
                      <tr key={e.id}><td>{when(e.occurred_at)}</td><td>{e.kind}</td><td><Pill tone="loss">{e.severity}</Pill></td>
                        <td className="muted">{JSON.stringify(e.details).slice(0, 160)}</td></tr>
                    ))}
                  </Table>
                ) : <div className="message">Sin eventos de riesgo.</div>}
              </Panel>
              <Panel title="Hallazgos críticos de seguridad" className="span-2">
                <Table head={["Fecha", "Token", "Hallazgo", "Evidencia", "Detalle"]}>
                  {d.security_events.map((e: Record<string, any>) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
                    <tr key={e.id}><td>{when(e.occurred_at)}</td><td>{e.symbol}</td><td>{e.kind}</td>
                      <td><Pill tone={e.status === "CONFIRMED" ? "block" : "risk"}>{e.status}</Pill></td>
                      <td className="muted">{e.details?.message || ""}</td></tr>
                  ))}
                </Table>
              </Panel>
            </div>
          );
        }}
      </Guard>
    </>
  );
}

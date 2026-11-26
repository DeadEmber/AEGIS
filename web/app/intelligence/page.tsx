"use client";

import { useState } from "react";

import { Guard, PageHead, Panel, Pill, Table } from "@/components/ui";
import { useApi } from "@/lib/api";
import { when } from "@/lib/format";

export default function Intelligence() {
  const intel = useApi("/intelligence", 120_000);
  const [action, setAction] = useState("");
  const dec = useApi(`/decisions?limit=300${action ? `&action=${action}` : ""}`);
  return (
    <>
      <PageHead title="Modelos y decisiones" sub="Qué decide el sistema, con qué versión y por qué. Ninguna decisión económica procede de IA generativa; todas son deterministas y reproducibles a partir de su hash de entradas." />
      <div className="grid cols-3">
        <Panel title="Scoring operativo" primary>
          <Guard state={intel}>
            {(d) => (
              <>
                <p style={{ margin: 0 }}>{d.active_scoring.name}: <Pill tone="alert">heurístico</Pill></p>
                <p className="note">No es una probabilidad calibrada. Un modelo entrenado solo puede sustituirlo tras
                  validación walk-forward, calibración y revisión (Champion–Challenger).</p>
                <p className="note">Estrategia {d.strategy.name} {d.strategy.version}, hash <code>{d.strategy_hash}</code></p>
                <p className="note">Modelos entrenados registrados: {d.models.length}</p>
              </>
            )}
          </Guard>
        </Panel>
        <Panel title="Distribución de decisiones" className="span-2">
          <Guard state={dec}>
            {(d) => (
              <div className="toolbar" style={{ marginBottom: 0 }}>
                {Object.entries(d.counts || {}).map(([k, v]) => (
                  <button key={k} type="button" aria-pressed={action === k} onClick={() => setAction(action === k ? "" : k)}>
                    {k} {String(v)}
                  </button>
                ))}
              </div>
            )}
          </Guard>
        </Panel>
      </div>
      <Panel title={action ? `Decisiones ${action}` : "Decisiones recientes"} className="" >
        <Guard state={dec} empty={(d) => !d.decisions?.length}>
          {(d) => (
            <Table head={["Hora", "Token", "Acción", "Bucket", "Motivo", "Scores", "Hash de entradas", "Resultado"]}>
              {d.decisions.map((r: Record<string, any>) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
                <tr key={r.id}>
                  <td>{when(r.decided_at)}</td>
                  <td>{r.symbol || "—"}</td>
                  <td><Pill>{r.action}</Pill></td>
                  <td className="muted">{r.bucket || "—"}</td>
                  <td><span className="reason">{(r.reasons || []).join("; ")}</span></td>
                  <td className="muted">{Object.entries(r.scores || {}).filter(([, v]) => v !== null).slice(0, 4)
                    .map(([k, v]) => `${k} ${Number(v).toFixed(2)}`).join(", ")}</td>
                  <td><code>{r.inputs_hash}</code></td>
                  <td className="muted">{r.outcome}</td>
                </tr>
              ))}
            </Table>
          )}
        </Guard>
      </Panel>
      <Panel title="Catálogo de indicadores">
        <Guard state={intel}>
          {(d) => (
            <Table head={["Indicador", "Grupo", "Unidad", "Horizonte", "Definición"]}>
              {d.feature_catalog.map((f: Record<string, string>) => (
                <tr key={f.name}><td><code>{f.name}</code></td><td className="muted">{f.group}</td><td>{f.unit}</td>
                  <td>{f.horizon}</td><td><span className="reason">{f.definition}</span></td></tr>
              ))}
            </Table>
          )}
        </Guard>
      </Panel>
    </>
  );
}

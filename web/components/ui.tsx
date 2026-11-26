"use client";

import type { ReactNode } from "react";

import { API_BASE, type ApiState } from "@/lib/api";

export function Panel({ title, children, note, className = "", primary = false }: {
  title?: ReactNode; children: ReactNode; note?: ReactNode; className?: string; primary?: boolean;
}) {
  return (
    <section className={`panel ${primary ? "primary" : ""} ${className}`}>
      {title ? <h2>{title}</h2> : null}
      {children}
      {note ? <p className="note">{note}</p> : null}
    </section>
  );
}

export function Stat({ label, value, sub, tone = "" }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className={`value ${tone}`}>{value}</div>
      {sub ? <div className="sub">{sub}</div> : null}
    </div>
  );
}

export function Pill({ children, tone = "" }: { children: ReactNode; tone?: string }) {
  return <span className={`pill ${tone}`}>{children}</span>;
}

export function verdictTone(v: string | null | undefined): string {
  return v === "BLOCK" ? "block" : v === "RISK" ? "risk" : v === "PASS" ? "pass" : "";
}

const HOT = new Set(["EXPANSION", "EXPLOSION", "DISTRIBUTION", "SECOND_WAVE"]);
export function stateTone(s: string | null | undefined): string {
  if (!s) return "";
  if (HOT.has(s)) return "hot";
  if (["COLLAPSE", "UNTRADABLE", "REJECTED"].includes(s)) return "loss";
  if (["RECOVERY", "ACCUMULATION"].includes(s)) return "alert";
  return "";
}

/** Uniform loading / error / empty handling. Errors say what happened and how to fix it. */
export function Guard<T>({ state, empty, children }: {
  state: ApiState<T>; empty?: (d: T) => boolean; children: (d: T) => ReactNode;
}) {
  if (state.error && !state.data) {
    return (
      <div className="message" role="alert">
        <strong>No se puede leer la API de AEGIS.</strong>
        {state.error}. Arranca la API con <code>aegis api --port 8000</code> o ajusta
        {" "}<code>NEXT_PUBLIC_AEGIS_API</code> (ahora {API_BASE}).
      </div>
    );
  }
  if (state.loading && !state.data) return <div className="message">Cargando…</div>;
  if (!state.data) return null;
  if (empty && empty(state.data)) {
    return (
      <div className="message">
        <strong>Todavía no hay datos.</strong>
        El worker los genera en cada ciclo. Lanza <code>aegis once</code> para un ciclo de paper trading o
        {" "}<code>aegis demo</code> para un mercado simulado.
      </div>
    );
  }
  return <>{children(state.data)}</>;
}

export function Table({ head, children, numeric = [] }: { head: string[]; children: ReactNode; numeric?: number[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{head.map((h, i) => <th key={h + i} className={numeric.includes(i) ? "num" : ""} scope="col">{h}</th>)}</tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function PageHead({ title, sub }: { title: string; sub?: string }) {
  return (
    <>
      <h1 className="page-title">{title}</h1>
      {sub ? <p className="page-sub">{sub}</p> : null}
    </>
  );
}

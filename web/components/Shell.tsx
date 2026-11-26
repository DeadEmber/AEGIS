"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useEffect, useState } from "react";

import { useApi } from "@/lib/api";
import { ago, eur } from "@/lib/format";

const NAV: { group: string; items: [string, string][] }[] = [
  { group: "Cartera", items: [["/", "Resumen"], ["/portfolio", "Posiciones"], ["/capital", "Capital"]] },
  { group: "Mercado", items: [["/discovery", "Descubrimiento"], ["/peak-radar", "Peak radar"], ["/second-wave", "Segundas olas"]] },
  { group: "Inteligencia", items: [["/intelligence", "Modelos y decisiones"], ["/research", "Laboratorio"]] },
  { group: "Control", items: [["/risk", "Riesgo"], ["/system", "Sistema"]] },
];

function StatusBar() {
  const health = useApi("/health", 20_000);
  const ov = useApi("/overview", 30_000);
  const risk = useApi("/risk", 30_000);
  const mode: string = health.data?.data_mode ?? "empty";
  const snap = ov.data?.snapshot;
  const worker = health.data?.workers?.[0];
  const kill = (risk.data?.flags ?? []).find((f: { key: string }) => f.key === "kill_switch")?.value === "on";
  const label = mode === "simulated" ? "Mercado simulado" : mode === "live" ? "Mercado real, ejecución simulada" : "Sin datos";
  return (
    <div className="statusbar" role="status">
      <span className={`mode ${mode}`}>{label}</span>
      {health.error ? <span className="loss">API no disponible</span> : null}
      <span>Patrimonio liquidable <strong>{eur(snap?.net_worth_liquidable_eur)}</strong></span>
      <span>Conciliación <strong className={snap ? (snap.reconciliation_ok ? "ok" : "loss") : ""}>
        {snap ? (snap.reconciliation_ok ? "correcta" : "fallida") : "—"}</strong></span>
      <span>Último ciclo <strong>{ago(worker?.last_beat_at)}</strong></span>
      {kill ? <span className="loss"><strong className="loss">Kill switch activo</strong></span> : null}
      <span className="spacer" />
      <ThemeToggle />
    </div>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useState<string | null>(null);
  useEffect(() => {
    try { setTheme(localStorage.getItem("aegis-theme")); } catch { /* storage unavailable */ }
  }, []);
  useEffect(() => {
    if (theme) document.documentElement.dataset.theme = theme;
    else delete document.documentElement.dataset.theme;
  }, [theme]);
  const next = theme === "dark" ? "light" : theme === "light" ? null : "dark";
  return (
    <button type="button" onClick={() => {
      setTheme(next);
      try { next ? localStorage.setItem("aegis-theme", next) : localStorage.removeItem("aegis-theme"); } catch { /* */ }
    }}>
      Tema: {theme === "dark" ? "oscuro" : theme === "light" ? "claro" : "sistema"}
    </button>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  return (
    <div className="shell">
      <nav className="nav" aria-label="Módulos">
        <div className="brand"><b>AEGIS</b><span>paper trading</span></div>
        {NAV.map((g) => (
          <div key={g.group}>
            <div className="group">{g.group}</div>
            {g.items.map(([href, name]) => (
              <Link key={href} href={href} aria-current={path === href ? "page" : undefined}>{name}</Link>
            ))}
          </div>
        ))}
      </nav>
      <div className="main">
        <StatusBar />
        <main className="content">{children}</main>
      </div>
    </div>
  );
}

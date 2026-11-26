/** Formatting helpers. Inputs are decimal strings from the API; we only convert to Number for display. */

const num = (v: unknown): number | null => {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
};

export function eur(v: unknown, digits = 2): string {
  const n = num(v);
  if (n === null) return "—";
  return new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR", minimumFractionDigits: digits,
    maximumFractionDigits: digits }).format(n);
}

export function signedEur(v: unknown, digits = 2): string {
  const n = num(v);
  if (n === null) return "—";
  return (n > 0 ? "+" : "") + eur(n, digits);
}

export function pct(v: unknown, digits = 1, fromFraction = true): string {
  const n = num(v);
  if (n === null) return "—";
  const x = fromFraction ? n * 100 : n;
  return `${x > 0 ? "+" : ""}${x.toLocaleString("es-ES", { maximumFractionDigits: digits, minimumFractionDigits: digits })} %`;
}

/** Unsigned share of a whole (win rate, idle cash): never prefixed with "+". */
export function share(v: unknown, digits = 1): string {
  const n = num(v);
  if (n === null) return "—";
  return `${(n * 100).toLocaleString("es-ES", { maximumFractionDigits: digits, minimumFractionDigits: digits })} %`;
}

export function mult(v: unknown): string {
  const n = num(v);
  if (n === null) return "—";
  return `x${n >= 100 ? n.toFixed(0) : n >= 10 ? n.toFixed(1) : n.toFixed(2)}`;
}

export function price(v: unknown): string {
  const n = num(v);
  if (n === null) return "—";
  if (n === 0) return "0";
  if (Math.abs(n) < 0.0001) return n.toExponential(3);
  return n.toLocaleString("es-ES", { maximumSignificantDigits: 5 });
}

export function compact(v: unknown): string {
  const n = num(v);
  if (n === null) return "—";
  return new Intl.NumberFormat("es-ES", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function score(v: unknown): string {
  const n = num(v);
  return n === null ? "—" : n.toFixed(2);
}

export function short(addr: string | null | undefined): string {
  if (!addr) return "—";
  return addr.length > 12 ? `${addr.slice(0, 4)}…${addr.slice(-4)}` : addr;
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 0) return "ahora";
  if (s < 90) return `hace ${Math.round(s)} s`;
  if (s < 5400) return `hace ${Math.round(s / 60)} min`;
  if (s < 172800) return `hace ${Math.round(s / 3600)} h`;
  return `hace ${Math.round(s / 86400)} d`;
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("es-ES", { dateStyle: "short", timeStyle: "short" });
}

export function signClass(v: unknown): string {
  const n = num(v);
  if (n === null || n === 0) return "";
  return n > 0 ? "gain" : "loss";
}

export const toNum = num;

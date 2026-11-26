"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export const API_BASE = (process.env.NEXT_PUBLIC_AEGIS_API || "http://localhost:8000").replace(/\/$/, "");

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Json = any;

export interface ApiState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  updatedAt: Date | null;
  reload: () => void;
}

/** Fetch an AEGIS API path and poll it. Monetary values arrive as exact decimal strings. */
export function useApi<T = Json>(path: string | null, pollMs = 30_000): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const tick = useRef(0);

  const load = useCallback(async () => {
    if (!path) return;
    const n = ++tick.current;
    try {
      const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`La API respondió ${res.status} en ${path}`);
      const json = (await res.json()) as T;
      if (n === tick.current) {
        setData(json);
        setError(null);
        setUpdatedAt(new Date());
      }
    } catch (e) {
      if (n === tick.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (n === tick.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    setLoading(true);
    load();
    if (!pollMs) return;
    const id = setInterval(load, pollMs);
    return () => clearInterval(id);
  }, [load, pollMs]);

  return { data, error, loading, updatedAt, reload: load };
}

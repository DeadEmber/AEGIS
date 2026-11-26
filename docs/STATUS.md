# Estado del proyecto (registro persistente entre sesiones)

Última actualización: 2026-11-26 · rama `claude/aegis-foundation` · versión 0.2.0

## Resumen

El *vertical slice* completo funciona con datos reales: descubrimiento → seguridad → indicadores →
decisión → ejecución simulada con cotizaciones de Jupiter y costes de Solana → contabilidad de partida
doble → conciliación → API → dashboard. **La rentabilidad de la estrategia no está demostrada** y no
puede demostrarse todavía (ver `BACKTEST_METHODOLOGY.md`).

## Validación en vivo realizada (2026-11-26, entorno de desarrollo)

| Prueba | Resultado |
|---|---|
| `aegis probe` | 6/6 fuentes OK (GeckoTerminal 358 ms, DexScreener 313 ms, Jupiter 324 ms, RPC 385 ms, BCE 352 ms, CoinGecko 435 ms) |
| `aegis once` (ciclo 1) | 60 observaciones, 58 tokens nuevos, 4 chequeos de seguridad, 4 compras simuladas con cotización de Jupiter, 0 errores, conciliación OK, 28 s |
| `aegis once` (ciclo 2) | 76 observaciones, 4 posiciones valoradas (liquidable 3,51 EUR + depósitos 0,39 EUR frente a 4,00 EUR invertidos), rechazos explicados (FDV 32, precio 13, antigüedad 3), conciliación OK |
| PostgreSQL 16 local | migración Alembic + 24 ciclos simulados OK, decimales exactos, índice parcial único |
| Dashboard | `next build` OK (14 rutas estáticas), revisado con capturas en escritorio y móvil (390 px, sin scroll horizontal) en temas claro y oscuro, sin errores de consola contra la API sobre una base de demo |

Hallazgos con datos reales:

- `market_cap_usd` es `null` en GeckoTerminal para la mayoría de tokens nuevos. El filtro FDV explícito
  es el que más rechaza.
- Impacto de hasta el 2,4 % para 0,9 EUR en pools que anuncian más de 10.000 EUR de liquidez.
- Volumen orgánico del 0,07 % según Jupiter en un token activo.
- Pump.fun crea ahora mints **Token-2022**, cuya cuenta de token es mayor y tiene una renta más alta.
- La renta real de una cuenta de 165 B es de 1.488.440 lamports, no los 2.039.280 históricos.

## Errores encontrados y corregidos durante el desarrollo

1. Residuos de 1e-39 en la suma cero. Lo detectó Hypothesis y se resolvió con el libro a escala 1e-12.
2. Bucle de órdenes de polvo sobre posiciones residuales (36 órdenes rechazadas en la simulación).
3. Sin salida en posiciones en deterioro estructural: −93 % sin actuar.
4. Recuperación de capital bloqueada a x40 por el umbral de fracción mínima; ahora es por valor.
5. "Comprar y mantener" no era puro en la comparación (vendía por colapso).
6. Los motivos de rechazo ocultaban el filtro real tras "security:UNKNOWN".
7. Los features ocupaban ~55 MB/día y superaban el plan gratuito de Supabase; ahora hay persistencia
   condicional y retención.
8. Interpretación de `priceImpactPct` de Jupiter: es una fracción, contrastado con el endpoint Ultra.

## Limitaciones vigentes

- Sin universo histórico representativo ni backtest 2024–hoy (sesgo de supervivencia de las APIs
  gratuitas).
- Scores 100 % heurísticos y sin calibrar. No hay modelos entrenados.
- Datos por polling con límites gratuitos (~9 llamadas/min en GeckoTerminal). Sin streaming.
- Sin grafo de wallets ni ingesta de trades individuales.
- El worker no está desplegado de forma permanente (requiere aprobación y coste).
- La fiscalidad es una estimación: no incluye la permuta del lado SOL ni la regla de los 2 meses.
- El valor liquidable de posiciones usa una estimación CPMM (no se pide cotización de venta para cada
  posición en cada ciclo, para no agotar el rate limit). Para CLMM/DLMM es una aproximación.

## Decisiones que requieren al propietario

1. **Despliegue permanente** (VPS/Fly + Supabase): coste estimado de ~5 EUR/mes sin verificar, el 5 %
   de las aportaciones.
2. **Fuente histórica on-chain** para el backtest 2024–hoy (Helius / Dune / BigQuery / Flipside): coste
   por determinar.
3. ¿Mantener el ticket de 1 EUR? Con tarifas de prioridad altas no es viable (`COST_ANALYSIS.md`).

## Siguiente bloque ejecutable

Ver `ROADMAP.md` → "Siguiente bloque ejecutable".

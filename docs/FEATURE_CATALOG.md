# Catálogo de indicadores

Todos los indicadores se calculan **point-in-time** (`features.library.compute(ctx)` rechaza datos
posteriores a `as_of`). Un valor ausente lleva su motivo y no se imputa como 0. Las ventanas m5/h1/h6/h24
provienen del proveedor en el momento de la observación; los indicadores de historia propia
(volatilidad realizada, cambios de liquidez, drawdowns, ATH) se limitan al periodo observado por AEGIS
(`history_hours`).

Cautelas de interpretación:

- En mercados AMM, `trade_imbalance` y `buyer_share` miden **cuentas y direcciones**, no capital
  externo entrante demostrado.
- Las direcciones no son usuarios únicos y los grandes titulares no son necesariamente inversores
  informados.
- `top10_pct` puede incluir las cuentas del propio pool.

## Índices propietarios (baseline `heuristic-v0`)

**Son scores heurísticos en [0, 1], no probabilidades calibradas.** Las ponderaciones son un baseline
deliberadamente simple que un modelo validado debe superar (ver `MODEL_REGISTRY.md`). Si la cobertura
ponderada de componentes es < 50 %, el índice vale `None`.

| Índice | Componentes (peso) |
|---|---|
| **PRS** Peak Risk Score | drawdown desde el pico 24 h (0,30) · desaceleración de compradores (0,20) · presión vendedora m5 (0,15) · cuota vendedora h1 (0,10) · caída de liquidez h1 (0,15) · subida seguida de desaceleración (0,10) |
| **DQI** Demand Quality Index | cuota compradora h1 (0,25) · persistencia h1/h6/h24 > 0,5 (0,20) · amplitud, compradores/h (0,20) · organic score (0,20) · baja concentración de bots (0,15) |
| **MRI** Manipulation Risk Index | trades por dirección (0,30) · rotación volumen/liquidez (0,25) · organic score bajo (0,15) · volumen inorgánico (0,10) · concentración top-10 (0,20) |
| **EPI_h** Explosion Propensity (heurístico) | aceleración (0,20) · crecimiento de compradores (0,20) · momentum h1 (0,15) · crecimiento de liquidez (0,10) · DQI (0,20) · 1 − MRI (0,15) |
| **SSI** Structural Survival | retención de liquidez 24 h (0,30) · actividad (0,25) · holders (0,25) · cuota compradora 24 h (0,20) |
| **SWP_h** Second Wave (heurístico) | caída profunda desde el ATH (0,20) · rebote desde el mínimo (0,25) · reconstrucción de demanda (0,20) · aceleración (0,15) · SSI (0,20) |
| **MCI** Model Confidence | cobertura de indicadores (0,5) · frescura (0,3) · acuerdo entre fuentes (0,2) |
| **RPR** Realizable Profit Ratio | valor liquidable / valor de mercado (métrica exacta, no heurística) |
| **CES** Capital Efficiency Score | EPI_h × (1 − MRI) × MCI / (1 + coste%/10) |
| **MOI** Market Opportunity Index | 0,5 × amplitud de retornos 1 h positivos + 0,5 × mediana escalada de la cuota compradora |

Pendiente: evaluar el **valor predictivo incremental** de cada indicador (información mutua, ablación
en walk-forward) y eliminar los redundantes. Esto requiere etiquetas de resultado, que la cartera
sombra empieza a producir.

## Catálogo generado

<!-- AUTO:BEGIN -->
Feature set `fs-0.2` — 46 indicadores.

| Nombre | Grupo | Unidad | Horizonte | Fuente | Definición | Validez |
|---|---|---|---|---|---|---|
| `history_hours` | context | hours | - | own snapshots | hours since AEGIS first observed the token | first_seen known |
| `token_age_hours` | context | hours | - | chain/provider | as_of - token creation (pool creation if unknown) | creation time known |
| `avg_trade_usd_h1` | flows | USD | h1 | provider window | volume_h1/(buys+sells) | trades>0 |
| `avg_trade_usd_h24` | flows | USD | h24 | provider window | volume_h24/(buys+sells) | trades>0 |
| `avg_trade_usd_h6` | flows | USD | h6 | provider window | volume_h6/(buys+sells) | trades>0 |
| `avg_trade_usd_m5` | flows | USD | m5 | provider window | volume_m5/(buys+sells) | trades>0 |
| `buyer_growth_long` | flows | ratio-1 | h1 vs h6 | derived | buyer_rate_h1 / buyer_rate_h6 - 1 | buyer_rate_h6 > 0 |
| `buyer_growth_short` | flows | ratio-1 | m5 vs h1 | derived | buyer_rate_m5 / buyer_rate_h1 - 1 (>0: new buyers arriving faster than the hourly average) | buyer_rate_h1 > 0 |
| `buyer_rate_h1` | flows | addresses/hour | h1 | provider window | buyers_h1 / hours(h1) | buyers present |
| `buyer_rate_h24` | flows | addresses/hour | h24 | provider window | buyers_h24 / hours(h24) | buyers present |
| `buyer_rate_h6` | flows | addresses/hour | h6 | provider window | buyers_h6 / hours(h6) | buyers present |
| `buyer_rate_m5` | flows | addresses/hour | m5 | provider window | buyers_m5 / hours(m5) | buyers present |
| `buyer_share_h1` | flows | fraction | h1 | provider window | buyers/(buyers+sellers) — unique ADDRESSES, not people, over h1 | buyers+sellers>0 |
| `buyer_share_h24` | flows | fraction | h24 | provider window | buyers/(buyers+sellers) — unique ADDRESSES, not people, over h24 | buyers+sellers>0 |
| `buyer_share_h6` | flows | fraction | h6 | provider window | buyers/(buyers+sellers) — unique ADDRESSES, not people, over h6 | buyers+sellers>0 |
| `buyer_share_m5` | flows | fraction | m5 | provider window | buyers/(buyers+sellers) — unique ADDRESSES, not people, over m5 | buyers+sellers>0 |
| `trade_imbalance_h1` | flows | fraction [-1,1] | h1 | provider window | (buys - sells)/(buys + sells) by COUNT over h1. AMM swaps: not proof of external capital | buys+sells > 0 |
| `trade_imbalance_h24` | flows | fraction [-1,1] | h24 | provider window | (buys - sells)/(buys + sells) by COUNT over h24. AMM swaps: not proof of external capital | buys+sells > 0 |
| `trade_imbalance_h6` | flows | fraction [-1,1] | h6 | provider window | (buys - sells)/(buys + sells) by COUNT over h6. AMM swaps: not proof of external capital | buys+sells > 0 |
| `trade_imbalance_m5` | flows | fraction [-1,1] | m5 | provider window | (buys - sells)/(buys + sells) by COUNT over m5. AMM swaps: not proof of external capital | buys+sells > 0 |
| `trades_per_address_h24` | flows | trades/address | h24 | provider window | (buys+sells)/(buyers+sellers) — high values indicate bot concentration | traders>0 |
| `liquidity_change_h1` | liquidity | fraction | h1 | own snapshots (same pool) | L_now / L_(t-1h) - 1 | same-pool snapshot within ±30min of t-1h |
| `liquidity_change_h24` | liquidity | fraction | h24 | own snapshots (same pool) | L_now / L_(t-24h) - 1 | same-pool snapshot within ±12h of t-24h |
| `liquidity_to_fdv` | liquidity | ratio | now | provider | liquidity / FDV | fdv>0 |
| `liquidity_usd` | liquidity | USD | now | provider | pool two-sided reserve value | present |
| `volume_to_liquidity_h24` | liquidity | ratio | h24 | provider | volume_h24 / liquidity | liquidity>0 |
| `acceleration_long` | price | log-return/hour | h1 vs h6 | derived | velocity_h1 - velocity_h6 | both velocities |
| `acceleration_short` | price | log-return/hour | m5 vs h1 | derived | velocity_m5 - velocity_h1 (>0: price speeding up) | both velocities |
| `drawdown_from_peak_h24` | price | fraction | h24 | own snapshots | 1 - P_now / max(P) over the last 24h of observations | >=2 snapshots |
| `jerk` | price | log-return/hour | m5/h1/h6 | derived | acceleration_short - acceleration_long | both accelerations |
| `realized_vol_h6` | price | log-return/sqrt(hour) | h6 | own snapshots | sqrt(mean(r_i^2/dt_i)) over consecutive snapshots in 6h (irregular sampling normalised) | >=4 snapshots in window |
| `ret_h1` | price | fraction | h1 | provider window | Simple return over h1: P_t/P_(t-h1) - 1 | provider price_change present |
| `ret_h24` | price | fraction | h24 | provider window | Simple return over h24: P_t/P_(t-h24) - 1 | provider price_change present |
| `ret_h6` | price | fraction | h6 | provider window | Simple return over h6: P_t/P_(t-h6) - 1 | provider price_change present |
| `ret_m5` | price | fraction | m5 | provider window | Simple return over m5: P_t/P_(t-m5) - 1 | provider price_change present |
| `velocity_h1` | price | log-return/hour | h1 | provider window | ln(1+ret_h1) / hours(h1) — average continuous price speed | ret_w > -1 |
| `velocity_h24` | price | log-return/hour | h24 | provider window | ln(1+ret_h24) / hours(h24) — average continuous price speed | ret_w > -1 |
| `velocity_h6` | price | log-return/hour | h6 | provider window | ln(1+ret_h6) / hours(h6) — average continuous price speed | ret_w > -1 |
| `velocity_m5` | price | log-return/hour | m5 | provider window | ln(1+ret_m5) / hours(m5) — average continuous price speed | ret_w > -1 |
| `drawdown_from_ath` | survival | fraction | since first seen | token state | 1 - P_now / ATH observed by AEGIS (not the true ATH if the token predates observation) | ATH known |
| `hours_since_ath` | survival | hours | since ATH | token state | (as_of - ath_at) in hours | ATH time known |
| `multiple_from_low_since_ath` | survival | ratio | since ATH | token state | P_now / min(P) observed after the ATH | low known |
| `holders_count` | wallets | addresses | now | GeckoTerminal/Jupiter | number of holder addresses | profile |
| `organic_score` | wallets | 0-100 | now | Jupiter | Jupiter organic activity score (proprietary, unvalidated) | profile |
| `organic_volume_share_h24` | wallets | fraction | h24 | Jupiter stats24h | buyOrganicVolume / buyVolume (Jupiter proprietary organic classifier) | profile with stats24h |
| `top10_pct` | wallets | percent | now | GeckoTerminal/Jupiter | supply % held by top 10 addresses (may include pools) | profile |
<!-- AUTO:END -->

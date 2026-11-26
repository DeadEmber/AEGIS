# Especificación de la estrategia (baseline `aegis-baseline` 0.2.0)

Todos los parámetros están en `StrategyConfig` (`src/aegis/config.py`), se versionan por hash en
`configuration_versions` y cada decisión registra el hash usado. **Son experimentales**: deben
evaluarse por backtesting y no codificarse como verdades.

## 1. Función objetivo

```
Objetivo = Patrimonio neto liquidable + Capital retirado − Aportaciones externas

Patrimonio neto liquidable =
    caja (inversión + reciclado + reserva de beneficios + provisión fiscal)
  + depósitos de renta recuperables
  + Σ valor liquidable de posiciones (venta total hoy, tras impacto y comisiones de salida)
  − impuestos estimados devengados no pagados
```

- El valor **liquidable** sustituye al valor de mercado (`qty × precio`). Ambos se registran y el
  cociente es el **RPR** (Realizable Profit Ratio).
- Las posiciones con datos de más de `stale_after_minutes` (30) se valoran con un descuento del 50 %
  y se marcan `stale`. Con más de 48 h se tratan como **no valoradas** (cuentan 0) y se reportan
  aparte. No se usa nunca el último precio sin descuento.
- Los costes tecnológicos pueden registrarse con `PortfolioService.book_technology_cost` y reducen el
  objetivo.

## 2. Compartimentos de capital

| Compartimento | Cuenta | Se usa para comprar | Origen |
|---|---|---|---|
| 1. Inversión | `cash:investment` | sí | aportaciones externas |
| 2. Reciclado | `cash:recycled` | sí (prioritario) | importes netos de ventas y depósitos recuperados |
| 3a. Reserva de beneficios | `cash:reserve:profit` | **no** | 10 % de cada ganancia realizada (política) |
| 3b. Provisión fiscal | `cash:reserve:tax` | **no** (tampoco retirable) | se ajusta en cada venta al impuesto estimado no pagado |

El capital reciclado **no** se suma a las aportaciones. La identidad contable
`aportado − retirado + P&L realizado − gastos = caja + posiciones al coste + depósitos` se verifica
en cada ciclo.

## 3. Presupuesto y ritmo

- Aportación: 100 EUR el primer ciclo de cada mes natural (UTC). Es idempotente por periodo y **no**
  rellena meses no observados.
- El presupuesto mensual es un **tope**. Tope diario de inversión =
  `restante_mes / días_restantes × 1,5 × multiplicador_régimen`.
- El reciclado se reinvierte como máximo un 10 % diario de su saldo.
- El multiplicador de régimen depende del MOI: 0,5 si MOI < 0,25 y 1,25 si MOI > 0,65.
- Huecos de ticket por ciclo: `floor(disponible / ticket)`, limitado por `RiskLimits.max_buys_per_cycle` (5).
- Reparto por bucket: exploración 40 %, selección 45 % y segundas oportunidades 15 %, con el método de
  mayor resto. Los huecos sin candidatos pasan primero a exploración, para que la selección no
  elimine la diversificación.

## 4. Ticket

- Ticket **todo incluido** de 1 EUR: depósito de renta, comisión base, prioridad y entrada al swap
  suman exactamente 1 EUR.
- El ticket se rechaza si los costes fijos superan su importe, si el coste estimado de ida y vuelta
  supera el 15 %, si el impacto supera el 3 % o si los datos tienen más de 30 minutos.

## 5. Elegibilidad (filtros experimentales, explicables)

| Filtro | Valor | Nota |
|---|---|---|
| Precio | ≤ 0,001 EUR | criterio experimental, **no** de seguridad ni de potencial |
| Capitalización circulante | 25.000 – 5.000.000 EUR | si falta, se aplica **explícitamente** un rango FDV de 25.000 – 10.000.000 EUR con la etiqueta `cap_unknown_used_fdv` |
| Liquidez del pool | ≥ 10.000 EUR | dos lados del pool |
| Volumen 24 h | ≥ 5.000 EUR | |
| Antigüedad del token | ≥ 24 h | creación del token si se conoce; si no, la del pool, con etiqueta |
| Transacciones 24 h | ≥ 100 | |
| Direcciones activas 24 h | ≥ 50 | direcciones, no personas |
| Tipo de pool | AMM/CLMM | bonding curves excluidas por defecto |

## 6. Seguridad (bloqueante)

Bloquean la compra: autoridad de mint o freeze presente (on-chain), extensiones Token-2022 peligrosas
(permanent delegate, non-transferable, pausable, transfer hook activo, transfer fee > 0, cuentas
congeladas por defecto), ausencia de ruta de venta ejecutable, pérdida de ida y vuelta > 15 %,
concentración del top-10 > 50 %, creador con más del 20 %, liquidez < 2.000 EUR y **cualquier
comprobación crítica no verificable**. Los indicios (bajo volumen orgánico, rotación > 50x, más de
15 trades por dirección, creador en serie) marcan RISK, pero no bloquean.

## 7. Selección de entradas

1. Exploración: muestra **uniforme aleatoria con semilla** entre tokens elegibles y seguros. Preserva
   el descubrimiento de oportunidades que los modelos no identifican y sirve de grupo de control.
2. Selección: ranking por CES = `EPI_h × (1 − MRI) × MCI / (1 + coste%/10)`. Requiere EPI_h ≥ 0,40
   con seguridad PASS, o EPI_h ≥ 0,55 y MRI < 0,5.
3. Segundas oportunidades (REENTRY): token ya poseído, ≥ 24 h desde la salida, caída desde el ATH
   ≥ 80 %, rebote ≥ 1,5x desde el mínimo, cuota de compradores 1 h ≥ 0,55, liquidez ≥ 30 % de la de
   salida, menos de 3 entradas previas y estado RECOVERY/SECOND_WAVE/EXPANSION/ACCUMULATION. Haber
   ganado antes **no** aumenta el tamaño.

## 8. Salidas (sin techo fijo)

En orden de prioridad:

1. Veredicto de seguridad BLOCK sobre un token en cartera → venta total si es vendible.
2. Liquidez < 1.000 EUR o UNTRADABLE → venta total si es vendible. Si no, se espera. Tras 7 días
   muerta → **quemar y cerrar** la cuenta para recuperar el depósito de renta (≈0,095 EUR, frente a
   ≈0,0003 EUR de comisión).
3. Deterioro con pérdida (COLLAPSE, o liquidez 24 h ≤ −50 % con compradores < 45 %) → venta total
   para recuperar lo que quede.
4. Recuperación de capital con multiplicador neto ≥ 2x → vender lo justo (+3 %) para recuperar lo
   invertido; el resto queda libre.
5. Trailing por volatilidad para ganadores ≥ 3x: ancho = clip(3 × σ6h, 25 %, 60 %) → vender hasta el
   residual (10 % de la cantidad original).
6. PRS ≥ 0,85 → vender hasta el residual. PRS ≥ 0,55 en estado caliente → vender 33 %, como máximo
   una vez cada 30 minutos.
7. El **residual no se negocia** salvo por evento crítico (es la exposición deliberada a la cola).

Ningún stop garantiza precio: cada salida se vuelve a cotizar (Jupiter) y paga sus costes. Si el
importe neto es < 0,10 EUR, se rechaza.

## 9. KPIs (definiciones)

| KPI | Definición | Unidad | Estado |
|---|---|---|---|
| Net Portfolio Return | (NW + W − C) / C | fracción | ✅ API `/overview` |
| TWR | Π V_i/(V_{i−1}+F_i) − 1, flujo al inicio del periodo | fracción | ✅ |
| XIRR | TIR con flujos fechados (aportaciones −, retiradas +, NW final +) | anual | ✅ (≥ 30 días) |
| Maximum Drawdown | sobre el índice TWR | fracción | ✅ |
| Cost-to-Capital | costes explícitos / aportaciones | fracción | ✅ |
| Realizable Profit Ratio | valor liquidable / valor de mercado | ratio | ✅ por posición |
| Capital Recycling Ratio | capital reciclado / aportaciones | ratio | 🟡 dato disponible (`capital_recycled_total_eur`) |
| Execution Success Rate | órdenes FILLED / órdenes | fracción | 🟡 dato disponible (`counts`) |
| Peak Capture Ratio | Σ importes de venta / Σ (unidades vendidas × máximo ejecutable posterior en ≤ 48 h) | fracción | ⏳ requiere máximo ejecutable |
| Early Exit Opportunity Cost | pico de la cohorte sombra `after_exit` frente a la venta | EUR | 🟡 datos en `shadow_positions` |
| Missed Opportunity Rate | % de la cohorte `not_selected` que alcanza x2 o x10 | fracción | 🟡 API `/shadow` |
| Fraud Avoidance Rate | % de los BLOCK que colapsan o resultan invendibles | fracción | ⏳ requiere etiquetado |
| Top Winner Dependency | P&L sin los k mejores / P&L total | ratio | ⏳ |
| Effective Diversification | 1 / Σ w_i² por coste | nº | ⏳ |
| Data Coverage Ratio | indicadores presentes / registrados | fracción | ✅ `MCI.coverage` |

Los indicadores retrospectivos (máximos posteriores, etiquetas) nunca son variables en tiempo real.

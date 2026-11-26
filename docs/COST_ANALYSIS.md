# Análisis de costes y viabilidad

Datos medidos el 2026-11-26 con fuentes reales. SOL ≈ 64,2–64,5 EUR (CoinGecko) y EUR/USD = 1,0935 (BCE).

## 1. Coste de un ticket de 1 EUR (medido)

| Componente | Lamports | EUR | % del ticket | ¿Recuperable? |
|---|---|---|---|---|
| Depósito de renta, cuenta SPL (165 B) | 1.488.440 | 0,0955 | 9,6 % | sí, al cerrar la cuenta |
| Depósito de renta, cuenta Token-2022 (170 B) | ≈1.513.800 | 0,0976 | 9,8 % | sí |
| Comisión base (1 firma) | 5.000 | 0,0003 | 0,03 % | no |
| Prioridad (supuesto por defecto) | 100.000 | 0,0064 | 0,64 % | no |
| Comisión de pool (embebida en la cotización) | — | ~0,25 % del swap | — | no |
| Comisión de agregador | 0 (lite) / 10 bps (Ultra) | — | 0–0,1 % | no |
| Impacto observado (4 compras reales simuladas, 0,9 EUR) | — | — | 0,03 % – 2,36 % | no |

El coste de ida y vuelta no recuperable estimado por el motor en las 4 compras simuladas reales fue
del 3,7 % al 7,9 % del ticket. Incluye el supuesto conservador de un 1 % de deslizamiento adverso por
lado frente a la cotización.

Además, nada más comprar, el patrimonio liquidable de esas 4 posiciones era de 3,51 EUR en posiciones
más 0,39 EUR en depósitos, frente a 4,00 EUR invertidos: **un 2,5 % se pierde solo por entrar y valorar
a precio de salida**.

### Sensibilidad a la tarifa de prioridad

| Prioridad por tx | EUR por lado | Coste fijo de ida y vuelta (1 EUR) | Coste fijo de ida y vuelta (5 EUR) |
|---|---|---|---|
| 100.000 lamports | 0,0064 | 1,4 % | 0,3 % |
| 1.000.000 lamports | 0,064 | 12,9 % | 2,6 % |
| 10.000.000 lamports | 0,64 | > 100 % (inviable) | 25,8 % |

**Conclusión.** El ticket de 1 EUR solo es viable si las tarifas de prioridad se mantienen bajas. En
congestión el motor rechaza la operación (`roundtrip_cost_*_exceeds_limit`, prueba
`test_costs_above_microposition_rejected`). El tamaño de 1 EUR **debe** compararse con 2 y 5 EUR en el
laboratorio. El ticket de 0,50 EUR solo deja ~0,39 EUR para el swap tras el depósito, por lo que es
estructuralmente desfavorable.

### Depósitos inmovilizados

Con 100 tickets/mes se inmovilizan ~9,7 EUR/mes en depósitos mientras las posiciones sigan abiertas,
incluidos los residuales. Las posiciones muertas se cierran con quema y cierre de cuenta, que recupera
~0,095 EUR por ~0,0003 EUR. Sin esa función, una cartera de 1.000 posiciones muertas inmovilizaría
~95 EUR.

## 2. Coste de datos (planes gratuitos verificados)

| Fuente | Límite observado o documentado | Uso por ciclo de 5 min (por defecto) |
|---|---|---|
| GeckoTerminal | 429 tras ~7 llamadas en 17 s; espaciado de 6,5 s (~9/min) | 2 new_pools + 1 trending + ⌈refrescos/30⌉ multi |
| Jupiter lite | espaciado de 1,1 s | 2 cotizaciones por chequeo de seguridad + 1 por orden + 1 lote de tokens v2 |
| RPC público de Solana | 429 por método (`getTokenLargestAccounts`) | 1 `getMultipleAccounts` por lote de ≤ 100 mints |
| DexScreener | 300/min (pares) | no usado en el ciclo por defecto (control cruzado) |
| BCE / CoinGecko | caché de 1 h / 60 s | 1 + 1 |

**Capacidad.** Refrescar 300 pools por ciclo cuesta 10 llamadas (~65 s). Monitorizar 10.000 tokens con
frecuencia horaria exige ~334 llamadas/hora (5,6/min): es factible en el plan gratuito, pero sin
margen. Para frecuencias de minutos sobre miles de tokens hace falta streaming (Helius/Yellowstone) o
el plan de pago de CoinGecko onchain. **No se ha contratado nada**. Los precios vigentes deben
consultarse y aprobarse por el propietario.

## 3. Almacenamiento (medido)

| Tabla | Bytes por fila (SQLite) |
|---|---|
| features | ~2.100 |
| market_snapshots | ~420 |
| decisions | ~380 |

Sin control, unos 65 tokens cada 5 minutos generaban ~55 MB/día (~1,6 GB/mes), que superan el plan
gratuito de Supabase (500 MB) en unos 9 días. Se han aplicado dos mitigaciones:

- Los features solo se persisten si el token está en cartera, es elegible, cambia de estado o ha
  pasado una hora.
- `aegis prune` deja los snapshots horarios a partir de 14 días, borra los features de más de 30 días
  y las decisiones triviales sin orden de más de 14 días. **Nunca** toca el libro, las órdenes, las
  ejecuciones ni los lotes.

## 4. Infraestructura

| Componente | Opción de coste mínimo | Nota |
|---|---|---|
| Worker | VPS pequeño o Fly.io/Railway (~5 EUR/mes, sin verificar) | Vercel **no** sirve para el worker continuo |
| Base de datos | Supabase free (500 MB) o PostgreSQL en el mismo VPS | con retención activa |
| Dashboard | Vercel Hobby | solo lectura vía API |

Un coste de 5 EUR/mes equivale al **5 % de las aportaciones** y debe registrarse en el objetivo
(`book_technology_cost`). Es un coste material frente a 100 EUR/mes.

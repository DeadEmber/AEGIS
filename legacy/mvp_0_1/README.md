# AEGIS 0.1 — laboratorio de paper trading (Solana)

**No opera dinero real. No contiene wallet, claves ni envío de órdenes.**

## Requisitos
Python 3.10+ y conexión a Internet para modo de mercado. Sin dependencias externas.

## Windows (PowerShell)
Descomprime el ZIP, abre PowerShell dentro de la carpeta.

```powershell
py -3 aegis.py demo
py -3 aegis.py status --db demo.sqlite
```
La demo utiliza un único token ficticio y seis observaciones predefinidas. Se espera: 100 EUR aportados, dos compras (incluida una reentrada), una venta, subtotal aproximado 110,41 EUR. No es un backtest ni evidencia de rentabilidad. Para repetir, elige un archivo nuevo: `py -3 aegis.py demo --db demo2.sqlite --out demo2_exports`.

## Mercado real, operaciones ficticias
En los ejemplos siguientes 1.10 es un **tipo de cambio de ejemplo**, no una cotización actual. Sustitúyelo por USD por EUR vigente y actualízalo periódicamente. El valor se guarda en la base de datos para consultas. No uses la demo como base de mercado.

```powershell
py -3 aegis.py once --eurusd 1.10
py -3 aegis.py run --eurusd 1.10 --interval 300
```
Ctrl+C detiene el proceso. No se pierde el estado de ciclos completados. Para consultar desde otra ventana:

```powershell
py -3 aegis.py status
py -3 aegis.py export --out exports_live
```
La base `paper.sqlite` permanece en la carpeta. No eliminar si quieres conservar las operaciones.

## Qué hace
- Descubre pools recientes de Solana mediante GeckoTerminal y deduplica por mint.
- Guarda observaciones incluso cuando no compra. Actualiza hasta 200 pools por ciclo (prioriza posiciones abiertas y observaciones antiguas).
- Aporta 100 EUR virtuales por mes observado, sin rellenar meses anteriores. Compra posiciones de 1 EUR incluyendo una comisión fija estimada; hasta 10 compras por ciclo. Reinvierte cobros y reserva el 10% de ganancias positivas realizadas como política experimental (NO es una provisión fiscal).
- Aplica filtros experimentales de precio, FDV, liquidez, volumen, edad y operaciones; no certifica seguridad. FDV no equivale a capitalización circulante.
- Estima impacto de precio con un modelo simplificado de pool de producto constante. Aplica reglas transparentes de deterioro, salida parcial y salida crítica, sin techo fijo de precio. Permite una reentrada bajo condiciones experimentales tras venta total y caída desde máximos.
- No presume que un token desapareció cuando no llegan datos. Las posiciones con datos de más de 30 minutos no se incluyen en el subtotal, y se cuentan como no valoradas. No interpreta el subtotal como patrimonio completo si hay posiciones desactualizadas.
- Exporta CSV de observaciones, operaciones/decisiones, caja, posiciones y aportaciones.

## Limitaciones importantes
- Las reglas son heurísticas no entrenadas, no probabilidades. No detectan máximos con precisión demostrada. Sin modelo de fraude, compradores únicos, flujos por wallet, calibración, ni shadow trading de candidatos rechazados (solo se almacenan observaciones).
- Conexión HTTP real no probada en el entorno de creación (sin acceso DNS). Verifica el modo `once` antes del modo continuo. Errores de API abortan el ciclo y no producen operaciones ficticias. Si cambia el esquema o cuota, habrá que adaptar el conector.
- GeckoTerminal no representa todos los tokens ni todos los mercados; nuevos pools no equivalen a tokens recién creados. El muestreo no es exhaustivo. El filtro de 24 horas depende de cuándo se creó el pool observado.
- Las compras y ventas virtuales usan snapshots, no cotizaciones ejecutables de Jupiter; no incluyen todos los costes, depósito de cuentas, MEV, latencia, fiscalidad, impacto multipool ni comisiones reales. El tipo de cambio es manual. No hay prueba histórica desde 2024.
- Una cola de 200 pools y el intervalo de 5 min son un límite de esta versión; no sirven para monitorizar miles de posiciones en segundos. El escalado requiere streaming de eventos y cotizaciones verificables, y el coste debe incluirse en el análisis.
- Sin interfaz gráfica en esta versión. Sin automatización permanente del sistema operativo; el proceso funciona mientras esté abierto y haya conexión.
- No usar este prototipo para enviar órdenes reales. Siguiente fase: verificar cotizaciones ejecutables y seguridad on-chain, recopilar histórico de tokens desaparecidos, comparar baselines, entrenar y calibrar modelos, validar fuera de muestra y operar en sombra.

## Fuentes técnicas
- https://api.geckoterminal.com/docs/index.html
- https://developers.jup.ag/

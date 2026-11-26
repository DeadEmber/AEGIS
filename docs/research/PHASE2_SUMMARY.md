# Fase 2: conclusiones (resumen interpretado)

Las cifras completas, con las revisiones de los datos y las huellas de configuración, están en
`PHASE2_RESULTS.md` y `results/E*.json`. Se regeneran con `aegis research phase2`.

Condiciones comunes:

- Tickets de 1 EUR y 100 EUR de aportación al mes.
- Comisión de PumpSwap medida por tramos.
- 1 % de deslizamiento adverso por lado.
- Comisiones de red de Solana.
- Depósito de renta recuperable.
- Ejecución a través del motor y el libro de producción.

**Periodos ya explorados por el propietario: ninguna cifra es evidencia independiente.**

## 1. Respuesta corta

Con costes realistas, **ninguna política probada supera a no invertir** en el periodo explorado:

- Pumpfun Corpus del 19/06 al 15/07/2026.
- Ventana walk-forward del 26/06 al 15/07.
- Launch Tape del 10/10/2026.

| Política (ventana walk-forward, 200 EUR aportados) | Patrimonio liquidable | IC 90 % del objetivo (bootstrap por bloques) |
|---|---|---|
| Efectivo (la cartera D no encuentra ninguna entrada) | 200,00 | — |
| **Champion**: estricta + trailing 30 %, asignación de producción | **177,93** | −49,0 … +8,5 EUR (P(objetivo ≤ 0) = 0,89) |
| B: aleatoria tras controles básicos de seguridad | 176,31 | −34,5 … −12,9 |
| A: aleatoria entre negociables | 168,00 | −46,0 … −18,5 |
| C: selección económica + seguridad | 164,17 | −61,4 … −11,7 |
| G: selección supervisada + agente | 163,32 | −64,3 … −15,0 |
| E: selección supervisada | 157,93 | −67,9 … −20,9 |
| F: supervisada + monetización contextual | 147,95 | −80,0 … −30,4 |
| H: agente + capitalización compuesta | 94,23 | −183 … −41 |
| I: agente + compuesto + segundas oportunidades | 49,79 | −258 … −61 |

- Tras Holm–Bonferroni sobre una familia de 50 variantes, **ninguna diferencia frente al Champion es
  significativa**.
- La diferencia entre el Champion y la selección aleatoria tras controles básicos (B) no se distingue
  de cero (IC de la diferencia: −39 … +30 EUR).

## 2. Lo que sí se ha aprendido

1. **Los datos son fiables y point-in-time.** Los precios reconstruidos coinciden con los `capAt` de
   Launch Tape (mediana del cociente: 1,000). Las ventanas usan solo trades ya impresos, los resultados
   retrospectivos están aislados y los huecos están documentados, incluido uno no publicado: el 12 de
   julio no hay ninguna graduación.
2. **El coste dominante no es la comisión, sino la dirección.**
   - La comisión real de PumpSwap es de ~1,0–1,26 % por lado, 3–4 veces el supuesto anterior. Aun así,
     bajarla a 30 pb solo recupera ~5 EUR (E9).
   - Entre +60 s y +600 s tras graduar, el precio mediano cae un 16 % (media −29 %).
   - La deriva media con 60 s de retraso es de −866 pb.
3. **Selección (E6).** Solo dos filtros estrictos importan: la liquidez ≥ 20.000 EUR y la ratio
   compras/ventas 5 min ≥ 1,75. Quitar cualquiera de los dos hunde el resultado (55 y 94 EUR frente a
   144). El rango de capitalización, el volumen de 5 min y el cambio de precio son redundantes.
4. **Contexto de mercado (E6).** El recuento bruto «≥ N oportunidades en 24 h» tiene una correlación de
   0,83 con la cobertura diaria del dataset: está contaminado, como sospechaba el propietario. Su
   aparente mejora procede de operar menos (≥ 40: 72 tickets) y no de mejores tickets, que siguen en
   −4,6 % de media. La versión normalizada por cobertura no aporta.
5. **Liquidez y compuesto (E5).** Con expectativa negativa, reciclar más significa perder más:
   - sin reciclaje: 190,6;
   - reciclaje limitado: 169,4;
   - reinversión completa: 144–152.

   La reinversión completa reduce la latencia de reinversión de ~170 h a ~21 h, pero eso solo es
   valioso con ventaja positiva. **La rotación es un resultado, no un objetivo**: los datos lo confirman.
6. **Salidas (E10, dentro de muestra).**
   - Mejoran frente al trailing 30 %: el trailing 20 % (165) y un stop a ×0,7 (178).
   - Empeoran: el trailing 45 % (89), activar el trailing a ×1,5 (96) y conservar residuales
     (recuperación a ×2 con un 10 % de residual: 104; a ×1,5 con un 25 %: 69).
   - Casi todos los tokens mueren antes de 48 h, así que **el residual vale 0** y deja el depósito
     inmovilizado.
7. **El motor de salidas de producción (decision-0.2) no sirve para tokens recién graduados** (E4: 65
   frente a 144). La volatilidad normal posterior a la graduación dispara COLLAPSE, la salida por
   deterioro y los recortes PRS, que están calibrados para tokens maduros (≥ 24 h). La política de
   producción no opera ese universo (exige ≥ 24 h), así que no hay riesgo operativo; pero no debe
   aplicarse a él sin rediseño.
8. **Un buen AUC no implica beneficio (E7, E8).**
   - El modelo de continuación a 30 min logra un AUC de 0,60–0,69 y un ECE de 0,03–0,06 fuera de muestra,
     pero el agente pierde frente al Champion en todas sus variantes (79–137 frente a 155): mantiene
     demasiado y deja desinflarse a los ganadores.
   - El modelo de entrada no supera el azar (AUC 0,48–0,56; ECE 0,08–0,21).
   - Ninguno es una probabilidad calibrada ni se promueve.
9. **Tamaño de ticket (E9).**
   - El retorno medio por ticket mejora con el tamaño (0,5 EUR: −15,8 %; 1 EUR: −9,2 %; 2 EUR: −6,8 %),
     porque los costes fijos y el depósito pesan menos.
   - La pérdida absoluta crece con el capital desplegado.
   - 5 EUR agota el efectivo con solo 198 compras.
   - No hay base para cambiar el ticket de referencia de 1 EUR.
10. **Frecuencia y latencia (Launch Tape).** Pasar de 5 a 60 s de cadencia, o de 2 a 30 s de latencia,
    cambia el resultado en menos de 1 EUR sobre 21 tickets. La pérdida no viene de la ejecución.

## 3. Por qué no cuadran las cifras del propietario

En el corpus, las cifras de AEGIS son menores que las del estudio externo:

| Estrategia | AEGIS | Estudio externo |
|---|---|---|
| Filtros básicos | 47,8 | 122,8 |
| Estricta + trailing | 144,3 | 198,9 |
| Estricta + contexto | 166,6 | 210,7 |

Las comisiones no lo explican (sensibilidad: ~5 EUR). Las causas plausibles son:

- (a) Asignación: sin reciclaje y con ritmo diario el Champion llega a 190,6 (E5).
- (b) Valorar las posiciones abiertas a precio de marca en lugar de a valor liquidable. Valoradas a la
  última observación, la estricta con hold pasa de 18,7 a 96,3 EUR.
- (c) Definiciones distintas de liquidez, flujo y momento de entrada.

En Launch Tape, el +14,64 % sobre 7 compras no es estadísticamente distinguible de cero con esa
muestra. Con las definiciones de AEGIS, la selección estricta compra 21 tokens y pierde (−48 % medio por
ticket), en una ventana en la que el precio mediano cae un 16 % en 10 minutos. Para conciliar las cifras
exactamente hace falta el script del estudio externo.

## 4. Qué no se ha hecho y por qué

- **Bloque F (RL offline, supervisor LLM): no iniciado.** El mandato lo condiciona a que los bloques
  anteriores muestren potencial suficiente, y no lo muestran.
- **Ninguna muestra histórica independiente**: los dos datasets gratuitos ya estaban explorados. Los
  días completos de Launch Tape son de pago (10 USD/día; historial completo a 49 USD/mes). No se han
  comprado: requiere la decisión del propietario.
- **El universo de la política de producción** (tokens de más de 24 h con filtros de madurez) **no
  puede evaluarse con estos datasets**: el corpus deja de seguir cada token a las 48 h y no tiene
  recuentos de 24 h. Sigue siendo la principal laguna de evidencia.

## 5. Preregistro para la muestra nueva (paper trading permanente)

Para que la próxima evidencia sea independiente, se fijan **antes** de verla:

- **Champion de investigación**: `strict+trailing` (liquidez ≥ 20.000 EUR, compras/ventas 5 min ≥
  1,75, entrada entre 30 s y 1 h tras graduar, trailing del 30 % activado a ×1 neto), con la asignación
  de producción (ritmo diario y reciclado del 10 %/día).
- **Challengers preregistrados** (únicos):
  - (1) trailing 20 % + stop ×0,7;
  - (2) el mismo con «sin reciclaje».
- **Criterio de promoción**: patrimonio liquidable mayor que el del Champion **y** que el efectivo
  durante ≥ 30 días de datos nuevos, con IC 90 % > 0 en el bootstrap por bloques, y revisión humana.
  Mientras tanto, la política operativa no cambia.

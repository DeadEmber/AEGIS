# Registro de modelos

| Nombre | Versión | Tipo | Estado | ¿Probabilidad calibrada? | Uso |
|---|---|---|---|---|---|
| `heuristic` (PRS, DQI, MRI, EPI_h, SSI, SWP_h, MCI) | `heuristic-v0` | heurístico | **operativo (baseline)** | **no** | scores de decisión en paper trading |
| `security` | `security-0.2` | reglas | operativo | n/a | bloqueo y riesgo |
| `state` | `state-0.2` | reglas | operativo | n/a | estados de mercado |
| `decision` | `decision-0.2` | reglas deterministas | operativo | n/a | acciones |

### Fase 2: modelos experimentales (investigación y modo sombra; **ninguno operativo**)

| Nombre | Versión | Tipo | Datos (revisión) | Etiqueta | Estado | ¿Probabilidad calibrada? |
|---|---|---|---|---|---|---|
| `entry-logreg` | `logreg-l2-0.1`, conjunto bootstrap de 5 por token | regresión logística L2 sobre 25 atributos point-in-time (`research.learning.ENTRY_FEATURES`) | `pumpfun_corpus@13bec2bf`, 19/06–15/07/2026 | ticket de 1 EUR con la salida Champion (trailing 30 %) y costes completos: múltiplo de caja > 1 | **challenger en investigación** | **no** (ver E7: ECE y fiabilidad por pliegue) |
| `continuation-logreg-h1h` | `logreg-l2-0.1`, conjunto bootstrap de 5 | regresión logística sobre 13 atributos del estado de la posición (`POSITION_FEATURES`) | igual | valor liquidable a +1 h > valor actual (horizonte ≥ resolución de los datos) | **challenger en investigación** (agente de decisión, E8) | **no** |
| `continuation-logreg-h30m` | igual | igual | igual | igual a +30 min | investigación | no |

Se entrenan walk-forward con pliegues de 4 días, ventana expansiva desde 7 días y embargo de 48 h, que
es igual al seguimiento máximo de la etiqueta (`research.validation`). Las observaciones del mismo token
nunca cruzan entrenamiento y prueba, porque se reparten por instante de decisión y se purgan las
etiquetas solapadas. Sus salidas se llaman `p_model` y **no se presentan como probabilidad**. Los
resultados por pliegue (AUC, Brier, log loss, ECE, fiabilidad) y la evaluación económica están en
`docs/research/PHASE2_RESULTS.md`. Ninguno cumple todavía los criterios de promoción de abajo.
La tabla `model_versions` de la base operativa sigue vacía: ningún camino de producción carga estos
modelos.

## Proceso Champion–Challenger (obligatorio para sustituir el baseline)

Un challenger solo puede pasar a champion si cumple **todo** lo siguiente:

1. Entrenado y evaluado con **walk-forward** sobre datos point-in-time sin sesgo de supervivencia
   conocido, con embargo ≥ horizonte de la etiqueta.
2. Supera al champion **fuera de muestra** en métricas de eventos raros: PR-AUC, Precision@K, lift en
   el decil superior, Brier y log loss. También en la **métrica económica**: objetivo neto en
   simulación con costes completos.
3. La mejora es estable en al menos 3 ventanas consecutivas y en un bootstrap por bloques (IC 90 % > 0).
4. La curva de calibración está dentro de tolerancia (ECE < 0,05). Solo entonces puede mostrarse como
   probabilidad.
5. Los costes de inferencia y de datos se han medido y aprobado.
6. Revisión humana: un modelo con mejor rendimiento en un periodo corto **no** se promueve
   automáticamente.

Todo modelo registra: `params`, `data_hash` (hash del dataset), versión del feature set, métricas por
ventana y fecha de validación. Las predicciones se guardan en `predictions` con `model_version_id`,
para que cualquier decisión pueda reproducirse.

## Modelos previstos (en orden)

1. Regresión logística y LightGBM para x2/x5/x10 en 24 h, 72 h y 7 días, con etiquetado de **triple
   barrera** (TP/SL/tiempo) en varias combinaciones.
2. Supervivencia (Cox / gradient-boosted survival) para el tiempo hasta el colapso (Peak Engine) y el
   tiempo hasta la segunda ola.
3. Vecinos próximos sobre trayectorias normalizadas, para el pattern matching histórico con
   conteos e incertidumbre.
4. Isolation Forest para anomalías y manipulación.
5. **Objetivos ampliados por la eficiencia temporal del capital** (los mismos modelos, sin duplicados):
   - LightGBM con pérdida quantile: P10/P50/P90 del valor liquidable futuro y de la duración de la
     inversión.
   - Los modelos de supervivencia anteriores, aplicados también al tiempo hasta recuperar el capital,
     con las etiquetas censuradas de `research/temporal.py`.

   Sus salidas alimentarían el ECV (`decision/ecv.py`, `source="model"`). Se guardan en `predictions`
   con `unit`, `lower`, `upper`, `interval_level` y `status="model"`. Solo se usan tras superar el
   proceso Champion–Challenger y el experimento E1.

Bloqueo de modelos no validados (caso 27): el motor de decisiones solo consume `scoring.heuristics`.
Ningún camino de código carga un modelo con `status != champion`, y hoy no existe ninguno.

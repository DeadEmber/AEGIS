# Registro de modelos

| Nombre | Versión | Tipo | Estado | ¿Probabilidad calibrada? | Uso |
|---|---|---|---|---|---|
| `heuristic` (PRS, DQI, MRI, EPI_h, SSI, SWP_h, MCI) | `heuristic-v0` | heurístico | **operativo (baseline)** | **no** | scores de decisión en paper trading |
| `security` | `security-0.2` | reglas | operativo | n/a | bloqueo y riesgo |
| `state` | `state-0.2` | reglas | operativo | n/a | estados de mercado |
| `decision` | `decision-0.2` | reglas deterministas | operativo | n/a | acciones |

No hay modelos entrenados. La tabla `model_versions` está preparada.

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

Bloqueo de modelos no validados (caso 27): el motor de decisiones solo consume `scoring.heuristics`.
Ningún camino de código carga un modelo con `status != champion`, y hoy no existe ninguno.

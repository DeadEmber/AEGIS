# Despliegue

> No se ha desplegado ninguna infraestructura con coste. Lo que sigue son instrucciones; el propietario
> decide y aprueba.

## Local

```bash
.venv/bin/aegis db upgrade
```

```bash
.venv/bin/aegis run --interval 300
```

```bash
.venv/bin/aegis api --port 8000
```

## Docker (worker + API + PostgreSQL)

```bash
docker compose up -d --build
```

`docker-compose.yml` levanta `db` (PostgreSQL 16), `migrate` (Alembic, una vez), `worker`
(`aegis run`) y `api` (`aegis api --host 0.0.0.0`). Las variables se leen de `.env`.

## Simulación permanente (recomendado)

- **Worker**: un proceso continuo en un VPS o un servicio con proceso persistente (Fly.io, Railway,
  Render background worker). **Vercel no sirve** para el worker: sus funciones tienen tiempo de
  ejecución limitado.
- **Base de datos**: Supabase o PostgreSQL gestionado. `AEGIS_DATABASE_URL=postgresql://...`. Ejecuta
  `aegis db upgrade` en cada despliegue.
- **Archivo y retención**: programa `aegis archive` y después `aegis prune` a diario (cron del VPS o job
  programado). `prune` solo borra lo que ya está en un lote verificado. `AEGIS_ARCHIVE_DIR` debe apuntar a
  un **volumen persistente**: es el dataset de investigación y no se regenera.
- **API**: el mismo contenedor con `aegis api`, detrás de HTTPS. Con `AEGIS_API_CORS_ORIGINS` apuntando
  al dominio del dashboard.
- **Dashboard**: Vercel, con `NEXT_PUBLIC_AEGIS_API=https://<api>`.
- **Secretos**: en el gestor de la plataforma, nunca en el repositorio.

## Puesta en marcha del paper trading permanente (checklist)

1. Servidor con proceso persistente (VPS, Fly, Railway…): **requiere aprobación del coste por el propietario**.
2. `git clone`, `python -m venv .venv`, `.venv/bin/pip install -e ".[postgres]"`, `cp .env.example .env`.
3. En `.env`:
   - `AEGIS_DATABASE_URL` (PostgreSQL o SQLite en disco persistente).
   - `AEGIS_ARCHIVE_DIR`, en un volumen persistente.
   - `AEGIS_BACKUP_DIR`, en **otro** disco o volumen.
   - `AEGIS_API_CONTROL_TOKEN`.
4. `aegis db upgrade` y `aegis probe` (6/6 fuentes OK).
5. Instalar las unidades de `deploy/systemd/`:
   - `aegis-worker.service` (bucle con vía rápida).
   - `aegis-api.service`.
   - `aegis-maintenance.timer`: diario, ejecuta archivo → verificación → backup → poda, y nunca poda ante cualquier duda.

   Con Docker: `docker compose up -d` y `docker compose run --rm maintenance` desde el cron del host.
6. Comprobar el primer día:
   - `/system`: retrasos por clase, fallos, fuentes.
   - `/capital`: conciliación.
   - `aegis archive --verify` y `aegis archive --verify $AEGIS_BACKUP_DIR`.

## Política de backup y restauración (sin servicios de pago)

| Qué | Cómo | Frecuencia | RPO | Retención |
|---|---|---|---|---|
| Archivo de investigación (Parquet + `manifest.json`), que incluye las **tablas financieras de solo inserción** (libro, órdenes, ejecuciones, disposiciones, aportaciones) | `aegis maintenance` → `backup` incremental a `AEGIS_BACKUP_DIR`; cada copia se verifica por hash y `manifest.json` se escribe al final | diaria (timer) | 24 h | indefinida; el backup nunca borra |
| Base operativa (estado mutable: posiciones, lotes, cartera sombra, cola) | PostgreSQL: `pg_dump -Fc "$AEGIS_DATABASE_URL" > $AEGIS_BACKUP_DIR/db/aegis-$(date +%F).dump`. SQLite: `sqlite3 var/x.sqlite ".backup '$AEGIS_BACKUP_DIR/db/x-$(date +%F).sqlite'"` | diaria | 24 h | 30 diarios + 12 mensuales (rotación manual o por script del propietario) |
| Comprobación del backup | `aegis archive --verify $AEGIS_BACKUP_DIR` (no necesita la base de datos) | semanal | — | — |
| Simulacro de restauración | `aegis archive --restore $AEGIS_BACKUP_DIR` sobre un `AEGIS_ARCHIVE_DIR` temporal + `--verify` | mensual | — | — |

Regla 3-2-1 adaptada:

- **Copia primaria:** volumen del archivo.
- **Segunda copia:** `AEGIS_BACKUP_DIR` en otro disco físico o volumen.
- **Tercera copia fuera del servidor:** decisión del propietario (p. ej. un almacenamiento que ya tenga). AEGIS no contrata nada.

Procedimientos de restauración:

1. **Ficheros del archivo perdidos o corruptos.** `aegis archive --verify` los lista y
   `aegis archive --restore $AEGIS_BACKUP_DIR` los recupera. Nunca sobrescribe un lote válido.
2. **Base operativa perdida.**
   - Restaurar el último `pg_dump` o `.backup`.
   - Si no existe:
     1. Crear una base nueva con `aegis db upgrade`.
     2. Ejecutar `aegis archive --restore $AEGIS_BACKUP_DIR --import-manifest`, que vuelve a registrar los
        lotes desde el `manifest.json`.
   - El histórico de investigación y el libro financiero siguen siendo auditables en Parquet con DuckDB.
   - **Limitación:** el estado mutable posterior al último dump (posiciones abiertas, cola) no se
     reconstruye automáticamente.
3. **Integridad.** Cada lote tiene SHA-256 canónico. Un fichero corrupto nunca se propaga a un backup
   (`backup` lo rechaza) ni permite borrar filas en la base operativa.

## Migraciones

```bash
.venv/bin/aegis db upgrade
```

Para crear una migración nueva tras cambiar `storage/schema.py`:

```bash
AEGIS_DATABASE_URL=sqlite:////tmp/empty.sqlite .venv/bin/alembic revision --autogenerate -m "describe change"
```

Revisa el archivo generado y pruébalo en SQLite y en PostgreSQL (`AEGIS_TEST_POSTGRES_URL`).

## CI/CD

`.github/workflows/ci.yml` ejecuta en cada push y pull request: ruff, pytest (SQLite), el test de
PostgreSQL contra un servicio `postgres:16` y la compilación del dashboard. No despliega nada
automáticamente.

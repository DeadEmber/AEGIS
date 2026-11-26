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
- **Retención**: programa `aegis prune` a diario (cron del VPS o job programado).
- **API**: el mismo contenedor con `aegis api`, detrás de HTTPS. Con `AEGIS_API_CORS_ORIGINS` apuntando
  al dominio del dashboard.
- **Dashboard**: Vercel, con `NEXT_PUBLIC_AEGIS_API=https://<api>`.
- **Secretos**: en el gestor de la plataforma, nunca en el repositorio.

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

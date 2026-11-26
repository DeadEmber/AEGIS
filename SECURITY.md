# Seguridad

## Principios

- **Sin claves privadas en ningún sitio.** El repositorio, los logs, el frontend, la base de datos y
  las variables de entorno de AEGIS no contienen ni deben contener claves privadas, seed phrases ni
  keypairs. No existe código de firma.
- **Ejecución real inexistente por diseño.** `ExecutionMode` no tiene modo live. `LIVE_DISABLED` lanza
  `LiveExecutionDisabled` al construir el motor de ejecución y al iniciar el worker (probado en
  `test_live_disabled_mode_refuses_to_start`).
- **Secretos solo por entorno** (`.env`, nunca versionado; `.gitignore` lo excluye) o por el gestor de
  secretos de la plataforma de despliegue. El logger enmascara las claves con nombres sensibles
  (`key`, `secret`, `token`, `password`, `private`, `seed`…).
- **Endpoints mutantes protegidos.** `/control/*` exige `Authorization: Bearer $AEGIS_API_CONTROL_TOKEN`
  con comparación en tiempo constante. Sin token configurado devuelven 403. Cada cambio queda en
  `audit_logs`.
- **El resto de la API es de solo lectura.** CORS está limitado a `AEGIS_API_CORS_ORIGINS`.
- **El aprendizaje no puede modificar `RiskLimits`**: es un modelo inmutable (`frozen=True`) que se
  carga en el arranque.

## Requisitos previos a cualquier piloto real (FASE 14, no implementada)

1. Autorización expresa y por escrito del propietario, con límites monetarios explícitos.
2. Validación independiente del código de ejecución y de la contabilidad.
3. Un servicio de firma separado (HSM / wallet en hardware o servicio gestionado) fuera de este
   repositorio, con límites por operación, por día y por lista blanca de programas.
4. Wallet dedicada con saldo mínimo, nunca la wallet principal del propietario.
5. Shadow trading previo con conciliación contra on-chain durante un periodo significativo.
6. Revisión de las implicaciones fiscales (cada swap es una permuta tributable) y legales.

## Supabase / PostgreSQL

- Usa el rol `service_role` solo en el worker (backend). El dashboard consume la API de AEGIS, no
  Supabase directamente.
- Si en el futuro el frontend leyera Supabase, activa **RLS** en todas las tablas con políticas de solo
  lectura para usuarios autenticados y prohíbe la escritura desde el cliente.
- Copias de seguridad: activa PITR/backups diarios del proveedor. Para SQLite local, copia
  `var/*.sqlite` con el worker detenido o usa `sqlite3 .backup`.

## Notificación de vulnerabilidades

Abre un issue privado (Security Advisory) en GitHub. No publiques detalles explotables.

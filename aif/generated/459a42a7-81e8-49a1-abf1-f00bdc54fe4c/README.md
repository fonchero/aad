# Estadísticas de préstamos por cliente

Servicio HTTP de solo lectura en el puerto 8080. Requiere Go 1.24 y una base PostgreSQL existente con `public.customers` y `public.loans`. El usuario de PostgreSQL debe tener permiso de lectura sobre las columnas que usa la consulta.

## Ejecutar localmente

Configura las variables de conexión para tu entorno. Si se requiere contraseña, proporciona `PGPASSWORD` mediante el entorno del proceso.

```sh
export PGHOST=localhost
export PGPORT=5432
export PGDATABASE=banking
export PGUSER=banking_reader
go run .
```

Puedes configurar el modo TLS mediante `PGSSLMODE`. `PGCONNECT_TIMEOUT` controla el tiempo de conexión en segundos y toma el valor 3 por defecto en el servicio. Cada solicitud de estadísticas tiene un límite de 3 segundos.

## Consultar el servicio

```sh
curl -i http://localhost:8080/health
curl -i http://localhost:8080/api/v1/customers/42/loan-statistics
```

Sustituye `42` por el identificador que quieras consultar.

| Ruta o caso | HTTP | Cuerpo JSON |
| --- | --- | --- |
| `GET /health` | 200 | `{"status":"ok"}` |
| Cliente existente | 200 | `customer_id`, `total_loans`, `active_loans`, `defaulted_loans` y `paid_off_loans`, todos enteros |
| Identificador inválido | 400 | `{"error":"invalid_customer_id"}` |
| Cliente inexistente | 404 | `{"error":"customer_not_found"}` |
| Error de consulta | 500 | `{"error":"internal_error"}` |

El identificador acepta dígitos decimales con valor de 1 a 2147483647, el máximo del tipo `integer` de PostgreSQL. Los ceros iniciales se admiten. La ruta requiere un segmento para el identificador.

`total_loans` incluye todos los préstamos del cliente. Los otros tres campos cuentan las coincidencias exactas con `active`, `defaulted` y `paid_off` en `loans.status`. Para un cliente sin préstamos, los cuatro conteos son cero. `GET /health` indica que el servidor HTTP responde.

## Pruebas

```sh
go test ./...
```

Las pruebas HTTP usan un almacén simulado y se ejecutan sin PostgreSQL.

## Ejecutar con Docker

Exporta las variables `PG*` requeridas antes de ejecutar el contenedor. Usa un `PGHOST` accesible desde la red del contenedor.

```sh
docker build -t aif-loan-statistics .
docker run --rm -p 8080:8080 \
  -e PGHOST -e PGPORT -e PGDATABASE -e PGUSER -e PGPASSWORD \
  -e PGSSLMODE -e PGCONNECT_TIMEOUT aif-loan-statistics
```

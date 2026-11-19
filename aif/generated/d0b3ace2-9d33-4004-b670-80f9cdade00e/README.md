# Consulta de préstamos de un cliente

Servicio REST de solo lectura para el requerimiento AIF `d0b3ace2-9d33-4004-b670-80f9cdade00e`. Usa Go 1.24, `net/http` y `pgx/v5`.

## Configuración de PostgreSQL

Exporta las variables en el entorno del proceso con los valores autorizados para la base de datos existente. El rol debe tener permiso de lectura sobre `customers` y `loans`, y el `search_path` del rol debe resolver ambas tablas del esquema autorizado.

| Variable | Uso |
| --- | --- |
| `PGHOST`, `PGDATABASE`, `PGUSER` | Obligatorias. Identifican el servidor, la base de datos y el rol. |
| `PGPORT` | Puerto de PostgreSQL. Predeterminado `5432`. |
| `PGPASSWORD` | Contraseña si el método de autenticación la requiere. |
| `PGSSLMODE` | Modo TLS. Predeterminado `verify-full`. |
| `PGSSLROOTCERT` | Ruta al certificado de la CA si se necesita una CA privada. |
| `PGCONNECT_TIMEOUT` | Límite de conexión en segundos. Predeterminado `5`. |

El servicio usa conexiones con transacciones de solo lectura por defecto y una consulta `SELECT` parametrizada por petición. La consulta utiliza la relación `loans.customer_id = customers.id` y tiene un límite de 5 segundos.

## Ejecución local

Con Go 1.24 y las variables configuradas, ejecuta desde este directorio:

```sh
go test -mod=readonly ./...
go run -mod=readonly .
```

Las pruebas unitarias usan `httptest` y un lector inyectado. Se ejecutan sin PostgreSQL.

## Ejecución con Docker

La compilación de la imagen ejecuta las pruebas. El contenedor escucha en el puerto `8080` y se ejecuta con un usuario sin privilegios.

```sh
docker build -t aif-customer-loans .
docker run --rm -p 127.0.0.1:8080:8080 \
  -e PGHOST -e PGPORT -e PGDATABASE -e PGUSER -e PGPASSWORD \
  -e PGSSLMODE -e PGCONNECT_TIMEOUT aif-customer-loans
```

`PGHOST` debe ser alcanzable desde el contenedor. Si la base de datos usa una CA privada, monta su certificado en modo de solo lectura y configura `PGSSLROOTCERT` con la ruta dentro del contenedor.

## API

- `GET /health` devuelve HTTP 200 con `{"status":"ok"}` e indica que el servidor HTTP responde.
- `GET /api/v1/customers/{customer_id}/loans` consulta PostgreSQL. `customer_id` debe estar entre `1` y `2147483647`.

| HTTP | Respuesta de la consulta |
| --- | --- |
| 200 | Objeto con `customer_id` y `loans`. El arreglo contiene todos los préstamos ordenados por `id` ascendente y es `[]` cuando el cliente no tiene préstamos. |
| 400 | `{"error":"invalid_customer_id"}` |
| 404 | `{"error":"customer_not_found"}` |
| 500 | `{"error":"internal_error"}` |

Cada préstamo incluye `id`, `loan_number`, `amount`, `status` y `start_date`. `amount` es una cadena decimal y `start_date` usa el formato `YYYY-MM-DD`.

```sh
curl --fail-with-body http://localhost:8080/health
curl --fail-with-body "http://localhost:8080/api/v1/customers/${CUSTOMER_ID:?define el ID del cliente}/loans"
```

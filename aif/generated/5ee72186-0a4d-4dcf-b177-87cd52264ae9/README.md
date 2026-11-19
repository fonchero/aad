# Resumen de cartera AIF

Servicio HTTP en el puerto `8080`. La consulta de solo lectura agrupa los préstamos de `public.loans` por `status`, ordena los grupos por estado y calcula los importes con `SUM(amount)`. AIF verificó que `amount` es `NUMERIC(14,2)`. El agregado se lee como texto y una escala superior a dos decimales se trata como error.

## Ejecución

Usa Go 1.24 y una cuenta de PostgreSQL con permiso de lectura sobre `public.loans`. Define `PGHOST`, `PGPORT`, `PGDATABASE` y `PGUSER` para tu entorno. Si necesitas contraseña, suministra `PGPASSWORD` mediante el entorno de ejecución. Configura `PGSSLMODE` según el servidor. Para TLS con validación del nombre usa `verify-full` y, si corresponde, indica la ruta del certificado de CA en `PGSSLROOTCERT`.

```sh
go test ./...
go run .
```

Las pruebas usan dobles en memoria y pueden ejecutarse sin PostgreSQL. Para ejecutar el servicio en un contenedor, exporta las mismas variables y usa un `PGHOST` accesible desde el contenedor.

```sh
docker build -t aif-loans-summary .
docker run --rm -p 8080:8080 \
  -e PGHOST -e PGPORT -e PGDATABASE -e PGUSER -e PGPASSWORD -e PGSSLMODE \
  aif-loans-summary
```

## Endpoints

```sh
curl -fsS http://localhost:8080/health
curl -fsS http://localhost:8080/api/v1/loans/summary
```

- `GET /health` responde con HTTP 200 si PostgreSQL responde dentro de dos segundos y con HTTP 503 en caso contrario.
- `GET /api/v1/loans/summary` responde con HTTP 200 y un resumen JSON. Con la tabla vacía devuelve `{"total_loans":0,"by_status":[]}`. La consulta tiene un límite de cinco segundos. El servicio devuelve HTTP 504 si vence ese plazo y HTTP 500 ante otros errores de la consulta.

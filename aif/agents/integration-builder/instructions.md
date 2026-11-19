# AIF Integration Builder V0.1

## Objetivo

Generar un servicio Go funcional a partir de un
requerimiento autorizado por AIF.

## Entregables obligatorios

- go.mod y go.sum
- Código fuente Go
- Pruebas automatizadas Go
- Dockerfile multi-stage
- README.md con instrucciones de ejecución

## Reglas funcionales

- Cumplir el contrato recibido sin modificarlo.
- No inventar columnas o tablas.
- Utilizar solamente relaciones verificadas.
- Las consultas bancarias deben ser parametrizadas.
- Preservar precisión de valores monetarios.
- Implementar respuestas HTTP según el requerimiento.
- Manejar errores sin revelar detalles internos.

## Reglas técnicas

- Utilizar Go 1.24.
- Utilizar net/http de la biblioteca estándar.
- Utilizar pgx/v5 para PostgreSQL.
- No utilizar frameworks adicionales.
- No utilizar credenciales embebidas.
- Configurar PostgreSQL mediante variables PG*.
- Implementar endpoint GET /health.
- Usar contextos y tiempos límite.
- No crear ni modificar tablas bancarias.
- No modificar archivos fuera del directorio de trabajo.

## Seguridad

- No ejecutar migraciones.
- No implementar operaciones de escritura bancaria.
- No acceder a secretos locales.
- No desactivar pruebas ni validaciones.
- No realizar despliegues.
- No ejecutar git push.
- No modificar la aplicación AIF.

## Resultado

Generar directamente los archivos solicitados
en el directorio de trabajo.

No responder únicamente con una explicación o
con código dentro del mensaje final.

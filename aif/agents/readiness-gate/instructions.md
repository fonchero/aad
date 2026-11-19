# AIF Functional & Data Readiness Gate V0.1

## Objetivo

Determinar si un requerimiento funcional tiene suficiente
definicion y evidencia para comenzar su construccion.

## Evaluaciones independientes

FUNCTIONAL_READINESS:
- READY: las decisiones funcionales necesarias estan definidas.
- NEEDS_CLARIFICATION: faltan decisiones funcionales.

DATA_FEASIBILITY:
- FEASIBLE: existe evidencia suficiente de los datos requeridos.
- BLOCKED: existe una limitacion relevante demostrada.
- UNKNOWN: falta ejecutar descubrimiento adicional.

BUILD_READINESS:
- GO: ambas evaluaciones permiten construir.
- NO_GO: existen bloqueos o incertidumbres relevantes.

## Reglas obligatorias

1. No inventar reglas de negocio.
2. No inventar columnas o relaciones.
3. No transformar correlaciones en reglas funcionales.
4. Distinguir evidencia de inferencia.
5. No considerar el ultimo pago como fecha de cancelacion
   sin evidencia que respalde esa equivalencia.
6. No considerar el estado paid_off como prueba de que el
   historial de pagos registrado cubra el principal.
7. No preguntar al usuario lo que ya esta definido.
8. No pedir decisiones tecnicas cubiertas por AIF.
9. Formular preguntas exclusivamente sobre decisiones
   que requieren intervencion humana.
10. No declarar GO si falta una condicion esencial.

## Consistencia obligatoria

- Si functional_readiness es NEEDS_CLARIFICATION,
  build_readiness debe ser NO_GO.
- Si data_feasibility es BLOCKED o UNKNOWN,
  build_readiness debe ser NO_GO.
- Si existen blockers, build_readiness debe ser NO_GO.
- GO requiere functional_readiness READY,
  data_feasibility FEASIBLE y cero blockers.

## Evidencia

Cada finding debe incluir evidencia o indicar
expresamente que falta evidencia.

La ausencia de un campo no demuestra que el dato
no exista en otros sistemas.

Un bloqueo debe describir exactamente que no puede
garantizarse con la informacion disponible.

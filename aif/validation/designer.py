#!/usr/bin/env python3

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "aif/orchestrator"))

from worker import CONFIG, credentials

CONTRACT_DIR = ROOT / "aif/validation/contracts"
REGISTRY = ROOT / "aif/validation/registry.json"


def load_request(request_id):
    with psycopg.connect(
        **credentials(".env.control")
    ) as conn:
        row = conn.execute(
            """
            SELECT title, description, source_system,
                   status, specification
            FROM aif.requests
            WHERE id = %s
            """,
            (request_id,),
        ).fetchone()

    if not row:
        raise RuntimeError("Solicitud inexistente")

    title, description, source, status, spec = row

    readiness = spec.get("readiness", {})

    if (
        status != "READY_FOR_BUILD"
        or readiness.get("build_readiness") != "GO"
        or readiness.get("functional_readiness") != "READY"
        or readiness.get("data_feasibility") != "FEASIBLE"
        or readiness.get("blockers")
        or readiness.get("questions")
    ):
        raise RuntimeError("Solicitud sin GO válido")

    return {
        "id": request_id,
        "title": title,
        "description": description,
        "source_system": source,
        "analysis": spec["analysis"],
        "discovery": spec["banking_discovery"],
    }


def generate_contract(request):
    ai = CONFIG["agents"]["integration_builder"]

    if ai["provider"] != "codex":
        raise RuntimeError("Proveedor no soportado")

    discovery = request["discovery"]

    context = {
        "request_id": request["id"],
        "title": request["title"],
        "description": request["description"],
        "analysis": request["analysis"],
        "schema": {
            "columns": discovery.get("columns", []),
            "constraints": discovery.get("constraints", []),
            "numeric_metadata": [
                c for c in discovery.get(
                    "numeric_column_metadata", []
                )
                if c["table"] in {
                    x["table"]
                    for x in discovery.get("columns", [])
                }
            ],
        },
    }

    instructions = """
Eres AIF Test Designer.

Diseña pruebas de aceptación para un servicio
REST bancario todavía NO implementado.

NO generes código Go.
NO generes implementaciones.
NO ejecutes comandos.
NO accedas a bases de datos ni credenciales.

Devuelve exclusivamente un objeto JSON válido.

Formato obligatorio:

{
  "schema_version": "1",
  "id": "identificador-descriptivo-v1",
  "request_id": "UUID del contexto",
  "cases": [
    {
      "name": "health",
      "path": "/health",
      "status": 200,
      "expected_json": {"status": "ok"}
    }
  ]
}

Los casos pueden usar:
- name: identificador único
- path: ruta HTTP GET
- status: código HTTP esperado
- expected_json: objeto JSON literal esperado
- expected_sql: consulta SELECT que devuelve
  exactamente una fila y una columna de tipo JSON
- selector_sql: SELECT que devuelve un único
  entero para sustituir {value} en la ruta
- bind_value: true si expected_sql requiere %s
- minimum_array_items: objeto campo -> mínimo
- minimum_number: objeto campo -> mínimo

Reglas:
1. Incluye health y pruebas funcionales.
2. Incluye un caso positivo con expected_sql
   que calcule la respuesta esperada desde
   PostgreSQL, independientemente del servicio.
3. Para endpoints con identificador, incluye
   cliente existente, cliente inexistente,
   parámetro inválido y casos límite cuando
   sean aplicables.
4. Usa exclusivamente tablas y columnas
   verificadas en el contexto.
5. Usa consultas SELECT, sin escrituras,
   sin funciones de efectos secundarios,
   sin procedimientos, sin comandos DDL.
6. Si necesitas una variable de ruta, usa
   {value} y selector_sql.
7. expected_sql debe devolver un objeto JSON
   mediante json_build_object o equivalente.
8. Los importes monetarios deben preservar
   precisión y formato si el contrato lo exige.
9. No inventes datos, identificadores ni
   resultados esperados.
10. No copies pruebas de código generado:
    este contrato es el oráculo independiente.
11. Cada caso debe tener expected_json o
    expected_sql, pero nunca ambos.
12. No incluyas claves distintas de las
    descritas en este formato.
13. Máximo ocho casos. No uses paginación.
14. Devuelve JSON puro, sin Markdown.

CONTEXTO:
"""

    prompt = instructions + json.dumps(
        context, ensure_ascii=False, indent=2
    )

    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith("PG") or key in (
            "DATABASE_URL", "DB_PASSWORD"
        ):
            environment.pop(key, None)

    with tempfile.TemporaryDirectory(
        prefix="aad-test-designer-"
    ) as temporary:
        workspace = Path(temporary)
        output = workspace / "result.json"

        args = [
            "codex", "exec",
            "--cd", str(workspace),
            "--skip-git-repo-check",
            "--sandbox", "read-only",
            "--model", ai["model"],
            "-c",
            f'model_reasoning_effort="{ai["reasoning_effort"]}"',
            "--output-last-message", str(output),
            "-",
        ]

        result = subprocess.run(
            args,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=600,
            env=environment,
            check=False,
        )

        if result.returncode or not output.exists():
            raise RuntimeError(
                "Falló Test Designer:\n"
                + result.stderr[-1500:]
            )

        raw = output.read_text().strip()

    if raw.startswith("```"):
        raw = re.sub(
            r"^```(?:json)?\s*|\s*```$",
            "",
            raw,
        ).strip()

    return json.loads(raw)


def verify_sql(conn, sql, params=()):
    if not isinstance(sql, str):
        raise RuntimeError("SQL inválido")

    clean = sql.strip()

    # Filtro inicial para consultas generadas.
    # La protección principal es el usuario
    # PostgreSQL de solo lectura.
    if (
        not re.match(r"^SELECT\s", clean, re.I)
        or ";" in clean
        or "--" in clean
        or "/*" in clean
        or "*/" in clean
    ):
        raise RuntimeError("SQL fuera del perfil SELECT")

    forbidden = (
        r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|"
        r"CREATE|TRUNCATE|COPY|CALL|EXECUTE|"
        r"DO|GRANT|REVOKE|FOR\s+UPDATE|"
        r"pg_sleep|dblink|pg_read_file|"
        r"pg_write_file|set_config|nextval)\b"
    )

    if re.search(forbidden, clean, re.I):
        raise RuntimeError("SQL contiene operación no permitida")

    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute(
            "SET LOCAL statement_timeout = '3000ms'"
        )
        conn.execute(
            "EXPLAIN " + clean,
            params,
        ).fetchall()


def validate_contract(contract, request_id):
    if not isinstance(contract, dict):
        raise RuntimeError("Contrato no es un objeto")

    if set(contract) != {
        "schema_version", "id", "request_id", "cases"
    }:
        raise RuntimeError("Estructura de contrato inválida")

    if (
        contract["schema_version"] != "1"
        or contract["request_id"] != request_id
    ):
        raise RuntimeError("Identidad de contrato incorrecta")

    if not isinstance(contract["id"], str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9-]{2,79}", contract["id"]
    ):
        raise RuntimeError("ID de contrato inválido")

    cases = contract["cases"]

    if not isinstance(cases, list) or not 2 <= len(cases) <= 8:
        raise RuntimeError("Cantidad de casos inválida")

    names = set()
    has_sql_oracle = False
    has_health = False

    allowed = {
        "name", "path", "status",
        "expected_json", "expected_sql",
        "selector_sql", "bind_value",
        "minimum_array_items", "minimum_number"
    }

    with psycopg.connect(
        **credentials(".env.reader"),
        autocommit=True
    ) as conn:
        for case in cases:
            if not isinstance(case, dict) or set(case) - allowed:
                raise RuntimeError("Caso con estructura inválida")

            name = case.get("name")
            path = case.get("path")
            status = case.get("status")

            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", name)
                or name in names
            ):
                raise RuntimeError("Nombre de caso inválido")

            names.add(name)

            if (
                not isinstance(path, str)
                or not path.startswith("/")
                or "://" in path
                or len(path) > 300
            ):
                raise RuntimeError("Ruta HTTP inválida")

            if type(status) is not int or status not in (
                200, 400, 404
            ):
                raise RuntimeError("HTTP no soportado")

            if (
                ("expected_json" in case)
                == ("expected_sql" in case)
            ):
                raise RuntimeError(
                    "Se requiere exactamente un oráculo"
                )

            if "expected_json" in case and not isinstance(
                case["expected_json"], dict
            ):
                raise RuntimeError("expected_json debe ser objeto")

            selector = case.get("selector_sql")

            if "{value}" in path and not selector:
                raise RuntimeError(
                    "Ruta dinámica sin selector"
                )

            if selector:
                verify_sql(conn, selector)

            if "expected_sql" in case:
                has_sql_oracle = True
                params = (
                    (1,) if case.get("bind_value")
                    else ()
                )
                verify_sql(
                    conn, case["expected_sql"], params
                )

            for field in (
                "minimum_array_items",
                "minimum_number"
            ):
                invariant = case.get(field, {})
                if not isinstance(invariant, dict):
                    raise RuntimeError("Invariante inválido")
                if any(
                    not isinstance(k, str)
                    or type(v) is not int
                    or v < 0
                    for k, v in invariant.items()
                ):
                    raise RuntimeError("Invariante inválido")

            if (
                path == "/health"
                and status == 200
                and case.get("expected_json") == {
                    "status": "ok"
                }
            ):
                has_health = True

    if not has_health or not has_sql_oracle:
        raise RuntimeError(
            "Faltan health u oráculo SQL independiente"
        )

    return len(cases)


def ensure_contract(request_id):
    request_id = str(uuid.UUID(str(request_id)))

    registry = json.loads(REGISTRY.read_text())

    if registry.get("schema_version") != "1":
        raise RuntimeError("Registro inválido")

    known = registry["contracts"].get(request_id)

    if known:
        path = (CONTRACT_DIR / known).resolve()
        if path.parent != CONTRACT_DIR.resolve():
            raise RuntimeError("Ruta de contrato insegura")
        if not path.is_file():
            raise RuntimeError("Contrato registrado inexistente")

        contract = json.loads(path.read_text())
        validate_contract(contract, request_id)
        return path

    request = load_request(request_id)

    print(
        "Test Designer: creando contrato automáticamente",
        flush=True
    )

    contract = generate_contract(request)
    count = validate_contract(contract, request_id)

    filename = f"{request_id}.json"
    path = CONTRACT_DIR / filename

    if path.exists():
        raise RuntimeError("Conflicto de artefacto")

    path.write_text(
        json.dumps(
            contract, ensure_ascii=False, indent=2
        ) + "\n"
    )

    registry["contracts"][request_id] = filename
    REGISTRY.write_text(
        json.dumps(
            registry, ensure_ascii=False, indent=2
        ) + "\n"
    )

    print(
        f"PASS: contrato automático ({count} casos)",
        flush=True
    )
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-id", required=True)
    args = parser.parse_args()

    path = ensure_contract(args.request_id)

    print("Contrato:", path.relative_to(ROOT))


if __name__ == "__main__":
    main()

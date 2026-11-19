#!/usr/bin/env python3

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "aif/orchestrator"))

from worker import credentials


def http_request(base_url, path):
    url = base_url.rstrip("/") + path

    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )

    try:
        with urllib.request.urlopen(
            request, timeout=10
        ) as response:
            status = response.status
            raw = response.read(2_000_001)
    except urllib.error.HTTPError as error:
        status = error.code
        raw = error.read(2_000_001)

    if len(raw) > 2_000_000:
        raise RuntimeError("Respuesta HTTP demasiado grande")

    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            f"Respuesta no es JSON válido: {url}"
        ) from exc

    return status, payload


def select_value(conn, query):
    rows = conn.execute(query).fetchall()

    if len(rows) != 1 or len(rows[0]) != 1:
        raise RuntimeError(
            "El selector debe devolver una fila y una columna"
        )

    value = rows[0][0]

    if not isinstance(value, int) or isinstance(value, bool):
        raise RuntimeError(
            "El selector debe devolver un entero"
        )

    return value


def expected_from_db(conn, query, value=None):
    params = () if value is None else (value,)
    rows = conn.execute(query, params).fetchall()

    if len(rows) != 1 or len(rows[0]) != 1:
        raise RuntimeError(
            "La consulta de referencia debe devolver un JSON"
        )

    result = rows[0][0]

    if isinstance(result, str):
        result = json.loads(result)

    if not isinstance(result, dict):
        raise RuntimeError(
            "La referencia debe ser un objeto JSON"
        )

    return result


def check_invariants(case, actual):
    for field, minimum in case.get(
        "minimum_array_items", {}
    ).items():
        values = actual.get(field)
        if not isinstance(values, list) or len(values) < minimum:
            raise RuntimeError(
                f"{field} tiene menos elementos de los esperados"
            )

    for field, minimum in case.get(
        "minimum_number", {}
    ).items():
        value = actual.get(field)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < minimum
        ):
            raise RuntimeError(
                f"{field} está fuera del mínimo requerido"
            )


def validate_contract(contract_path, base_url):
    path = Path(contract_path).resolve()

    # Solo se ejecutan contratos versionados dentro de AIF.
    trusted = (ROOT / "aif/validation/contracts").resolve()
    if not path.is_relative_to(trusted):
        raise RuntimeError(
            "El contrato debe estar en aif/validation/contracts"
        )

    contract = json.loads(path.read_text())

    if contract.get("schema_version") != "1":
        raise RuntimeError("Versión de contrato no soportada")

    cases = contract.get("cases")

    if not isinstance(cases, list) or not cases:
        raise RuntimeError("Contrato sin casos")

    names = [c["name"] for c in cases]
    if len(names) != len(set(names)):
        raise RuntimeError("Nombres de pruebas duplicados")

    results = []

    with psycopg.connect(
        **credentials(".env.reader")
    ) as conn:

        for case in cases:
            name = case["name"]
            value = None

            if "selector_sql" in case:
                value = select_value(
                    conn, case["selector_sql"]
                )

            endpoint = case["path"]

            if "{value}" in endpoint:
                if value is None:
                    raise RuntimeError(
                        f"{name}: falta selector_sql"
                    )
                endpoint = endpoint.replace(
                    "{value}",
                    urllib.parse.quote(str(value), safe=""),
                )

            if "expected_sql" in case:
                expected = expected_from_db(
                    conn,
                    case["expected_sql"],
                    value if case.get("bind_value") else None,
                )
            else:
                expected = case["expected_json"]

            status, actual = http_request(
                base_url, endpoint
            )

            if status != case["status"]:
                raise RuntimeError(
                    f"{name}: HTTP {status}, "
                    f"esperado {case['status']}"
                )

            if actual != expected:
                raise RuntimeError(
                    f"{name}: JSON diferente de PostgreSQL "
                    f"o del contrato\n"
                    f"Esperado: {json.dumps(expected, ensure_ascii=False)}\n"
                    f"Obtenido: {json.dumps(actual, ensure_ascii=False)}"
                )

            check_invariants(case, actual)

            results.append({
                "case": name,
                "result": "PASS",
                "http_status": status,
            })

            print(f"PASS: {name} (HTTP {status})", flush=True)

    return {
        "contract": contract["id"],
        "schema_version": contract["schema_version"],
        "status": "PASS",
        "cases_passed": len(results),
        "cases_total": len(cases),
        "results": results,
        "validated_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    result = validate_contract(
        args.contract, args.base_url
    )

    if args.output:
        Path(args.output).write_text(
            json.dumps(result, indent=2) + "\n"
        )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

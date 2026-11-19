#!/usr/bin/env python3
import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from urllib.request import urlopen

import psycopg
from psycopg.types.json import Jsonb
from jsonschema import validate

ROOT = Path(__file__).resolve().parents[2]
API = "http://127.0.0.1:8080"
CONFIG = json.loads((ROOT / "config/ai.json").read_text())


def credentials(name):
    path = ROOT / "infrastructure/postgres" / name
    values = dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line and not line.startswith("#")
    )
    return {
        "host": "127.0.0.1",
        "port": 5433,
        "dbname": values["PGDATABASE"],
        "user": values["PGUSER"],
        "password": values["PGPASSWORD"],
        "connect_timeout": 5,
    }


def event(conn, request_id, event_type, payload):
    conn.execute(
        """
        INSERT INTO aif.events
            (request_id, event_type, source, payload)
        VALUES (%s, %s, 'AIF_ORCHESTRATOR', %s)
        """,
        (request_id, event_type, Jsonb(payload)),
    )


def fetch_request(request_id):
    with urlopen(
        f"{API}/api/v1/requests/{request_id}", timeout=10
    ) as response:
        return json.load(response)


def claim_request(request_id):
    with psycopg.connect(**credentials(".env.control")) as conn:
        row = conn.execute(
            """
            UPDATE aif.requests
            SET status = 'DISCOVERING',
                updated_at = NOW()
            WHERE id = %s
              AND status = 'DRAFT'
            RETURNING id
            """,
            (request_id,),
        ).fetchone()

        if not row:
            raise RuntimeError(
                "La solicitud no existe o ya no esta en DRAFT"
            )

        event(conn, request_id, "ANALYSIS_STARTED", {
            "status": "DISCOVERING"
        })


def codex_run(agent, prompt):
    settings = CONFIG["agents"][agent]
    provider = settings["provider"]

    if provider != "codex":
        raise RuntimeError(
            f"Proveedor aun no implementado: {provider}"
        )

    schema = ROOT / "aif/agents" / agent.replace(
        "_", "-"
    ) / "output.schema.json"

    # La ruta del Requirement Analyzer es historica.
    if agent == "requirement_analyzer":
        schema = ROOT / (
            "aif/agents/requirement-analyzer/output.schema.json"
        )
    elif agent == "readiness_gate":
        schema = ROOT / (
            "aif/agents/readiness-gate/output.schema.json"
        )

    with tempfile.TemporaryDirectory() as temporary:
        result_path = Path(temporary) / "result.json"

        cmd = [
            "codex", "exec",
            "--cd", str(ROOT),
            "--sandbox", "read-only",
            "--model", settings["model"],
            "-c",
            'model_reasoning_effort="' +
            settings["reasoning_effort"] + '"',
            "--output-schema", str(schema),
            "--output-last-message", str(result_path),
            "-"
        ]

        print(
            f"Ejecutando {agent}: "
            f"{settings['model']} / "
            f"{settings['reasoning_effort']}",
            flush=True,
        )

        completed = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=CONFIG["defaults"]["timeout_seconds"],
            cwd=ROOT,
        )

        if completed.returncode != 0:
            raise RuntimeError(
                f"Codex fallo en {agent}: "
                + completed.stderr[-1500:]
            )

        result = json.loads(result_path.read_text())
        validate(result, json.loads(schema.read_text()))
        return result


def analyze(request):
    instructions = (
        ROOT / "aif/agents/requirement-analyzer/instructions.md"
    ).read_text()

    prompt = (
        instructions
        + "\n\nREQUERIMIENTO REGISTRADO:\n"
        + json.dumps(request, ensure_ascii=False, indent=2)
        + "\n\nNo modifiques archivos. Devuelve solo JSON."
    )

    return codex_run("requirement_analyzer", prompt)


def discover_banking():
    evidence = {}

    with psycopg.connect(
        **credentials(".env.reader")
    ) as conn:
        conn.execute("SET TRANSACTION READ ONLY")

        columns = conn.execute(
            """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name IN (
                  'loans', 'loan_payments', 'customers'
              )
            ORDER BY table_name, ordinal_position
            """
        ).fetchall()

        evidence["columns"] = [
            {
                "table": t,
                "column": c,
                "type": d
            }
            for t, c, d in columns
        ]

        constraints = conn.execute(
            """
            SELECT conrelid::regclass::text,
                   conname,
                   pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid IN (
                'public.loans'::regclass,
                'public.loan_payments'::regclass
            )
            ORDER BY 1, 2
            """
        ).fetchall()

        evidence["constraints"] = [
            {
                "table": table,
                "name": name,
                "definition": definition
            }
            for table, name, definition in constraints
        ]

        statuses = conn.execute(
            """
            SELECT status, COUNT(*)
            FROM public.loans
            GROUP BY status
            ORDER BY status
            """
        ).fetchall()

        evidence["loan_status_counts"] = {
            status: count for status, count in statuses
        }

        coverage = conn.execute(
            """
            WITH totals AS (
                SELECT l.id, l.amount,
                       COALESCE(SUM(p.principal_paid), 0)
                           AS paid_principal,
                       MAX(p.payment_date) AS last_payment
                FROM public.loans l
                LEFT JOIN public.loan_payments p
                  ON p.loan_id = l.id
                WHERE l.status = 'paid_off'
                GROUP BY l.id, l.amount
            )
            SELECT COUNT(*),
                   COUNT(*) FILTER (
                       WHERE last_payment IS NULL
                   ),
                   COUNT(*) FILTER (
                       WHERE paid_principal >= amount
                   )
            FROM totals
            """
        ).fetchone()

        evidence["paid_off_payment_coverage"] = {
            "paid_off_loans": coverage[0],
            "without_payments": coverage[1],
            "recorded_principal_covers_amount": coverage[2],
        }

    evidence["limitations"] = [
        "Los estados permitidos no prueban una fecha de cierre.",
        "MAX(payment_date) no es necesariamente la fecha de cancelacion.",
        "El historial de pagos puede ser parcial.",
        "No se han investigado otras fuentes de datos."
    ]

    return evidence


def readiness(request, analysis, discovery):
    instructions = (
        ROOT / "aif/agents/readiness-gate/instructions.md"
    ).read_text()

    prompt = (
        instructions
        + "\n\nREQUERIMIENTO:\n"
        + json.dumps(request, ensure_ascii=False)
        + "\n\nANALISIS FUNCIONAL:\n"
        + json.dumps(analysis, ensure_ascii=False)
        + "\n\nEVIDENCIA BANCARIA:\n"
        + json.dumps(discovery, ensure_ascii=False)
        + "\n\nUsa solamente esta evidencia."
        + "\nNo inventes reglas ni modifiques archivos."
        + "\nDevuelve exclusivamente JSON."
    )

    result = codex_run("readiness_gate", prompt)

    functional = result["functional_readiness"]
    data = result["data_feasibility"]
    blockers = result["blockers"]

    if result["build_readiness"] == "GO":
        if (
            functional != "READY"
            or data != "FEASIBLE"
            or blockers
            or result["questions"]
        ):
            raise RuntimeError(
                "Inconsistencia: GO sin condiciones suficientes"
            )

    if functional != "READY" or data != "FEASIBLE" or blockers:
        if result["build_readiness"] != "NO_GO":
            raise RuntimeError(
                "Inconsistencia: GO con bloqueos pendientes"
            )

    return result


def persist(request_id, analysis, discovery, decision):
    is_ready = decision["build_readiness"] == "GO"
    status = "READY_FOR_BUILD" if is_ready else "WAITING_INPUT"

    specification = {
        "analysis": analysis,
        "banking_discovery": discovery,
        "readiness": decision,
    }

    with psycopg.connect(**credentials(".env.control")) as conn:
        row = conn.execute(
            """
            UPDATE aif.requests
            SET specification = %s,
                status = %s,
                updated_at = NOW()
            WHERE id = %s AND status = 'DISCOVERING'
            RETURNING id
            """,
            (Jsonb(specification), status, request_id),
        ).fetchone()

        if not row:
            raise RuntimeError(
                "La solicitud cambio de estado durante el analisis"
            )

        event(conn, request_id, "REQUIREMENT_ANALYZED", {
            "readiness": analysis["readiness"],
            "model": CONFIG["agents"][
                "requirement_analyzer"
            ]["model"],
        })

        event(conn, request_id, "BANKING_DISCOVERY_COMPLETED", {
            "loan_status_counts":
                discovery["loan_status_counts"],
            "paid_off_payment_coverage":
                discovery["paid_off_payment_coverage"],
        })

        for question in decision["questions"]:
            conn.execute(
                """
                INSERT INTO aif.requirement_questions
                    (request_id, question, reason, priority)
                VALUES (%s, %s, %s, 'BLOCKING')
                """,
                (
                    request_id,
                    question["question"],
                    question["reason"],
                ),
            )

        event(conn, request_id, "READINESS_EVALUATED", {
            "functional": decision["functional_readiness"],
            "data": decision["data_feasibility"],
            "build": decision["build_readiness"],
            "blockers": decision["blockers"],
            "questions_count": len(decision["questions"]),
            "status": status,
        })

    return status


def record_failure(request_id, message):
    with psycopg.connect(**credentials(".env.control")) as conn:
        conn.execute(
            """
            UPDATE aif.requests
            SET status = 'FAILED', updated_at = NOW()
            WHERE id = %s AND status = 'DISCOVERING'
            """,
            (request_id,),
        )
        event(conn, request_id, "ANALYSIS_FAILED", {
            "error": message[:1000]
        })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-id", required=True)
    args = parser.parse_args()

    request = fetch_request(args.request_id)

    if request["source_system"] != "banking":
        raise RuntimeError(
            "Este worker solo admite source_system=banking"
        )

    if "préstam" not in (
        request["title"] + request["description"]
    ).lower() and "prestam" not in (
        request["title"] + request["description"]
    ).lower():
        raise RuntimeError(
            "Banking Discovery V0.1 solo admite prestamos"
        )

    claim_request(args.request_id)

    try:
        analysis = analyze(request)
        print("Analisis:", analysis["readiness"], flush=True)

        discovery = discover_banking()
        print("Descubrimiento bancario completado", flush=True)

        decision = readiness(request, analysis, discovery)
        print(
            "Readiness:", decision["build_readiness"],
            flush=True,
        )

        status = persist(
            args.request_id, analysis, discovery, decision
        )

        print(json.dumps({
            "request_id": args.request_id,
            "status": status,
            "build_readiness": decision["build_readiness"],
            "questions": len(decision["questions"]),
            "blockers": len(decision["blockers"]),
        }, indent=2))

    except Exception as error:
        try:
            record_failure(args.request_id, str(error))
        except Exception as recording_error:
            print("Error al registrar fallo:", recording_error)
        raise


if __name__ == "__main__":
    main()

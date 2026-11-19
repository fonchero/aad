#!/usr/bin/env python3

import argparse
import json
from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb

from worker import (
    ROOT,
    CONFIG,
    credentials,
    discover_banking,
    fetch_request,
    codex_run,
)


def audit(conn, request_id, execution_id, kind, payload):
    conn.execute(
        """
        INSERT INTO aif.events
            (request_id, execution_id, event_type, source, payload)
        VALUES (%s, %s, %s, 'technical_resume', %s)
        """,
        (
            request_id,
            execution_id,
            kind,
            Jsonb(payload),
        ),
    )


def claim(request_id):
    with psycopg.connect(
        **credentials(".env.control")
    ) as conn:

        row = conn.execute(
            """
            SELECT status, specification
            FROM aif.requests
            WHERE id = %s
            FOR UPDATE
            """,
            (request_id,),
        ).fetchone()

        if not row:
            raise RuntimeError("Solicitud inexistente")

        status, specification = row

        if status != "BLOCKED":
            raise RuntimeError(
                f"Se requiere BLOCKED, actual: {status}"
            )

        if not all(
            k in specification
            for k in (
                "analysis",
                "banking_discovery",
                "readiness",
            )
        ):
            raise RuntimeError(
                "Falta contexto de la ejecución anterior"
            )

        if specification["readiness"]["build_readiness"] != "NO_GO":
            raise RuntimeError(
                "La decisión anterior no es NO_GO"
            )

        pending = conn.execute(
            """
            SELECT COUNT(*)
            FROM aif.requirement_questions
            WHERE request_id = %s
              AND status = 'OPEN'
            """,
            (request_id,),
        ).fetchone()[0]

        if pending:
            raise RuntimeError(
                "Hay preguntas abiertas; usar reanudación funcional"
            )

        attempt = conn.execute(
            """
            SELECT COALESCE(MAX(attempt), 0) + 1
            FROM aif.executions
            WHERE request_id = %s
            """,
            (request_id,),
        ).fetchone()[0]

        execution_id = conn.execute(
            """
            INSERT INTO aif.executions (
                request_id,
                attempt,
                status,
                agent_provider,
                started_at
            )
            VALUES (%s, %s, 'RUNNING', %s, NOW())
            RETURNING id
            """,
            (
                request_id,
                attempt,
                CONFIG["agents"]["readiness_gate"]["provider"],
            ),
        ).fetchone()[0]

        conn.execute(
            """
            UPDATE aif.requests
            SET status = 'DISCOVERING',
                updated_at = NOW()
            WHERE id = %s
            """,
            (request_id,),
        )

        audit(
            conn,
            request_id,
            execution_id,
            "TECHNICAL_RESUME_STARTED",
            {
                "attempt": attempt,
                "reused_analysis": True,
                "refresh_banking_discovery": True,
            },
        )

    return execution_id, specification, attempt


def reevaluate(request, specification, new_discovery):
    instructions = (
        ROOT / "aif/agents/readiness-gate/instructions.md"
    ).read_text()

    # Incluir respuestas funcionales anteriores si existen.
    with psycopg.connect(
        **credentials(".env.control")
    ) as conn:
        rows = conn.execute(
            """
            SELECT question, answer
            FROM aif.requirement_questions
            WHERE request_id = %s
              AND status = 'ANSWERED'
            ORDER BY created_at, id
            """,
            (request["id"],),
        ).fetchall()

    answers = [
        {"question": q, "answer": a}
        for q, a in rows
    ]

    context = {
        "request": {
            "id": request["id"],
            "title": request["title"],
            "description": request["description"],
            "source_system": request["source_system"],
        },
        "analysis": specification["analysis"],
        "answered_questions": answers,
        "previous_readiness": specification["readiness"],
        "updated_banking_discovery": new_discovery,
    }

    prompt = (
        instructions
        + "\n\n# REEVALUACION POR NUEVA EVIDENCIA TECNICA\n"
        + json.dumps(
            context,
            ensure_ascii=False,
            default=str,
        )
        + """

REGLAS:
- Reutiliza el análisis funcional existente.
- La decisión anterior es histórica, no vinculante.
- La evidencia bancaria actualizada sustituye
  a la evidencia bancaria anterior.
- Evalúa nuevamente los bloqueos con evidencia real.
- numeric_precision y numeric_scale son metadatos
  declarados por PostgreSQL.
- NUMERIC(14,2) garantiza escala máxima de dos
  decimales para valores almacenados.
- SUM(numeric) calcula una suma exacta.
- Un resultado con exactamente dos decimales puede
  representarse mediante formato decimal explícito.
- No inventes garantías ausentes.
- No declares GO si quedan bloqueos.
- No repitas preguntas respondidas.
- No generes código ni modifiques archivos.
- Devuelve exclusivamente JSON con el esquema del Gate.
"""
    )

    decision = codex_run("readiness_gate", prompt)

    if decision["build_readiness"] == "GO":
        if (
            decision["functional_readiness"] != "READY"
            or decision["data_feasibility"] != "FEASIBLE"
            or decision["blockers"]
            or decision["questions"]
        ):
            raise RuntimeError(
                "GO inconsistente: faltan condiciones"
            )

    elif decision["build_readiness"] == "NO_GO":
        pass

    else:
        raise RuntimeError(
            "Valor inválido de build_readiness"
        )

    return decision


def persist(
    request_id,
    execution_id,
    specification,
    discovery,
    decision,
):
    questions = decision["questions"]

    if decision["build_readiness"] == "GO":
        new_status = "READY_FOR_BUILD"
    elif questions:
        new_status = "WAITING_INPUT"
    else:
        new_status = "BLOCKED"

    history = list(
        specification.get("readiness_history", [])
    )
    previous = specification["readiness"]

    if not history or history[-1] != previous:
        history.append(previous)

    history.append(decision)

    specification["readiness_history"] = history
    specification["banking_discovery"] = discovery
    specification["readiness"] = decision
    specification["last_technical_resume"] = {
        "execution_id": str(execution_id),
        "evaluated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "analysis_reused": True,
        "discovery_refreshed": True,
    }

    with psycopg.connect(
        **credentials(".env.control")
    ) as conn:
        row = conn.execute(
            """
            SELECT status
            FROM aif.requests
            WHERE id = %s
            FOR UPDATE
            """,
            (request_id,),
        ).fetchone()

        if not row or row[0] != "DISCOVERING":
            raise RuntimeError(
                "La solicitud cambió de estado"
            )

        existing_rows = conn.execute(
            """
            SELECT question, status
            FROM aif.requirement_questions
            WHERE request_id = %s
            """,
            (request_id,),
        ).fetchall()

        existing = {
            q.strip().casefold(): status
            for q, status in existing_rows
        }

        new_count = 0
        for item in questions:
            question = item["question"]
            normalized = question.strip().casefold()

            if normalized in existing:
                if existing[normalized] == "ANSWERED":
                    continue
                raise RuntimeError(
                    "El Gate repitió una pregunta existente"
                )

            conn.execute(
                """
                INSERT INTO aif.requirement_questions (
                    request_id,
                    question,
                    reason,
                    priority
                )
                VALUES (%s, %s, %s, 'BLOCKING')
                """,
                (
                    request_id,
                    question,
                    item.get(
                        "reason",
                        "Aclaración requerida"
                    ),
                ),
            )
            existing[normalized] = "OPEN"
            new_count += 1

        if new_status == "WAITING_INPUT" and new_count == 0:
            # No dejar la solicitud esperando preguntas
            # que no se han registrado.
            new_status = "BLOCKED"

        conn.execute(
            """
            UPDATE aif.requests
            SET specification = %s,
                status = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                Jsonb(specification),
                new_status,
                request_id,
            ),
        )

        execution_status = (
            "WAITING_INPUT"
            if new_status == "WAITING_INPUT"
            else "SUCCEEDED"
        )

        conn.execute(
            """
            UPDATE aif.executions
            SET status = %s,
                finished_at = NOW()
            WHERE id = %s
            """,
            (execution_status, execution_id),
        )

        audit(
            conn,
            request_id,
            execution_id,
            "TECHNICAL_READINESS_REEVALUATED",
            {
                "request_status": new_status,
                "build_readiness":
                    decision["build_readiness"],
                "data_feasibility":
                    decision["data_feasibility"],
                "new_questions": new_count,
                "discovery_refreshed": True,
            },
        )

    return new_status


def fail(request_id, execution_id, error):
    with psycopg.connect(
        **credentials(".env.control")
    ) as conn:
        conn.execute(
            """
            UPDATE aif.executions
            SET status = 'FAILED',
                error_message = %s,
                finished_at = NOW()
            WHERE id = %s
              AND status = 'RUNNING'
            """,
            (str(error)[:1500], execution_id),
        )

        conn.execute(
            """
            UPDATE aif.requests
            SET status = 'BLOCKED',
                updated_at = NOW()
            WHERE id = %s AND status = 'DISCOVERING'
            """,
            (request_id,),
        )

        audit(
            conn,
            request_id,
            execution_id,
            "TECHNICAL_RESUME_FAILED",
            {"error": str(error)[:1000]},
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--request-id",
        required=True,
    )
    args = parser.parse_args()

    request = fetch_request(args.request_id)

    execution_id, specification, attempt = claim(
        args.request_id
    )

    print(
        f"Technical resume attempt {attempt}: {execution_id}",
        flush=True,
    )

    try:
        discovery = discover_banking()
        print(
            "PASS: Banking Discovery actualizado",
            flush=True,
        )

        decision = reevaluate(
            request,
            specification,
            discovery,
        )

        status = persist(
            args.request_id,
            execution_id,
            specification,
            discovery,
            decision,
        )

        print(json.dumps({
            "request_id": args.request_id,
            "execution_id": str(execution_id),
            "status": status,
            "build_readiness":
                decision["build_readiness"],
            "data_feasibility":
                decision["data_feasibility"],
            "blockers": len(decision["blockers"]),
            "questions": len(decision["questions"]),
            "analysis_reused": True,
            "discovery_refreshed": True,
        }, indent=2))

    except Exception as exc:
        fail(args.request_id, execution_id, exc)
        raise


if __name__ == "__main__":
    main()

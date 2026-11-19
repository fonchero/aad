#!/usr/bin/env python3

import argparse
import hashlib
import json
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from worker import ROOT, credentials


def command(*args, timeout=180):
    result = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"Comando fallido: {args[0]}\n"
            f"{result.stdout[-2500:]}\n"
            f"{result.stderr[-2500:]}"
        )
    return result.stdout.strip()


def audit(conn, request_id, execution_id, event_type, payload):
    conn.execute(
        """
        INSERT INTO aif.events
            (request_id, execution_id, event_type, source, payload)
        VALUES (%s, %s, %s, 'build_pipeline', %s)
        """,
        (request_id, execution_id, event_type, Jsonb(payload)),
    )


def claim(request_id):
    with psycopg.connect(**credentials(".env.control")) as conn:
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
        readiness = specification.get("readiness", {})

        if status != "READY_FOR_BUILD":
            raise RuntimeError(f"Estado no autorizado: {status}")

        if (
            readiness.get("build_readiness") != "GO"
            or readiness.get("functional_readiness") != "READY"
            or readiness.get("data_feasibility") != "FEASIBLE"
            or readiness.get("blockers")
            or readiness.get("questions")
        ):
            raise RuntimeError("Readiness GO no válido")

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
                request_id, attempt, status,
                agent_provider, started_at
            )
            VALUES (%s, %s, 'RUNNING',
                    'local-validation', NOW())
            RETURNING id
            """,
            (request_id, attempt),
        ).fetchone()[0]

        conn.execute(
            """
            UPDATE aif.requests
            SET status = 'IN_PROGRESS', updated_at = NOW()
            WHERE id = %s
            """,
            (request_id,),
        )

        audit(
            conn, request_id, execution_id,
            "BUILD_VALIDATION_STARTED",
            {"attempt": attempt, "mode": "adopt_existing_artifact"},
        )

    return execution_id, attempt


def verify_files(request_id):
    target = (
        ROOT / "aif" / "generated" / str(request_id)
    ).resolve()

    expected_parent = (
        ROOT / "aif" / "generated"
    ).resolve()

    if target.parent != expected_parent or not target.is_dir():
        raise RuntimeError("Directorio de artefactos inválido")

    manifest_path = target / "build-manifest.json"
    manifest = json.loads(manifest_path.read_text())

    if str(manifest.get("request_id")) != str(request_id):
        raise RuntimeError("Request ID no coincide con manifiesto")

    if manifest.get("source", {}).get("build_readiness") != "GO":
        raise RuntimeError("Manifiesto sin autorización GO")

    recorded = manifest["artifact"]["files_sha256"]
    actual_names = {
        str(p.relative_to(target))
        for p in target.rglob("*")
        if p.is_file() and p.name != "build-manifest.json"
    }

    if actual_names != set(recorded):
        raise RuntimeError("Archivos diferentes a los del manifiesto")

    for name, expected_hash in recorded.items():
        path = target / name

        if (
            not path.is_file()
            or path.is_symlink()
            or not path.resolve().is_relative_to(target)
        ):
            raise RuntimeError(f"Archivo inválido: {name}")

        actual_hash = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()

        if actual_hash != expected_hash:
            raise RuntimeError(f"Hash incorrecto: {name}")

    return target, manifest


def verify_go(target):
    image = "golang:1.24-alpine"

    output = command(
        "docker", "run", "--rm",
        "--network", "none",
        "-v", f"{target}:/app:ro",
        "-v", "aad-go-mod-cache:/go/pkg/mod:ro",
        "-v", "aad-go-build-cache:/root/.cache/go-build",
        "-w", "/app",
        "-e", "GOPROXY=off",
        image,
        "sh", "-ec",
        """
        test -z "$(gofmt -l .)"
        go test -count=1 ./...
        go vet ./...
        go build -o /tmp/generated-service .
        """,
        timeout=240,
    )

    return {
        "format": "PASS",
        "tests": "PASS",
        "vet": "PASS",
        "build": "PASS",
        "output": output[-1500:],
    }


def http_json(url, expected_status):
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            status = response.status
            body = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read()

    if status != expected_status:
        raise RuntimeError(
            f"HTTP {status}, esperado {expected_status}: {url}"
        )

    return json.loads(body), status


def verify_running_image(manifest):
    expected_image = manifest["artifact"]["image_id"]

    image = command(
        "docker", "inspect",
        "--format", "{{.Image}}",
        "aad-customer-loans-test",
    )

    running = command(
        "docker", "inspect",
        "--format", "{{.State.Running}}",
        "aad-customer-loans-test",
    )

    if running != "true" or image != expected_image:
        raise RuntimeError(
            "El contenedor no usa la imagen registrada"
        )

    return image


def verify_e2e(base_url):
    with psycopg.connect(**credentials(".env.reader")) as conn:
        customer_id = conn.execute(
            """
            SELECT customer_id
            FROM public.loans
            GROUP BY customer_id
            ORDER BY COUNT(*) DESC, customer_id
            LIMIT 1
            """
        ).fetchone()[0]

        expected = conn.execute(
            """
            SELECT json_build_object(
                'customer_id', c.id,
                'loans', COALESCE(
                    (
                        SELECT json_agg(
                            json_build_object(
                                'id', l.id,
                                'loan_number', l.loan_number,
                                'amount', l.amount::text,
                                'status', l.status,
                                'start_date', l.start_date::text
                            )
                            ORDER BY l.id
                        )
                        FROM public.loans l
                        WHERE l.customer_id = c.id
                    ),
                    '[]'::json
                )
            )
            FROM public.customers c
            WHERE c.id = %s
            """,
            (customer_id,),
        ).fetchone()[0]

    health, _ = http_json(base_url + "/health", 200)
    if health.get("status") != "ok":
        raise RuntimeError("Health inválido")

    actual, _ = http_json(
        base_url + f"/api/v1/customers/{customer_id}/loans",
        200,
    )

    if actual != expected or not actual["loans"]:
        raise RuntimeError("El resultado no coincide con PostgreSQL")

    missing, _ = http_json(
        base_url + "/api/v1/customers/2147483647/loans",
        404,
    )
    invalid, _ = http_json(
        base_url + "/api/v1/customers/abc/loans",
        400,
    )

    if missing != {"error": "customer_not_found"}:
        raise RuntimeError("Contrato 404 incorrecto")

    if invalid != {"error": "invalid_customer_id"}:
        raise RuntimeError("Contrato 400 incorrecto")

    return {
        "status": "PASS",
        "customer_id": customer_id,
        "loans_compared": len(actual["loans"]),
        "exact_json_match": True,
        "health": 200,
        "existing_customer": 200,
        "unknown_customer": 404,
        "invalid_customer_id": 400,
    }


def finish_success(
    request_id, execution_id, target, manifest, validation, image_id
):
    with psycopg.connect(**credentials(".env.control")) as conn:
        row = conn.execute(
            """
            SELECT status FROM aif.requests
            WHERE id = %s FOR UPDATE
            """,
            (request_id,),
        ).fetchone()

        if not row or row[0] != "IN_PROGRESS":
            raise RuntimeError("Estado modificado durante validación")

        conn.execute(
            """
            INSERT INTO aif.build_artifacts (
                request_id, execution_id,
                artifact_path, image_id, manifest, validation
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                request_id, execution_id,
                str(target.relative_to(ROOT)),
                image_id, Jsonb(manifest), Jsonb(validation),
            ),
        )

        conn.execute(
            """
            UPDATE aif.executions
            SET status = 'SUCCEEDED', finished_at = NOW()
            WHERE id = %s
            """,
            (execution_id,),
        )

        conn.execute(
            """
            UPDATE aif.requests
            SET status = 'COMPLETED', updated_at = NOW()
            WHERE id = %s
            """,
            (request_id,),
        )

        audit(
            conn, request_id, execution_id,
            "BUILD_VALIDATION_COMPLETED",
            {
                "status": "PASS",
                "artifact_path": str(target.relative_to(ROOT)),
                "image_id": image_id,
                "loans_compared": validation["e2e"]["loans_compared"],
            },
        )


def finish_failure(request_id, execution_id, message):
    with psycopg.connect(**credentials(".env.control")) as conn:
        conn.execute(
            """
            UPDATE aif.executions
            SET status = 'FAILED',
                error_message = %s,
                finished_at = NOW()
            WHERE id = %s AND status = 'RUNNING'
            """,
            (message[:1500], execution_id),
        )

        conn.execute(
            """
            UPDATE aif.requests
            SET status = 'READY_FOR_BUILD',
                updated_at = NOW()
            WHERE id = %s AND status = 'IN_PROGRESS'
            """,
            (request_id,),
        )

        audit(
            conn, request_id, execution_id,
            "BUILD_VALIDATION_FAILED",
            {"error": message[:1000]},
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-id", required=True)
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:18081"
    )
    args = parser.parse_args()

    request_id = args.request_id

    execution_id, attempt = claim(request_id)
    print(f"Build validation attempt {attempt}: {execution_id}", flush=True)

    try:
        target, manifest = verify_files(request_id)
        print("PASS: integridad del artefacto", flush=True)

        go = verify_go(target)
        print("PASS: Go format/test/vet/build", flush=True)

        image_id = verify_running_image(manifest)
        print("PASS: imagen Docker verificada", flush=True)

        e2e = verify_e2e(args.base_url)
        print("PASS: HTTP contra PostgreSQL", flush=True)

        validation = {
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "go": go,
            "e2e": e2e,
            "mode": "adopt_existing_artifact",
        }

        finish_success(
            request_id, execution_id,
            target, manifest, validation, image_id
        )

        print(json.dumps({
            "request_id": request_id,
            "execution_id": str(execution_id),
            "status": "COMPLETED",
            "validation": "PASS",
            "artifact_path": str(target.relative_to(ROOT)),
            "loans_compared": e2e["loans_compared"],
        }, indent=2))

    except Exception as exc:
        finish_failure(request_id, execution_id, str(exc))
        raise


if __name__ == "__main__":
    main()

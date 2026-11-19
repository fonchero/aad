#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from worker import ROOT, CONFIG, credentials
from build_pipeline import (
    claim, finish_failure, verify_go, audit
)


def run(args, cwd=None, timeout=300, env=None, stdin=None):
    result = subprocess.run(
        args,
        cwd=cwd or ROOT,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"Comando fallido: {args[0]}\n"
            f"{result.stdout[-2000:]}\n"
            f"{result.stderr[-2000:]}"
        )
    return result.stdout.strip()


def load_request(request_id):
    with psycopg.connect(**credentials(".env.control")) as conn:
        row = conn.execute(
            """
            SELECT title, description, source_system,
                   specification, status
            FROM aif.requests WHERE id = %s
            """,
            (request_id,),
        ).fetchone()

    if not row:
        raise RuntimeError("Solicitud inexistente")

    title, description, source, spec, status = row
    return {
        "id": request_id,
        "title": title,
        "description": description,
        "source_system": source,
        "specification": spec,
        "status": status,
    }


def check_contract(request):
    # V0.3: segundo contrato de aceptación.
    # No se pretende todavía admitir cualquier integración.
    description = request["description"]
    if (
        "/api/v1/loans/summary" not in description
        or "total_loans" not in description
        or "by_status" not in description
        or request["source_system"] != "banking"
    ):
        raise RuntimeError(
            "Contrato no soportado por el validador V0.3"
        )


def make_prompt(request):
    spec = request["specification"]
    discovery = spec["banking_discovery"]
    model_metadata = discovery.get("numeric_column_metadata", [])

    context = {
        "request_id": request["id"],
        "title": request["title"],
        "description": request["description"],
        "analysis": spec["analysis"],
        "readiness": spec["readiness"],
        "database_evidence": {
            "columns": [
                x for x in discovery["columns"]
                if x["table"] == "loans"
            ],
            "constraints": [
                x for x in discovery["constraints"]
                if x.get("table") == "loans"
            ],
            "numeric_metadata": [
                x for x in model_metadata
                if x["table"] == "loans"
            ],
        },
    }

    instructions = (
        ROOT / "aif/agents/integration-builder/instructions.md"
    ).read_text()

    return instructions + """

# CONSTRUCCION AUTORIZADA

Genera directamente en el directorio de trabajo
un proyecto Go 1.24 completo y ejecutable.

Endpoint obligatorio: GET /api/v1/loans/summary
Endpoint de salud: GET /health

El servicio HTTP debe escuchar en :8080.

Utiliza PostgreSQL con pgx/v5 y las variables PG*.
No utilices credenciales embebidas.
No modifiques la base de datos.

El resultado debe incluir:
- go.mod y go.sum
- código Go
- pruebas Go
- Dockerfile multi-stage
- README.md

El contrato JSON debe contener:
- total_loans: entero
- by_status: arreglo ordenado por status
- status: cadena
- count: entero
- total_amount: cadena con exactamente dos decimales

No codifiques los estados ni los importes.
No redondees silenciosamente valores con más de
dos decimales; usa los metadatos del esquema.

Utiliza SUM(amount) de PostgreSQL para el agregado
y formatea el decimal preservando su precisión.

Las consultas son de solo lectura.
El resultado vacío debe tener by_status: [].

IMPORTANTE:
No ejecutes despliegues.
No ejecutes Docker.
No hagas git commit ni git push.
No accedas a contraseñas ni servicios locales.
Genera archivos, no una explicación del código.

# CONTEXTO DE AIF

""" + json.dumps(context, ensure_ascii=False, indent=2)


def generate(request, workspace):
    ai = CONFIG["agents"]["integration_builder"]
    if ai["provider"] != "codex":
        raise RuntimeError("Proveedor no implementado")

    environment = os.environ.copy()
    # No transmitir credenciales PostgreSQL heredadas.
    for key in list(environment):
        if key.startswith("PG") or key in (
            "DATABASE_URL", "DB_PASSWORD"
        ):
            environment.pop(key, None)

    command = [
        "codex", "exec",
        "--cd", str(workspace),
        "--skip-git-repo-check",
        "--sandbox", "workspace-write",
        "--model", ai["model"],
        "-c",
        f'model_reasoning_effort="{ai["reasoning_effort"]}"',
        "-",
    ]

    output = run(
        command,
        timeout=600,
        env=environment,
        stdin=make_prompt(request),
    )

    print(
        "PASS: Integration Builder finalizado",
        flush=True,
    )

    return {
        "provider": ai["provider"],
        "model": ai["model"],
        "reasoning_effort": ai["reasoning_effort"],
        "invocations": 1,
        "output_excerpt": output[-1000:],
    }


def inspect_workspace(workspace):
    required = ["go.mod", "go.sum", "Dockerfile", "README.md"]
    for filename in required:
        if not (workspace / filename).is_file():
            raise RuntimeError(f"Falta archivo: {filename}")

    files = [p for p in workspace.rglob("*") if p.is_file()]
    if not any(p.suffix == ".go" for p in files):
        raise RuntimeError("No se generaron archivos Go")

    for p in workspace.rglob("*"):
        if p.is_symlink():
            raise RuntimeError(
                "No se admiten enlaces simbólicos"
            )

    # Revisión mínima de comandos de Dockerfile.
    # No constituye un análisis de seguridad completo.
    dockerfile = (workspace / "Dockerfile").read_text()
    forbidden = [
        "privileged",
        "docker.sock",
        "ssh ",
        "curl ",
        "wget ",
        "git clone",
    ]
    if any(x in dockerfile.lower() for x in forbidden):
        raise RuntimeError(
            "Dockerfile requiere revisión de seguridad"
        )

    if any(p.stat().st_size > 2_000_000 for p in files):
        raise RuntimeError("Archivo generado demasiado grande")

    return len(files)


def build_image(workspace, tag):
    environment = os.environ.copy()
    environment["DOCKER_BUILDKIT"] = "0"

    run(
        ["docker", "build",
         "--network", "bridge",
         "-t", tag, str(workspace)],
        timeout=600,
        env=environment,
    )

    return run([
        "docker", "image", "inspect",
        "--format", "{{.Id}}", tag
    ])


def start_service(tag, name, port):
    run([
        "docker", "run", "-d",
        "--name", name,
        "--network", "aad-integration-net",
        "-p", f"127.0.0.1:{port}:8080",
        "--env-file",
        str(ROOT / "infrastructure/postgres/.env.reader"),
        "-e", "PGHOST=aad-banking-db",
        "-e", "PGPORT=5432",
        "-e", "PGDATABASE=banking",
        "-e", "PGUSER=aif_reader",
        "-e", "PGSSLMODE=disable",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        tag,
    ])

    url = f"http://127.0.0.1:{port}/health"
    for _ in range(20):
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.5)

    raise RuntimeError(
        "El servicio no alcanzó estado saludable"
    )


def get_json(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(
                f"HTTP inesperado: {response.status}"
            )
        return json.load(response)


def validate_summary(base_url):
    with psycopg.connect(**credentials(".env.reader")) as conn:
        rows = conn.execute(
            """
            SELECT status, COUNT(*), SUM(amount)
            FROM public.loans
            GROUP BY status
            ORDER BY status
            """
        ).fetchall()

    expected = [
        {
            "status": status,
            "count": count,
            "total_amount": f"{total:.2f}",
        }
        for status, count, total in rows
    ]

    actual = get_json(
        base_url + "/api/v1/loans/summary"
    )

    if set(actual) != {"total_loans", "by_status"}:
        raise RuntimeError("Campos principales incorrectos")

    if not isinstance(actual["total_loans"], int):
        raise RuntimeError("total_loans debe ser entero")

    if not isinstance(actual["by_status"], list):
        raise RuntimeError("by_status debe ser arreglo")

    for item in actual["by_status"]:
        if set(item) != {"status", "count", "total_amount"}:
            raise RuntimeError("Campos de agregado incorrectos")
        if (
            not isinstance(item["status"], str)
            or not isinstance(item["count"], int)
            or not isinstance(item["total_amount"], str)
            or not re.fullmatch(
                r"-?\d+\.\d{2}", item["total_amount"]
            )
        ):
            raise RuntimeError("Tipos o formato decimal incorrectos")

    expected_count = sum(x["count"] for x in expected)

    if actual["total_loans"] != expected_count:
        raise RuntimeError("Total de préstamos incorrecto")

    if actual["by_status"] != expected:
        raise RuntimeError(
            "Agregados HTTP diferentes de PostgreSQL:\n"
            f"Esperado: {expected}\n"
            f"Obtenido: {actual['by_status']}"
        )

    return {
        "status": "PASS",
        "endpoint": "/api/v1/loans/summary",
        "total_loans": expected_count,
        "groups_compared": len(expected),
        "total_amount": format(
            sum(
                (Decimal(x["total_amount"]) for x in expected),
                Decimal("0")
            ),
            ".2f"
        ),
        "exact_json_match": True,
    }


def create_manifest(
    target, request, execution_id,
    builder, image_tag, image_id, validation
):
    hashes = {}
    for path in sorted(target.rglob("*")):
        if path.is_file():
            name = str(path.relative_to(target))
            if name == "build-manifest.json":
                continue
            hashes[name] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()

    manifest = {
        "schema_version": "0.2",
        "request_id": request["id"],
        "execution_id": str(execution_id),
        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "source": {
            "build_readiness": "GO",
        },
        "builder": builder,
        "artifact": {
            "image_tag": image_tag,
            "image_id": image_id,
            "files_sha256": hashes,
        },
        "validation": validation,
    }

    (target / "build-manifest.json").write_text(
        json.dumps(
            manifest, ensure_ascii=False, indent=2
        ) + "\n"
    )
    return manifest


def finish(request_id, execution_id, target, manifest, validation):
    with psycopg.connect(**credentials(".env.control")) as conn:
        row = conn.execute(
            """
            SELECT status FROM aif.requests
            WHERE id = %s FOR UPDATE
            """,
            (request_id,),
        ).fetchone()

        if not row or row[0] != "IN_PROGRESS":
            raise RuntimeError(
                "La solicitud no está en construcción"
            )

        conn.execute(
            """
            INSERT INTO aif.build_artifacts
              (request_id, execution_id, artifact_path,
               image_id, manifest, validation)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                request_id,
                execution_id,
                str(target.relative_to(ROOT)),
                manifest["artifact"]["image_id"],
                Jsonb(manifest),
                Jsonb(validation),
            ),
        )

        conn.execute(
            """
            UPDATE aif.executions
            SET status = 'SUCCEEDED',
                finished_at = NOW()
            WHERE id = %s AND status = 'RUNNING'
            """,
            (execution_id,),
        )

        conn.execute(
            """
            UPDATE aif.requests
            SET status = 'COMPLETED',
                updated_at = NOW()
            WHERE id = %s AND status = 'IN_PROGRESS'
            """,
            (request_id,),
        )

        audit(
            conn, request_id, execution_id,
            "AUTOMATED_BUILD_COMPLETED",
            {
                "validation": "PASS",
                "artifact_path": str(target.relative_to(ROOT)),
                "image_id": manifest["artifact"]["image_id"],
            },
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--port", type=int, default=18082)
    args = parser.parse_args()

    # UUID validado antes de formar nombres o rutas.
    import uuid
    request_id = str(uuid.UUID(args.request_id))

    request = load_request(request_id)
    check_contract(request)

    # claim() verifica el GO y bloquea transaccionalmente.
    execution_id, attempt = claim(request_id)

    print(
        f"Build attempt {attempt}: {execution_id}",
        flush=True,
    )

    workspace = Path(tempfile.mkdtemp(
        prefix="aad-build-",
    ))
    name = f"aad-summary-{request_id[:8]}"
    tag = f"aad-loans-summary:{request_id[:8]}"
    target = ROOT / "aif/generated" / request_id

    try:
        if target.exists():
            raise RuntimeError(
                "Ya existe un artefacto para esta solicitud"
            )

        builder = generate(request, workspace)

        count = inspect_workspace(workspace)
        print(f"PASS: {count} archivos revisados", flush=True)

        # Descargar módulos declarados sin ejecutar el servicio.
        run(
            [
                "docker", "run", "--rm",
                "--network", "bridge",
                "-v", f"{workspace}:/app:ro",
                "-v", "aad-go-mod-cache:/go/pkg/mod",
                "-w", "/app",
                "golang:1.24-alpine",
                "go", "mod", "download",
            ],
            timeout=180,
        )
        print("PASS: dependencias disponibles", flush=True)

        go_result = verify_go(workspace)
        print("PASS: Go format/test/vet/build", flush=True)

        image_id = build_image(workspace, tag)
        print("PASS: imagen Docker construida", flush=True)

        start_service(tag, name, args.port)
        print("PASS: contenedor iniciado", flush=True)

        e2e = validate_summary(
            f"http://127.0.0.1:{args.port}"
        )
        print("PASS: agregados PostgreSQL", flush=True)

        validation = {
            "status": "PASS",
            "go": go_result,
            "e2e": e2e,
            "validated_at": datetime.now(
                timezone.utc
            ).isoformat(),
        }

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(workspace, target)

        manifest = create_manifest(
            target, request, execution_id,
            builder, tag, image_id, validation,
        )

        finish(
            request_id, execution_id,
            target, manifest, validation,
        )

        print(json.dumps({
            "request_id": request_id,
            "execution_id": str(execution_id),
            "status": "COMPLETED",
            "validation": "PASS",
            "artifact_path": str(target.relative_to(ROOT)),
            "image_id": image_id,
            "total_loans": e2e["total_loans"],
            "groups_compared": e2e["groups_compared"],
        }, indent=2))

    except Exception as exc:
        finish_failure(request_id, execution_id, str(exc))
        print(
            f"Workspace conservado: {workspace}",
            flush=True,
        )
        raise


if __name__ == "__main__":
    main()

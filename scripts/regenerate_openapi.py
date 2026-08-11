"""Regenerate schema/openapi.yaml from the live application.

The committed YAML is the normative contract; the parity test compares it
against the generated OpenAPI after documented normalization (dropping
title/default/servers/security and bridging nullable unions). This script
emits the committed file in the same hand-documented style so the normative
artifact stays readable while remaining in exact parity with the app.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
OPENAPI_PATH = ROOT / "schema" / "openapi.yaml"

# operationIds are documentation, not compared by the parity test; keep the
# stable hand-authored names where they exist and derive sensible ones for
# new endpoints.
OPERATION_IDS = {
    ("/healthz", "get"): "getHealth",
    ("/readyz", "get"): "getReadiness",
    ("/api/v1/snapshot", "get"): "getSnapshot",
    ("/api/v1/services/{serviceId}/logs", "get"): "getServiceLogs",
    ("/api/v1/host", "get"): "getHost",
    ("/api/v1/host/history", "get"): "getHostHistory",
    ("/api/v1/units", "get"): "getUnits",
    ("/api/v1/units/{unitName}/logs", "get"): "getUnitLogs",
    ("/api/v1/units/{unitName}/operations", "post"): "postOperation",
    ("/api/v1/schedules", "get"): "getSchedules",
    ("/api/v1/schedules/{scheduleId}", "get"): "getSchedule",
    ("/api/v1/activity", "get"): "getActivity",
    ("/api/v1/schedules", "post"): "createSchedule",
    ("/api/v1/schedules/{scheduleId}", "put"): "updateSchedule",
    ("/api/v1/schedules/{scheduleId}", "delete"): "deleteSchedule",
}


def _drop_representation_keys(node: object) -> object:
    """Mirror the committed style: title/default are not carried."""
    if isinstance(node, dict):
        return {
            k: _drop_representation_keys(v)
            for k, v in node.items()
            if k not in ("title", "default")
        }
    if isinstance(node, list):
        return [_drop_representation_keys(v) for v in node]
    return node


def _emit() -> dict[str, object]:
    # Generate the OpenAPI from the installed package in a fresh interpreter so
    # the app's lifespan is not run by the dumping process.
    code = (
        "from hyperion.main import create_app; "
        "import json, sys; "
        "sys.stdout.write(json.dumps(create_app().openapi()))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT / "src"),
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONPATH": str(ROOT / "src")},
    )
    spec = json.loads(proc.stdout)
    spec = _drop_representation_keys(spec)

    # Documentation sections that the parity test drops but the committed
    # contract keeps for readers.
    spec["openapi"] = "3.1.0"
    spec["servers"] = [{"url": "/"}]
    spec["security"] = [{"authentikIdentity": []}]
    for (path, method), operation_id in OPERATION_IDS.items():
        operation = spec.get("paths", {}).get(path, {}).get(method)
        if operation is not None:
            operation["operationId"] = operation_id
    for path, methods in spec.get("paths", {}).items():
        for method, operation in methods.items():
            operation.setdefault("security", [])
    spec.setdefault("components", {})["securitySchemes"] = {
        "authentikIdentity": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Authentik-Username",
        }
    }
    return spec


def main() -> None:
    spec = _emit()
    components = spec.setdefault("components", {})
    schemas = components.get("schemas", {})
    for framework_schema in ("HTTPValidationError", "ValidationError"):
        schemas.pop(framework_schema, None)
    # The committed contract documents 422 only where a route declares it
    # (InvalidRequest); FastAPI's automatic per-parameter 422 responses are
    # stripped by the parity test and must not appear as dangling refs.
    for methods in spec.get("paths", {}).values():
        for operation in methods.values():
            responses = operation.get("responses", {})
            for status, response in list(responses.items()):
                if status != "422":
                    continue
                content = response.get("content", {})
                schema_ref = content.get("application/json", {}).get("schema", {})
                if schema_ref.get("$ref", "").endswith(("HTTPValidationError", "ValidationError")):
                    responses.pop(status)
    text = yaml.dump(spec, sort_keys=True, default_flow_style=False, allow_unicode=True)
    text = re.sub(r"^- '(\d+)':", lambda m: f"- '{m.group(1)}':", text)
    OPENAPI_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {OPENAPI_PATH}")


if __name__ == "__main__":
    main()

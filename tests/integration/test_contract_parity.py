"""Generated OpenAPI must match the committed schema/openapi.yaml contract."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from hyperion.main import create_app

OPENAPI_PATH = Path(__file__).parents[2] / "schema" / "openapi.yaml"

# Sibling constraints carried inside the non-null branch of a nullable union.
_CONSTRAINT_KEYS = (
    "format",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "pattern",
    "minItems",
    "maxItems",
    "exclusiveMinimum",
    "exclusiveMaximum",
)


def _resolve(node: Any, components: dict[str, Any]) -> Any:
    """Expand every $ref against the committed/generated components (schemas and responses)."""
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            if ref.startswith("#/components/responses/"):
                name = ref.removeprefix("#/components/responses/")
                target = copy.deepcopy(components["responses"][name])
            else:
                name = ref.removeprefix("#/components/schemas/")
                target = copy.deepcopy(components["schemas"][name])
            resolved = _resolve(target, components)
            extra = {k: v for k, v in node.items() if k != "$ref"}
            if extra:
                merged = copy.deepcopy(resolved)
                merged.update({k: _resolve(v, components) for k, v in extra.items()})
                return merged
            return resolved
        return {k: _resolve(v, components) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(i, components) for i in node]
    return node


def _sort(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _sort(v) for k, v in sorted(node.items())}
    if isinstance(node, list):
        return [_sort(i) for i in node]
    return node


def _normalize(node: Any) -> Any:
    """Canonicalize both sides; documented normalization rules only.

    Representation-level drops (cosmetic or not expressible in the generated
    contract): ``title``, ``default``, ``format``, ``servers``, ``security``,
    ``securitySchemes``, and ``openapi``. Auth enforcement (security/securitySchemes)
    is covered behaviorally by tests/integration/test_api.py, so the committed
    YAML's security section stays as documentation and is not compared.

    Structural bridges between the committed YAML style and pydantic's emission:
    - ``oneOf`` is compared as ``anyOf`` (OpenAPI 3.1 both valid; pydantic emits anyOf).
    - Type-array nullables ``type: [x, "null"]`` (committed YAML style) are rewritten
      to ``anyOf: [{type: x, <constraints>}, {type: null}]``; sibling constraints
      (format, min/max lengths and bounds) move into the non-null branch exactly as
      pydantic places them on union members.
    - ``type`` is dropped next to ``const``/``enum`` (pydantic emits both, the
      committed YAML only the const/enum).
    - FastAPI's automatic ``422`` response (HTTPValidationError) is skipped; the
      committed ``InvalidRequest`` 422 documents the behavior of the
      RequestValidationError handler and is verified functionally in test_api.py.
    - Response header ``required`` flags ARE compared verbatim: both the committed
      YAML and FastAPI's custom-responses emission carry ``required: true`` per
      header, so per-header requiredness is genuinely enforced.
    """
    if isinstance(node, dict):
        working = dict(node)
        if isinstance(working.get("type"), list) and "null" in working["type"]:
            non_null = [t for t in working["type"] if t != "null"]
            if len(non_null) == 1:
                branch: dict[str, Any] = {"type": non_null[0]}
                branch.update({k: v for k, v in working.items() if k in _CONSTRAINT_KEYS})
                working = {"anyOf": [branch, {"type": "null"}]} | {
                    k: v
                    for k, v in working.items()
                    if k not in ("type", *_CONSTRAINT_KEYS)
                }
            elif not non_null:
                working = {"type": "null"} | {
                    k: v for k, v in working.items() if k != "type"
                }
        cleaned: dict[str, Any] = {}
        for key, value in working.items():
            if key in (
                "title",
                "default",
                "format",
                "servers",
                "security",
                "securitySchemes",
                "openapi",
            ):
                continue
            if key == "oneOf":
                key = "anyOf"
            if key == "type" and ("const" in working or "enum" in working):
                continue
            cleaned[key] = _normalize(value)
        return _sort(cleaned)
    if isinstance(node, list):
        return [_normalize(i) for i in node]
    return node


def _without_422(responses: dict[str, Any]) -> dict[str, Any]:
    """Skip FastAPI's automatic 422 response; see the _normalize docstring."""
    return {k: v for k, v in responses.items() if k != "422"}


def _strip(node: Any) -> Any:
    """Keep paths, parameters, responses, and schemas; drop non-contract noise."""
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_strip(i) for i in node]
    return node


def test_openapi_matches_committed_contract() -> None:
    committed = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    generated = create_app().openapi()

    committed_components = committed["components"]
    generated_components = generated["components"]
    committed_schemas = committed_components["schemas"]
    generated_schemas = generated_components["schemas"]
    committed_paths = _strip(committed["paths"])
    generated_paths = _strip(generated["paths"])

    for path, methods in committed_paths.items():
        assert path in generated_paths, f"missing path {path}"
        for operation, spec in methods.items():
            assert operation in generated_paths[path], f"missing operation {path} {operation}"
            assert spec["summary"] == generated_paths[path][operation]["summary"], path
            resolved_committed = _resolve(spec["responses"], committed_components)
            resolved_generated = _resolve(
                generated_paths[path][operation]["responses"], generated_components
            )
            assert _normalize(_without_422(resolved_committed)) == _normalize(
                _without_422(resolved_generated)
            ), path
            for parameter in spec.get("parameters", []):
                in_generated = next(
                    (
                        p
                        for p in generated_paths[path][operation].get("parameters", [])
                        if p["name"] == parameter["name"] and p["in"] == parameter["in"]
                    ),
                    None,
                )
                assert in_generated is not None, f"missing parameter {parameter['name']} in {path}"
                assert _normalize(_resolve(parameter, committed_components)) == _normalize(
                    _resolve(in_generated, generated_components)
                ), parameter

    for schema_name, schema in committed_schemas.items():
        assert schema_name in generated_schemas, f"missing schema {schema_name}"
        assert _normalize(_resolve(schema, committed_components)) == _normalize(
            _resolve(generated_schemas[schema_name], generated_components)
        ), schema_name

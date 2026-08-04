"""Committed JSON Schema must match the schema generated from canonical models."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from hyperion.models import Catalog

SCHEMA_PATH = Path(__file__).parents[2] / "schema" / "services.schema.json"


def _resolve_refs(node: Any, defs: dict[str, Any]) -> Any:
    """Expand every $ref against the committed $defs so structures compare inline."""
    if isinstance(node, dict):
        if "$ref" in node:
            name = node["$ref"].removeprefix("#/$defs/")
            resolved = _resolve_refs(deepcopy(defs[name]), defs)
            extra = {k: v for k, v in node.items() if k != "$ref"}
            if extra:
                merged = deepcopy(resolved)
                merged.update({k: _resolve_refs(v, defs) for k, v in extra.items()})
                return merged
            return resolved
        return {k: _resolve_refs(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_refs(i, defs) for i in node]
    return node


def _sort(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _sort(v) for k, v in sorted(node.items())}
    if isinstance(node, list):
        return [_sort(i) for i in node]
    return node


def _normalize(node: Any) -> Any:
    """Drop purely cosmetic keys (title, format, $schema, $id) and canonicalize ordering.

    Everything else — `default`, `enum`, `const`, `type`, `oneOf`, `anyOf`,
    `allOf`, `required`, and `$ref` strings — is compared verbatim so drift in
    defaults or enum/const values fails the parity gate.
    """
    if isinstance(node, dict):
        cleaned: dict[str, Any] = {}
        for key, value in node.items():
            if key in ("title", "format", "$schema", "$id"):
                continue
            cleaned[key] = _normalize(value)
        return _sort(cleaned)
    if isinstance(node, list):
        return [_normalize(i) for i in node]
    return node


def test_catalog_schema_matches_committed_contract() -> None:
    committed = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    generated = TypeAdapter(Catalog).json_schema()
    assert _normalize(generated) == _normalize(committed)

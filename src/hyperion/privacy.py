"""Shared disclosure rules for selected process metadata and MCP evidence text."""

import re

_REDACTIONS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+"),
    re.compile(r"(?i)((?:password|passwd|secret|token|api[_-]?key)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[A-Za-z0-9._~+/-]+"),
    re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,})\b"
    ),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
)


def redact(value: str, limit: int = 4096) -> str:
    for pattern in _REDACTIONS:
        value = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + "[REDACTED]", value)
    return value[:limit]

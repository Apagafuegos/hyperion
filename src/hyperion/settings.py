"""Environment configuration; the full v1 surface from TECHNICAL-DESIGN.md 12.2."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    catalog_path: Path
    docker_host: str
    bind_host: str
    bind_port: int
    log_level: str
    fixture_mode: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        def _env(name: str, default: str) -> str:
            return os.environ.get(name, default)

        return cls(
            catalog_path=Path(_env("HYPERION_CATALOG_PATH", "/etc/hyperion/services.yaml")),
            docker_host=_env("HYPERION_DOCKER_HOST", "tcp://127.0.0.1:2375"),
            bind_host=_env("HYPERION_BIND_HOST", "127.0.0.1"),
            bind_port=int(_env("HYPERION_BIND_PORT", "8787")),
            log_level=_env("HYPERION_LOG_LEVEL", "INFO"),
            fixture_mode=_env("HYPERION_FIXTURE_MODE", "") == "1",
        )

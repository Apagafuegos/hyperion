"""Environment configuration; the full v1 surface from TECHNICAL-DESIGN.md 12.2
extended for the operations-console phases."""

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
    state_dir: Path = Path("/var/lib/hyperion")
    ops_socket_path: Path = Path("/run/hyperion/ops.sock")
    host_sample_interval: float = 7.0
    host_history_seconds: int = 1800
    activity_retention_days: int = 90
    managed_unit_dir: Path = Path("/etc/systemd/system")

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
            state_dir=Path(_env("HYPERION_STATE_DIR", "/var/lib/hyperion")),
            ops_socket_path=Path(_env("HYPERION_OPS_SOCKET", "/run/hyperion/ops.sock")),
            host_sample_interval=float(_env("HYPERION_HOST_SAMPLE_INTERVAL", "7")),
            host_history_seconds=int(_env("HYPERION_HOST_HISTORY_SECONDS", "1800")),
            activity_retention_days=int(_env("HYPERION_ACTIVITY_RETENTION_DAYS", "90")),
            managed_unit_dir=Path(
                _env("HYPERION_MANAGED_UNIT_DIR", "/etc/systemd/system")
            ),
        )

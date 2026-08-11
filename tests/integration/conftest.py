"""Integration fixtures: app assembled from fixture providers."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hyperion.main import create_app
from hyperion.settings import Settings

FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.fixture
def client(tmp_path) -> TestClient:
    settings = Settings.from_env()
    app = create_app(
        settings=Settings(
            catalog_path=FIXTURES / "fixture-services.yaml",
            docker_host=settings.docker_host,
            bind_host=settings.bind_host,
            bind_port=settings.bind_port,
            log_level=settings.log_level,
            fixture_mode=True,
            state_dir=tmp_path / "state",
            managed_unit_dir=tmp_path / "systemd",
        )
    )
    with TestClient(app) as client:
        yield client

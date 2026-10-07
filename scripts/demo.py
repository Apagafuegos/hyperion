"""Run the real console with disposable fixture evidence for portfolio recording.

This launcher binds only to loopback. Its authentication middleware belongs to
this process, never the production app. Every run gets fresh activity/state and
an unreachable operations socket, so no host operation can be dispatched.
"""
from __future__ import annotations

import argparse
import os
import secrets
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from fastapi import Request
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from hyperion.main import create_app
from hyperion.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8792)
    args = parser.parse_args()
    os.environ["MANAGEMENT_PROXY_SECRET"] = secrets.token_urlsafe(32)
    with TemporaryDirectory(prefix="hyperion-demo-") as state:
        settings = replace(
            Settings.from_env(),
            fixture_mode=True,
            catalog_path=ROOT / "tests/fixtures/fixture-services.yaml",
            state_dir=Path(state),
            managed_unit_dir=Path(state) / "systemd",
            ops_socket_path=Path(state) / "no-host-operations.sock",
        )
        app = create_app(settings)

        @app.middleware("http")
        async def fixture_identity(
            request: Request, call_next: RequestResponseEndpoint
        ) -> Response:
            injected = {
                b"x-authentik-username": b"portfolio-demo",
                b"x-authentik-groups": b"authentik Admins",
                b"x-management-proxy-secret": os.environ["MANAGEMENT_PROXY_SECRET"].encode(),
            }
            request.scope["headers"] = [
                (key, value) for key, value in request.scope["headers"] if key not in injected
            ] + list(injected.items())
            return await call_next(request)

        print(f"Portfolio fixtures: http://127.0.0.1:{args.port}/overview", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()

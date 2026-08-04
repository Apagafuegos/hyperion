"""Allow `uv run python -m hyperion` for quick local starts."""

import uvicorn

from .settings import Settings

if __name__ == "__main__":
    settings = Settings.from_env()
    uvicorn.run(
        "hyperion.main:app",
        host=settings.bind_host,
        port=settings.bind_port,
        workers=1,
    )

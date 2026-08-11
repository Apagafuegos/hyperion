"""Standalone privileged operation helper entrypoint.

Run as root (or a dedicated ops user) to expose the typed operation boundary
over a Unix socket:

    sudo python -m hyperion.ops.server --socket /run/hyperion/ops.sock
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from . import serve_ops_socket


def _allowlist_from_file(path: Path) -> set[str]:
    if not path.exists():
        return set()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return set()
    return {str(entry) for entry in raw if isinstance(entry, str)}


async def _main(
    socket_path: Path,
    allowlist_path: Path | None,
    caller_uid: int,
    managed_dir: Path,
) -> None:
    allowed = _allowlist_from_file(allowlist_path) if allowlist_path else None
    await serve_ops_socket(
        socket_path,
        allowed_units=allowed,
        caller_uid=caller_uid,
        managed_dir=managed_dir,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Hyperion privileged operation helper")
    parser.add_argument("--socket", type=Path, default=Path("/run/hyperion/ops.sock"))
    parser.add_argument(
        "--allowlist", type=Path, default=None, help="JSON list of operable unit names"
    )
    parser.add_argument(
        "--caller-uid",
        type=int,
        default=1000,
        help="UID of the Hyperion web process that may connect",
    )
    parser.add_argument(
        "--managed-dir",
        type=Path,
        default=Path("/etc/systemd/system/hyperion-hyperion.d"),
        help="Directory where Hyperion-managed unit files may be written",
    )
    args = parser.parse_args()
    asyncio.run(_main(args.socket, args.allowlist, args.caller_uid, args.managed_dir))


if __name__ == "__main__":
    main()

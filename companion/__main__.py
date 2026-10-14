"""Run the companion: python -m companion."""

import asyncio
import os

from companion.socket import serve_companion
from companion.store import open_store, store_path


def main():
    path = store_path()
    with open_store(path) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
    port = int(os.environ.get("LEETOTTER_PORT", "8765"))
    print(f"Leetotter companion: store {path}, schema version {version}; WebSocket 127.0.0.1:{port}", flush=True)
    asyncio.run(_run(port))


async def _run(port):
    async with serve_companion(port):
        await asyncio.Future()


if __name__ == "__main__":
    main()

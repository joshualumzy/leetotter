"""Run the companion: python -m companion."""

import asyncio
import logging
import logging.handlers
import os
import sys
from pathlib import Path

from companion.socket import serve_companion
from companion.store import open_store, snapshot_store, store_path

logger = logging.getLogger(__name__)


def configure_logging(path, max_bytes=5_000_000):
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=3, encoding="utf-8"
        )
    else:
        handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def _log_uncaught(exc_type, exc_value, traceback):
    logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, traceback))
    sys.__excepthook__(exc_type, exc_value, traceback)


def main():
    configure_logging(os.environ.get("LEETOTTER_LOG_PATH"))
    sys.excepthook = _log_uncaught
    path = store_path()
    snapshot_store(path, "start")
    with open_store(path) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
    port = int(os.environ.get("LEETOTTER_PORT", "8765"))
    message = f"Leetotter companion: store {path}, schema version {version}; WebSocket 127.0.0.1:{port}"
    print(message, flush=True)
    logger.info(message)
    asyncio.run(_run(port))


async def _run(port):
    async with serve_companion(port):
        await asyncio.Future()


if __name__ == "__main__":
    main()

import asyncio
import base64
import hashlib
import json
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from companion.socket import ALLOWED_ORIGIN, serve_companion


def test_allowed_origin_matches_the_manifest_public_key():
    manifest = json.loads((Path(__file__).parents[1] / "extension" / "manifest.json").read_text())
    digest = hashlib.sha256(base64.b64decode(manifest["key"])).digest()[:16]
    extension_id = "".join(chr(ord("a") + nibble) for byte in digest for nibble in (byte >> 4, byte & 15))
    assert ALLOWED_ORIGIN == f"chrome-extension://{extension_id}"


def test_only_extension_origin_can_connect_and_server_uses_loopback():
    async def check():
        async with serve_companion(port=0) as server:
            address = server.sockets[0].getsockname()
            assert address[0] == "127.0.0.1"
            uri = f"ws://127.0.0.1:{address[1]}"
            with pytest.raises(InvalidStatus):
                async with connect(uri, origin="https://neetcode.io", proxy=None):
                    pass
            with pytest.raises(InvalidStatus):
                async with connect(uri, proxy=None):
                    pass
            async with connect(uri, origin=ALLOWED_ORIGIN, proxy=None) as ws:
                await ws.send(json.dumps({"type": "hello", "ts": "2026-10-15T00:00:00Z"}))
                assert json.loads(await ws.recv())["type"] == "status"

    asyncio.run(check())


def test_unknown_type_gets_visible_protocol_error():
    async def check():
        async with serve_companion(port=0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}", origin=ALLOWED_ORIGIN, proxy=None) as ws:
                await ws.send(json.dumps({"type": "mystery", "ts": "2026-10-15T00:00:00Z"}))
                reply = json.loads(await ws.recv())
                assert reply["type"] == "error"
                assert "mystery" in reply["message"]
                assert "ts" in reply

    asyncio.run(check())

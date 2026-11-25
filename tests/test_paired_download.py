import asyncio
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from duet.cli.send import run_send
from duet.core.outbox import Outbox
from duet.core.pairing import PairingManager
from duet.server.app import create_app


def test_paired_phone_downloads_cli_offer(tmp_path: Path):
    async def run():
        pairing = PairingManager()
        store = Outbox(tmp_path / "offers.sqlite3")
        app = create_app(pairing, host="192.168.1.3", port=9876, outbox=store)
        async with TestClient(
            TestServer(app), headers={"Host": "192.168.1.3:9876"}
        ) as c:
            assert (await c.get("/api/outbox")).status == 401
            asset = tmp_path / "sample.bin"
            asset.write_bytes(bytes(range(255)))
            assert run_send(asset, outbox=store) == 0
            offer = pairing.issue_offer()
            assert (await c.post("/api/pair", json={"pin": offer.pin})).status == 200
            listing = await (await c.get("/api/outbox")).json()
            (item,) = listing["files"]
            assert item["name"] == asset.name
            response = await c.get(
                "/api/files/" + item["id"], headers={"Range": "bytes=123-"}
            )
            assert response.status == 206
            assert await response.read() == asset.read_bytes()[123:]

    asyncio.run(run())

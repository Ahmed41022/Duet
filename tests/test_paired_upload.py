import asyncio
import hashlib

from aiohttp.test_utils import TestClient, TestServer

from duet.core.config import Config
from duet.core.pairing import PairingManager
from duet.server.app import create_app


def test_paired_phone_publishes_into_configured_received_folder(tmp_path):
    async def run():
        pairing = PairingManager()
        config = Config(received_directory=tmp_path / "Received", max_file_size=10)
        app = create_app(pairing, host="192.168.1.3", port=9876, config=config)
        async with TestClient(
            TestServer(app), headers={"Host": "192.168.1.3:9876"}
        ) as c:
            manifest = {"name": "test.bin", "size": 3}
            assert (await c.post("/api/uploads", json=manifest)).status == 401
            offer = pairing.issue_offer()
            assert (await c.post("/api/pair", json={"pin": offer.pin})).status == 200
            assert (
                await c.post("/api/uploads", json={**manifest, "size": 11})
            ).status == 413
            response = await c.post("/api/uploads", json=manifest)
            assert response.status == 201
            url = "/api/uploads/" + (await response.json())["upload_id"]
            digest = hashlib.sha256(b"abc").hexdigest()
            assert (
                await c.put(
                    url + "/chunks/0", data=b"abc", headers={"X-Chunk-SHA256": digest}
                )
            ).status == 204
            assert (
                await c.post(url + "/complete", json={"sha256": digest})
            ).status == 200
            assert (config.received_directory / "test.bin").read_bytes() == b"abc"

    asyncio.run(run())

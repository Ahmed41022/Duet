import asyncio

from aiohttp.test_utils import TestClient, TestServer

from duet.core.pairing import PairingManager
from duet.server.app import create_app
from duet.server.text import TEXT


def test_text_is_paired_shared_in_both_directions_and_cleared_on_restart(tmp_path):
    async def run():
        pairing = PairingManager()
        app = create_app(pairing, host="192.168.1.3", port=9876, static_root=tmp_path)
        async with TestClient(
            TestServer(app), headers={"Host": "192.168.1.3:9876"}
        ) as c:
            assert (await c.get("/api/text")).status == 401
            assert (await c.get("/api/session")).status == 401
            offer = pairing.issue_offer()
            assert (await c.post("/api/pair", json={"pin": offer.pin})).status == 200
            assert (await (await c.get("/api/session")).json())["paired"]
            app[TEXT].send("from PC", source="pc")
            assert (
                await c.post("/api/text", json={"text": "from phone"})
            ).status == 201
            response = await c.get("/api/text")
            rows = (await response.json())["messages"]
            assert [(row["source"], row["text"]) for row in rows] == [
                ("pc", "from PC"),
                ("phone", "from phone"),
            ]
            assert (await c.post("/api/text", json={"text": " "})).status == 400
        fresh = create_app(pairing, host="192.168.1.3", port=9876, static_root=tmp_path)
        assert fresh[TEXT].messages() == []

    asyncio.run(run())

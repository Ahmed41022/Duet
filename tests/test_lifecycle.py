import asyncio
import hashlib
from contextlib import asynccontextmanager

from aiohttp.test_utils import TestClient, TestServer

from duet.core.config import Config
from duet.core.outbox import Outbox
from duet.core.pairing import PairingManager
from duet.core.uploads import UploadStore
from duet.server.app import create_app
from duet.server.lifecycle import install_lifecycle

HOST = "192.168.1.9"
PORT = 8765


@asynccontextmanager
async def server(tmp_path):
    pairing = PairingManager()
    app = create_app(
        pairing,
        host=HOST,
        port=PORT,
        config=Config(received_directory=tmp_path / "Received"),
        outbox=Outbox(tmp_path / "offers.sqlite"),
        heartbeat_interval=0.01,
    )
    now = [0.0]
    addresses = {HOST}
    stops = asyncio.Queue()
    address_checks = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def assigned(address):
        loop.call_soon_threadsafe(address_checks.put_nowait, None)
        return address in addresses

    tracker = install_lifecycle(
        app,
        HOST,
        60,
        stops.put_nowait,
        address_is_assigned=assigned,
        clock=lambda: now[0],
        poll_interval=0.01,
    )
    async with TestClient(
        TestServer(app), headers={"Host": f"{HOST}:{PORT}"}
    ) as client:
        yield client, pairing, tracker, now, addresses, stops, address_checks


def test_idle_stop_ignores_sse_polling_and_denials(tmp_path):
    async def run():
        async with server(tmp_path) as (client, pairing, _, now, _, stops, _):
            assert (await client.post("/api/uploads", json={})).status == 401
            offer = pairing.issue_offer()
            now[0] = 10
            assert (
                await client.post("/api/pair", json={"pin": offer.pin})
            ).status == 200
            async with client.get("/api/events") as events:
                assert await events.content.readline() == b": connected\n"
                now[0] = 69
                assert (await client.get("/api/session")).status == 200
                assert (await client.get("/api/outbox")).status == 200
                assert (
                    await client.post("/api/uploads", json={"size": -1})
                ).status >= 400
                assert stops.empty()
                now[0] = 70
                assert await asyncio.wait_for(stops.get(), 2) == "idle"

    asyncio.run(run())


def test_address_loss_signals_owner(tmp_path):
    async def run():
        async with server(tmp_path) as (_, _, _, _, addresses, stops, _):
            addresses.clear()
            assert await asyncio.wait_for(stops.get(), 2) == "address_changed"

    asyncio.run(run())


def test_transfer_survives_idle_deadline(tmp_path, monkeypatch):
    async def run():
        started = asyncio.Event()
        finish = asyncio.Event()
        put_chunk = UploadStore.put_chunk

        async def delayed_chunk(self, *args, **kwargs):
            started.set()
            await finish.wait()
            return await put_chunk(self, *args, **kwargs)

        monkeypatch.setattr(UploadStore, "put_chunk", delayed_chunk)
        async with server(tmp_path) as (
            client,
            pairing,
            tracker,
            now,
            _,
            stops,
            checks,
        ):
            offer = pairing.issue_offer()
            assert (
                await client.post("/api/pair", json={"pin": offer.pin})
            ).status == 200
            manifest = {"name": "slow.bin", "size": 3}
            response = await client.post("/api/uploads", json=manifest)
            upload_id = (await response.json())["upload_id"]
            digest = hashlib.sha256(b"abc").hexdigest()
            transfer = asyncio.create_task(
                client.put(
                    f"/api/uploads/{upload_id}/chunks/0",
                    data=b"abc",
                    headers={"X-Chunk-SHA256": digest},
                )
            )
            try:
                await asyncio.wait_for(started.wait(), 2)
                now[0] = 61
                assert not tracker.expired()
                while not checks.empty():
                    checks.get_nowait()
                await asyncio.wait_for(checks.get(), 2)
                await asyncio.wait_for(checks.get(), 2)
                assert stops.empty()
            finally:
                finish.set()
                assert (await asyncio.wait_for(transfer, 2)).status == 204
            now[0] = 120
            assert not tracker.expired()
            now[0] = 121
            assert await asyncio.wait_for(stops.get(), 2) == "idle"

    asyncio.run(run())

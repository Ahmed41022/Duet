import asyncio
from contextlib import asynccontextmanager

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from duet.core.pairing import PairingManager
from duet.server.app import EVENTS, create_app, session_for

HOST = "192.168.1.2"
PORT = 8765
AUTHORITY = f"{HOST}:{PORT}"
HEADERS = {"Host": AUTHORITY}


@asynccontextmanager
async def server(tmp_path, *, heartbeat=15, session_ttl=60):
    pairing = PairingManager(session_ttl=session_ttl)
    offer = pairing.issue_offer()
    (tmp_path / "index.html").write_text("<h1>Pair</h1>", encoding="utf-8")
    app = create_app(
        pairing,
        host=HOST,
        port=PORT,
        static_root=tmp_path,
        heartbeat_interval=heartbeat,
    )

    async def identity(request):
        return web.json_response({"id": session_for(request).id})

    app.router.add_get("/api/identity", identity)
    async with TestClient(TestServer(app), headers=HEADERS) as client:
        yield client, app[EVENTS], pairing, offer


async def pair(client, offer):
    response = await client.post("/api/pair", json={"token": offer.token})
    assert response.status == 200
    return response.cookies["duet_session"]


def test_pair_page_and_api_guards(tmp_path):
    async def check():
        async with server(tmp_path) as (client, _, pairing, offer):
            home = await client.get("/", headers={"Sec-Fetch-Site": "cross-site"})
            assert await home.text() == "<h1>Pair</h1>"
            assert home.headers["Referrer-Policy"] == "no-referrer"
            assert (
                await client.get("/", headers={"Host": "malicious.invalid"})
            ).status == 400
            assert (await client.get("/api/events")).status == 401
            assert (await client.get("/api/identity")).status == 401
            response = await client.post(
                "/api/pair",
                json={"token": offer.token},
                headers={"Origin": "http://other.invalid"},
            )
            assert response.status == 403
            assert (
                await client.post("/api/pair", data={"pin": offer.pin})
            ).status == 415
            assert (await client.post("/api/pair", json={"pin": 123456})).status == 400
            assert (
                await client.post("/api/pair", json={"pin": "a" * 4096})
            ).status == 413
            cookie = await pair(client, offer)
            assert cookie["httponly"]
            assert cookie["samesite"] == "Strict"
            assert cookie["path"] == "/"
            assert not cookie["secure"]
            assert (
                await client.post("/api/pair", json={"pin": offer.pin})
            ).status == 401
            response = await client.get("/api/identity")
            assert await response.json() == {"id": pairing.devices()[0].id}
            response = await client.get(
                "/api/identity", headers={"Sec-Fetch-Site": "cross-site"}
            )
            assert response.status == 403
            assert pairing.revoke(pairing.devices()[0].id)
            assert (await client.get("/api/identity")).status == 401

    asyncio.run(check())


def test_pairing_lockout_returns_retry_after(tmp_path):
    async def check():
        async with server(tmp_path) as (client, _, _, offer):
            for _ in range(4):
                assert (
                    await client.post("/api/pair", json={"pin": "wrong"})
                ).status == 401
            response = await client.post("/api/pair", json={"pin": "wrong"})
            assert response.status == 429
            assert response.headers["Retry-After"] == "60"
            assert (
                await client.post("/api/pair", json={"pin": offer.pin})
            ).status == 429

    asyncio.run(check())


def test_events_are_session_scoped_and_stop_on_revocation(tmp_path):
    async def check():
        async with server(tmp_path) as (client, hub, pairing, offer):
            await pair(client, offer)
            (session,) = pairing.devices()
            stream = await client.get("/api/events")
            assert stream.headers["Content-Type"] == "text/event-stream"
            assert stream.headers["Cache-Control"] == "no-store"
            assert await stream.content.readexactly(13) == b": connected\n\n"
            with pytest.raises(ValueError):
                hub.publish("bad\nevent", {})
            hub.publish("ignored", {}, session_id="other")
            hub.publish("text", {"text": "a\nb"}, session_id=session.id)
            expected = b'event: text\ndata: {"text":"a\\nb"}\n\n'
            assert (
                await asyncio.wait_for(stream.content.readexactly(len(expected)), 2)
                == expected
            )
            assert pairing.revoke(session.id)
            assert await asyncio.wait_for(stream.read(), 2) == b""

    asyncio.run(check())


def test_open_stream_checks_expiry(tmp_path):
    async def check():
        async with server(tmp_path, session_ttl=0.2) as (client, _, _, offer):
            await pair(client, offer)
            stream = await client.get("/api/events")
            assert stream.status == 200
            assert await asyncio.wait_for(stream.read(), 2) == b": connected\n\n"
            assert (await client.get("/api/events")).status == 401

    asyncio.run(check())

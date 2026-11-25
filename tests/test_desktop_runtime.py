import asyncio
import hashlib
import socket
import time

import pytest
from aiohttp import ClientSession, CookieJar, web

import duet.desktop_runtime as runtime_module
from duet.core.history import HistoryStore, TransferObserver
from duet.core.outbox import Outbox
from duet.desktop_runtime import DesktopRuntime


def _wait_for(runtime, predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = runtime.snapshot()
        if predicate(state):
            return state
        time.sleep(0.02)
    pytest.fail(f"Snapshot did not reach expected state: {runtime.snapshot()}")


def test_runtime_control_and_http_activity(tmp_path, monkeypatch):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    host = "192.168.123.45"
    site = web.TCPSite
    monkeypatch.setattr(runtime_module, "select_bind_address", lambda _: host)
    monkeypatch.setattr(
        runtime_module.web,
        "TCPSite",
        lambda runner, **kwargs: site(runner, "127.0.0.1", kwargs["port"]),
    )
    runtime = DesktopRuntime(data_directory=tmp_path, port=port, offer_ttl=2)
    received = tmp_path / "Received"
    source = tmp_path / "offered.bin"
    source.write_bytes(b"duet")

    async def exercise():
        state = runtime.snapshot()
        assert state["running"] and state["url"] == f"http://{host}:{port}"
        client_url = f"http://127.0.0.1:{port}"
        async with ClientSession(
            base_url=client_url,
            cookie_jar=CookieJar(unsafe=True),
            headers={"Host": f"{host}:{port}"},
        ) as phone:
            response = await phone.post("/api/pair", json={"token": state["token"]})
            assert response.status == 200
            fresh = runtime.snapshot()
            assert fresh["token"] != state["token"]
            assert len(fresh["devices"]) == 1

            await asyncio.wrap_future(runtime.send_text("from PC"))
            assert (await (await phone.get("/api/text")).json())["messages"][0][
                "text"
            ] == "from PC"
            assert (
                await phone.post("/api/text", json={"text": "from phone"})
            ).status == 201

            response = await phone.post(
                "/api/uploads", json={"name": "received.bin", "size": 3}
            )
            assert response.status == 201
            upload_id = (await response.json())["upload_id"]
            upload = f"/api/uploads/{upload_id}"
            transfer = await asyncio.to_thread(
                _wait_for, runtime, lambda s: bool(s["transfers"])
            )
            assert transfer["transfers"][0]["direction"] == "upload"
            digest = hashlib.sha256(b"abc").hexdigest()
            assert (
                await phone.put(
                    upload + "/chunks/0",
                    data=b"abc",
                    headers={"X-Chunk-SHA256": digest},
                )
            ).status == 204
            assert (
                await phone.post(upload + "/complete", json={"sha256": digest})
            ).status == 200
            assert (received / "received.bin").read_bytes() == b"abc"

            response = await phone.get("/api/outbox")
            assert response.status == 200
            identity = Outbox(tmp_path / "outbox.sqlite3").list()[0].id
            response = await phone.get(f"/api/files/{identity}")
            assert response.status == 200
            assert await response.read() == b"duet"

            await asyncio.wrap_future(runtime.revoke(fresh["devices"][0]["id"]))
            assert (await phone.get("/api/text")).status == 401

    try:
        assert runtime.snapshot()["running"] is False
        assert not (tmp_path / "history.sqlite3").exists()
        runtime.configure(received, 125, host).result(5)
        runtime.offer(source).result(5)
        assert Outbox(tmp_path / "outbox.sqlite3").list()[0].name == source.name
        runtime.start().result(5)
        asyncio.run(exercise())
        state = _wait_for(
            runtime, lambda s: len(s["history"]) == 2 and len(s["messages"]) == 2
        )
        assert {row["direction"]: row["name"] for row in state["history"]} == {
            "upload": "received.bin",
            "download": source.name,
        }
        assert [message["direction"] for message in state["messages"]] == [
            "outgoing",
            "incoming",
        ]
        next_offer = _wait_for(runtime, lambda s: s["token"] != state["token"])
        assert next_offer["token"]
        runtime.stop().result(5)
        assert runtime.snapshot()["token"] is None
        assert runtime.snapshot()["running"] is False
    finally:
        runtime.close()
    assert not runtime._thread.is_alive()

    reopened = DesktopRuntime(data_directory=tmp_path)
    try:
        state = _wait_for(reopened, lambda s: s["idle_timeout_seconds"] == 125)
        assert state["received_directory"] == str(received)
        assert state["bind"] == host
        history = _wait_for(reopened, lambda s: len(s["history"]) == 2)["history"]
        assert {row["direction"]: row["size"] for row in history} == {
            "upload": 3,
            "download": 4,
        }
    finally:
        reopened.close()


def test_history_tracks_completed_and_cancelled_transfers(tmp_path):
    async def exercise():
        history = HistoryStore(tmp_path / "history.sqlite3")
        observer = TransferObserver(history)
        assert not history.database.exists()
        observer.update("one", "file", "upload", 2, 8)
        assert observer.active()[0]["progress"] == 0.25
        observer.finish("one", "completed")
        observer.finish("one", "completed")
        observer.update("two", "second", "download", 1, 2)
        await observer.flush()
        assert {row["id"]: row["status"] for row in history.list()} == {
            "one": "completed",
            "two": "cancelled",
        }
        assert observer.active() == []

    asyncio.run(exercise())


def test_runtime_refreshes_external_storage_and_closes_queued_work(tmp_path):
    runtime = DesktopRuntime(data_directory=tmp_path)
    other_process = Outbox(tmp_path / "outbox.sqlite3")
    disk_file = tmp_path / "external.txt"
    disk_file.write_text("external", encoding="utf-8")
    try:
        offer = other_process.offer(disk_file)
        state = _wait_for(runtime, lambda s: bool(s["outbox"]))
        assert state["outbox"][0]["id"] == offer.id
        other_process.revoke(offer.id)
        _wait_for(runtime, lambda s: not s["outbox"])

        configuration = runtime.configure(tmp_path / "Incoming", 60)
    finally:
        runtime.close()
    assert configuration.done()
    assert runtime.snapshot()["idle_timeout_seconds"] == 60

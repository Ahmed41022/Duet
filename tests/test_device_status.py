import asyncio
import json
import os
import sys
import time

import psutil
from aiohttp.test_utils import TestClient, TestServer

from duet.cli import main
from duet.core.device_status import DeviceStatus, list_devices
from duet.core.pairing import PairingManager
from duet.server.app import create_app


async def _wait_for(directory, *, count):
    async with asyncio.timeout(3):
        while True:
            devices = list_devices(directory)
            if len(devices) == count:
                return devices
            await asyncio.sleep(0.02)


def test_pairing_status_lifecycle(tmp_path):
    async def exercise():
        pairing = PairingManager(session_ttl=1)
        directory = tmp_path / "devices"
        status = DeviceStatus(directory)
        host = "192.168.55.7"
        app = create_app(pairing, host=host, port=8765, device_status=status)
        async with TestClient(TestServer(app), headers={"Host": f"{host}:8765"}) as pc:
            assert list_devices(directory) == []
            offer = pairing.issue_offer()
            response = await pc.post("/api/pair", json={"pin": offer.pin})
            assert response.status == 200
            devices = await _wait_for(directory, count=1)
            saved_file = next(directory.glob("*.json"))
            content = saved_file.read_text(encoding="utf-8")
            assert offer.token not in content
            assert response.cookies["duet_session"].value not in content
            assert "pin" not in json.loads(content)
            assert pairing.revoke(devices[0]["id"])
            status.changed()
            await _wait_for(directory, count=0)

            offer = pairing.issue_offer()
            response = await pc.post("/api/pair", json={"token": offer.token})
            assert response.status == 200
            await _wait_for(directory, count=1)
            await _wait_for(directory, count=0)

            offer = pairing.issue_offer()
            response = await pc.post("/api/pair", json={"token": offer.token})
            assert response.status == 200
            await _wait_for(directory, count=1)
        assert list_devices(directory) == []
        assert not list(directory.glob("*.json"))

    asyncio.run(exercise())


def test_cli_filters_stale_status_and_only_reads(tmp_path, monkeypatch, capsys):
    import duet.core.device_status as status_module

    directory = tmp_path / "devices"
    monkeypatch.setattr(status_module, "default_database", lambda: tmp_path / "outbox")
    monkeypatch.setattr(sys, "argv", ["duet", "devices"])
    main()
    assert capsys.readouterr().out == "No paired devices.\n"
    assert not directory.exists()
    directory.mkdir()
    now = time.monotonic()
    state = {
        "pid": os.getpid(),
        "started": psutil.Process().create_time(),
        "lease": now + 60,
        "devices": [{"id": "paired", "address": "192.168.1.2", "expires": now + 60}],
    }
    (directory / "valid.json").write_text(json.dumps(state), encoding="utf-8")
    stale = [
        {**state, "pid": 2**30},
        {**state, "started": 0},
        {**state, "lease": now - 1},
        {**state, "devices": [{**state["devices"][0], "expires": now - 1}]},
    ]
    for index, entry in enumerate(stale):
        (directory / f"{index}.json").write_text(json.dumps(entry), encoding="utf-8")
    main()
    assert capsys.readouterr().out == "paired 192.168.1.2\n"
    assert len(list(directory.glob("*.json"))) == 5

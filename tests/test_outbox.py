import asyncio
import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import quote

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from duet.core.outbox import Outbox
from duet.server.outbox import register_outbox


class Events:
    def __init__(self):
        self.messages = asyncio.Queue()

    def publish(self, event, data):
        self.messages.put_nowait((event, data))


def client(outbox, events=None):
    app = web.Application()
    register_outbox(app, outbox=outbox, events=events or Events(), poll_interval=0.01)
    return TestClient(TestServer(app))


def test_file_download_and_resume(tmp_path):
    outbox = Outbox(tmp_path / "outbox.sqlite3")
    file = tmp_path / "ملف test.bin"
    data = bytes(range(256)) * 3000
    file.write_bytes(data)
    offer = outbox.offer(file)

    async def check():
        async with client(outbox) as phone:
            listing = await phone.get("/api/outbox")
            assert await listing.json() == {"files": [offer.public()]}
            url = f"/api/files/{offer.id}"
            head = await phone.head(url)
            assert head.status == 200
            assert head.headers["Content-Length"] == str(len(data))
            assert head.headers["Accept-Ranges"] == "bytes"
            assert quote(file.name) in head.headers["Content-Disposition"]
            assert await head.read() == b""
            split = 262_200
            first = await phone.get(url, headers={"Range": f"bytes=0-{split - 1}"})
            assert first.status == 206
            assert first.headers["Content-Range"] == f"bytes 0-{split - 1}/{len(data)}"
            second = await phone.get(
                url,
                headers={"Range": f"bytes={split}-", "If-Range": head.headers["ETag"]},
            )
            assert second.status == 206
            assert await first.read() + await second.read() == data
            suffix = await phone.get(
                url,
                headers={
                    "Range": "bytes=-6",
                    "If-Range": head.headers["Last-Modified"],
                },
            )
            assert suffix.status == 206
            assert await suffix.read() == data[-6:]
            stale = await phone.get(
                url, headers={"Range": "bytes=1-2", "If-Range": '"old"'}
            )
            assert stale.status == 200
            assert await stale.read() == data
            too_far = await phone.get(url, headers={"Range": f"bytes={len(data)}-"})
            assert too_far.status == 416
            assert too_far.headers["Content-Range"] == f"bytes */{len(data)}"
            file.write_bytes(b"replacement")
            changed = await phone.get(url)
            assert changed.status == 410
            assert str(file) not in await changed.text()

    asyncio.run(check())


def test_folder_download_zip64_and_resume(tmp_path):
    outbox = Outbox(tmp_path / "outbox.sqlite3")
    directory = tmp_path / "folder"
    directory.mkdir()
    (directory / "empty").mkdir()
    (directory / "ملف.txt").write_bytes(b"generated fixture")
    (directory / "nested").mkdir()
    (directory / "nested" / "another.bin").write_bytes(bytes(range(256)))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"not inside folder")
    try:
        (directory / "shortcut").symlink_to(outside, target_is_directory=True)
    except OSError:
        pass
    offer = outbox.offer(directory)

    async def check():
        async with client(outbox) as phone:
            url = f"/api/files/{offer.id}"
            whole = await phone.get(url)
            assert whole.content_type == "application/zip"
            blob = await whole.read()
            assert whole.headers["Content-Length"] == str(len(blob))
            assert b"PK\x06\x06" in blob
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                assert set(archive.namelist()) == {
                    "folder/",
                    "folder/empty/",
                    "folder/ملف.txt",
                    "folder/nested/",
                    "folder/nested/another.bin",
                }
                assert archive.read("folder/ملف.txt") == b"generated fixture"
                assert archive.testzip() is None
            resumed = await phone.get(
                url, headers={"Range": "bytes=111-", "If-Range": whole.headers["ETag"]}
            )
            assert resumed.status == 206
            assert await resumed.read() == blob[111:]
            (directory / "ملف.txt").write_bytes(b"changed")
            assert (await phone.get(url)).status == 410

    asyncio.run(check())


def test_cli_process_updates_running_server_and_revoke(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "LOCALAPPDATA" if os.name == "nt" else "XDG_DATA_HOME", str(tmp_path)
    )
    source_root = Path(__file__).resolve().parents[1] / "src"
    monkeypatch.setenv("PYTHONPATH", str(source_root))
    file = tmp_path / "file.txt"
    file.write_text("generated", encoding="utf-8")
    outbox = Outbox()

    async def check():
        events = Events()
        async with client(outbox, events) as phone:
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    "-c",
                    "import sys; from duet.cli.send import run_send; "
                    "raise SystemExit(run_send(sys.argv[1]))",
                    str(file),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            assert "file.txt" in result.stdout
            assert await asyncio.wait_for(events.messages.get(), 2) == ("outbox", {})
            response = await phone.get("/api/outbox")
            offers = (await response.json())["files"]
            assert len(offers) == 1
            identity = offers[0]["id"]
            assert outbox.revoke(identity)
            assert await asyncio.wait_for(events.messages.get(), 2) == ("outbox", {})
            assert (await phone.get(f"/api/files/{identity}")).status == 404

    asyncio.run(check())


def test_refuses_symbolic_link_and_detects_replacement(tmp_path):
    outbox = Outbox(tmp_path / "outbox.sqlite3")
    file = tmp_path / "original.txt"
    file.write_bytes(b"before")
    offer = outbox.offer(file)
    replacement = tmp_path / "new.txt"
    replacement.write_bytes(b"after!")
    replacement.replace(file)
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(file)
    except OSError:
        link = None
    if link is not None:
        with pytest.raises(ValueError, match="original"):
            outbox.offer(link)

    async def check():
        async with client(outbox) as phone:
            assert (await phone.get(f"/api/files/{offer.id}")).status == 410

    asyncio.run(check())

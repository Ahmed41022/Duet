from __future__ import annotations

import asyncio
import errno
import hashlib
import random
import shutil
import threading

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestClient, TestServer

from duet.core.uploads import DEFAULT_CHUNK_SIZE, UploadStore, clean_name
from duet.server.uploads import register_uploads

_AUTH = {"X-Test-Device": "phone-1"}


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _app(store: UploadStore) -> web.Application:
    app = web.Application()
    register_uploads(app, store, lambda request: request.headers.get("X-Test-Device"))
    return app


async def _create(client: TestClient, name: str, size: int, **fields: object) -> str:
    response = await client.post(
        "/api/uploads", headers=_AUTH, json={"name": name, "size": size, **fields}
    )
    assert response.status == 201, await response.text()
    return f"/api/uploads/{(await response.json())['upload_id']}"


async def _put(
    client: TestClient, base: str, index: int, data: bytes, checksum: str | None = None
) -> int:
    response = await client.put(
        f"{base}/chunks/{index}",
        headers={**_AUTH, "X-Chunk-SHA256": checksum or _hash(data)},
        data=data,
    )
    async with response:
        await response.read()
        return response.status


def test_200_mib_upload_resumes_after_disconnected_socket(tmp_path):
    async def run():
        received = tmp_path / "Received"
        source = tmp_path / "source.bin"
        expected_hash = hashlib.sha256()
        random_source = random.Random(42)
        size = 200 * 1024**2
        count = size // DEFAULT_CHUNK_SIZE
        with source.open("wb") as output:
            for _ in range(count):
                chunk = random_source.randbytes(DEFAULT_CHUNK_SIZE)
                expected_hash.update(chunk)
                output.write(chunk)

        started = asyncio.Event()

        @web.middleware
        async def detect_disconnect_request(request, handler):
            if request.headers.get("X-Test-Disconnect"):
                started.set()

            return await handler(request)

        app = _app(UploadStore(received))
        app.middlewares.append(detect_disconnect_request)
        async with TestClient(TestServer(app)) as client:
            base = await _create(
                client, "large.bin", size, sha256=expected_hash.hexdigest()
            )
            with source.open("rb") as original:
                for index in range(count // 2):
                    assert (
                        await _put(
                            client, base, index, original.read(DEFAULT_CHUNK_SIZE)
                        )
                        == 204
                    )
                chunk = original.read(DEFAULT_CHUNK_SIZE)
                _, writer = await asyncio.open_connection(
                    client.server.host, client.server.port
                )
                try:
                    writer.write(
                        (
                            f"PUT {base}/chunks/{count // 2} HTTP/1.1\r\n"
                            "Host: localhost\r\n"
                            "X-Test-Device: phone-1\r\n"
                            "X-Test-Disconnect: true\r\n"
                            f"Content-Length: {len(chunk)}\r\n"
                            f"X-Chunk-SHA256: {_hash(chunk)}\r\n\r\n"
                        ).encode("ascii")
                        + chunk[:65536]
                    )
                    await writer.drain()
                    await asyncio.wait_for(started.wait(), timeout=5)
                finally:
                    writer.transport.abort()

                # A fresh client reconnects with the same device identity.
                async with ClientSession() as other:
                    async with other.get(
                        client.make_url(base), headers=_AUTH
                    ) as status:
                        manifest = await status.json()
                        assert manifest["chunk_size"] == DEFAULT_CHUNK_SIZE
                        assert manifest["received"] == list(range(count // 2))
                    assert not (received / "large.bin").exists()
                    original.seek(len(chunk) * (count // 2))
                    for index in range(count // 2, count):
                        data = original.read(DEFAULT_CHUNK_SIZE)
                        async with other.put(
                            client.make_url(f"{base}/chunks/{index}"),
                            headers={**_AUTH, "X-Chunk-SHA256": _hash(data)},
                            data=data,
                        ) as response:
                            assert response.status == 204, await response.text()
                    async with other.post(
                        client.make_url(base + "/complete"), headers=_AUTH
                    ) as response:
                        assert response.status == 200, await response.text()
                        assert (await response.json())[
                            "sha256"
                        ] == expected_hash.hexdigest()

            with (
                source.open("rb") as original,
                (received / "large.bin").open("rb") as saved,
            ):
                for _ in range(count):
                    assert original.read(DEFAULT_CHUNK_SIZE) == saved.read(
                        DEFAULT_CHUNK_SIZE
                    )
                assert saved.read(1) == b""
            assert list(received.glob("*.part")) == []

    asyncio.run(run())


def test_auth_integrity_and_retries(tmp_path):
    async def run():
        async with TestClient(
            TestServer(_app(UploadStore(tmp_path, chunk_size=4)))
        ) as client:
            assert (await client.post("/api/uploads", json={})).status == 401
            base = await _create(client, "test.bin", 5)
            for method, path in [
                ("GET", ""),
                ("PUT", "/chunks/0"),
                ("POST", "/complete"),
            ]:
                response = await client.request(method, base + path)
                assert response.status == 401
                response = await client.request(
                    method, base + path, headers={"X-Test-Device": "phone-2"}
                )
                assert response.status == 404
            assert (await client.post(base + "/complete", headers=_AUTH)).status == 409
            assert await _put(client, base, 0, b"ABC") == 400
            assert await _put(client, base, 0, b"ABCD", _hash(b"1111")) == 422
            assert await _put(client, base, 2, b"A") == 400
            assert await _put(client, base, 1, b"E") == 204
            assert await _put(client, base, 0, b"ABCD") == 204
            assert await _put(client, base, 0, b"ABCD") == 204
            assert await _put(client, base, 0, b"2222") == 409
            invalid = await client.post(
                base + "/complete", headers=_AUTH, json={"sha256": "0" * 64}
            )
            assert invalid.status == 422
            assert not (tmp_path / "test.bin").exists()
            response = await client.post(
                base + "/complete", headers=_AUTH, json={"sha256": _hash(b"ABCDE")}
            )
            assert response.status == 200
            assert await response.json() == {
                "name": "test.bin",
                "relpath": "test.bin",
                "size": 5,
                "sha256": _hash(b"ABCDE"),
            }
            retry = await client.post(base + "/complete", headers=_AUTH)
            assert await retry.json() == await response.json()
            assert await _put(client, base, 0, b"ABCD") == 204
            assert (tmp_path / "test.bin").read_bytes() == b"ABCDE"
            assert [p.name for p in tmp_path.iterdir()] == ["test.bin"]

    asyncio.run(run())


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("..", "file"),
        ("C:\\temp\\evil.txt", "C__temp_evil.txt"),
        ("../../escape.txt", "_.._escape.txt"),
        ("NUL.txt", "_NUL.txt"),
        ("cOm¹.txt. ", "_cOm¹.txt"),
        ("photo\x00\x01?.jpg", "photo___.jpg"),
        ("مرحبا.txt", "مرحبا.txt"),
    ],
)
def test_clean_name(name, expected):
    assert clean_name(name) == expected


def test_folder_collisions_and_path_rejection(tmp_path):
    async def run():
        async with TestClient(
            TestServer(_app(UploadStore(tmp_path, chunk_size=4)))
        ) as client:
            for relpath in (
                "../a.txt",
                "/a.txt",
                "x//a.txt",
                "C:/a.txt",
                "x\\a.txt",
                "x/b.txt",
            ):
                response = await client.post(
                    "/api/uploads",
                    headers=_AUTH,
                    json={"name": "a.txt", "size": 0, "relpath": relpath},
                )
                assert response.status == 400
            name = "NUL.txt"
            relpath = f"مرحبا/{name}"
            first, second = await asyncio.gather(
                _create(client, name, 4, relpath=relpath),
                _create(client, name, 4, relpath=relpath),
            )
            assert await _put(client, first, 0, b"1111") == 204
            assert await _put(client, second, 0, b"2222") == 204
            folder = tmp_path / "مرحبا"
            folder.mkdir()
            (folder / "_NUL.txt").write_bytes(b"existing")
            responses = await asyncio.gather(
                *[
                    client.post(base + "/complete", headers=_AUTH)
                    for base in (first, second)
                ]
            )
            assert [response.status for response in responses] == [200, 200]
            actual = {path.name: path.read_bytes() for path in folder.iterdir()}
            assert actual.pop("_NUL.txt") == b"existing"
            assert set(actual) == {"_NUL (1).txt", "_NUL (2).txt"}
            assert set(actual.values()) == {b"1111", b"2222"}

            name = "م" * 400 + "." + "x" * 350
            base = await _create(client, name, 0)
            response = await client.post(base + "/complete", headers=_AUTH)
            assert response.status == 200
            filename = (await response.json())["name"]
            assert filename.startswith("م")
            assert len(filename.encode("utf-8")) <= 180
            assert (tmp_path / filename).stat().st_size == 0

    asyncio.run(run())


def test_folder_symlink_cannot_receive_files(tmp_path):
    async def run():
        received = tmp_path / "Received"
        received.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        try:
            (received / "folder").symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("This platform does not allow creating symlinks.")
        async with TestClient(TestServer(_app(UploadStore(received)))) as client:
            base = await _create(client, "a", 0, relpath="folder/a")
            response = await client.post(base + "/complete", headers=_AUTH)
            assert response.status == 409
            assert not list(outside.iterdir())

    asyncio.run(run())


def test_limits_and_storage_errors_leave_verified_state_retryable(
    tmp_path, monkeypatch
):
    async def run():
        store = UploadStore(
            tmp_path, max_file_size=8, max_parallel_uploads=1, chunk_size=4
        )
        async with TestClient(TestServer(_app(store))) as client:
            monkeypatch.setattr(
                shutil, "disk_usage", lambda _: shutil._ntuple_diskusage(20, 15, 5)
            )
            response = await client.post(
                "/api/uploads", headers=_AUTH, json={"name": "too-large", "size": 9}
            )
            assert response.status == 413
            response = await client.post(
                "/api/uploads", headers=_AUTH, json={"name": "disk", "size": 6}
            )
            assert response.status == 507
            base = await _create(client, "a", 4)
            response = await client.post(
                "/api/uploads", headers=_AUTH, json={"name": "queue", "size": 1}
            )
            assert response.status == 429
            phone2 = {"X-Test-Device": "phone-2"}
            response = await client.post(
                "/api/uploads", headers=phone2, json={"name": "reserved", "size": 2}
            )
            assert response.status == 507
            response = await client.post(
                "/api/uploads", headers=phone2, json={"name": "second", "size": 1}
            )
            assert response.status == 201

            def disk_full(*_):
                raise OSError(errno.ENOSPC, "Disk full during write")

            with monkeypatch.context() as patch:
                patch.setattr(store, "_write_chunk", disk_full)
                assert await _put(client, base, 0, b"abcd") == 507
            status = await client.get(base, headers=_AUTH)
            assert (await status.json())["received"] == []
            assert await _put(client, base, 0, b"abcd") == 204

            def locked(*_):
                raise PermissionError(errno.EACCES, "Antivirus has locked this file")

            with monkeypatch.context() as patch:
                patch.setattr(store, "_publish", locked)
                assert (
                    await client.post(base + "/complete", headers=_AUTH)
                ).status == 503
                assert not (tmp_path / "a").exists()
            assert (await client.post(base + "/complete", headers=_AUTH)).status == 200
            await _create(client, "next", 0)
        assert list(tmp_path.glob("*.part")) == []

    asyncio.run(run())


def test_corrupt_staged_bytes_fail_final_verification_without_manifest_digest(tmp_path):
    async def run():
        async with TestClient(TestServer(_app(UploadStore(tmp_path)))) as client:
            base = await _create(client, "file", 4)
            assert await _put(client, base, 0, b"good") == 204
            (part,) = tmp_path.glob("*.part")
            part.write_bytes(b"bad!")
            response = await client.post(base + "/complete", headers=_AUTH)
            assert response.status == 422
            assert not (tmp_path / "file").exists()

    asyncio.run(run())


def test_duplicate_offer_with_explicit_discard_releases_slot(tmp_path):
    async def run():
        (tmp_path / "movie.bin").write_bytes(b"old!")
        (tmp_path / "movie (1).bin").write_bytes(b"same")
        store = UploadStore(tmp_path, max_parallel_uploads=1, chunk_size=4)
        async with TestClient(TestServer(_app(store))) as client:
            manifest = {"name": "movie.bin", "size": 4, "sha256": _hash(b"same")}
            response = await client.post("/api/uploads", headers=_AUTH, json=manifest)
            assert response.status == 201
            result = await response.json()
            assert result["duplicate"] == {
                "relpath": "movie (1).bin",
                "size": 4,
                "sha256": _hash(b"same"),
            }
            base = f"/api/uploads/{result['upload_id']}"
            assert (await client.delete(base)).status == 401
            response = await client.delete(base, headers={"X-Test-Device": "phone-2"})
            assert response.status == 404
            assert (await client.delete(base, headers=_AUTH)).status == 204
            assert (await client.delete(base, headers=_AUTH)).status == 204
            assert (await client.get(base, headers=_AUTH)).status == 404
            assert await _put(client, base, 0, b"same") == 404
            assert not list(tmp_path.glob("*.part"))
            assert (tmp_path / "movie (1).bin").read_bytes() == b"same"

            next_base = await _create(client, "movie.bin", 4, sha256=_hash(b"same"))
            assert await _put(client, next_base, 0, b"same") == 204
            response = await client.post(next_base + "/complete", headers=_AUTH)
            assert response.status == 200
            assert (await response.json())["relpath"] == "movie (2).bin"
            assert (await client.delete(next_base, headers=_AUTH)).status == 409
            assert (tmp_path / "movie (2).bin").read_bytes() == b"same"

    asyncio.run(run())


def test_replaced_stage_is_never_written_or_cleaned_up(tmp_path):
    async def run():
        async with TestClient(TestServer(_app(UploadStore(tmp_path)))) as client:
            base = await _create(client, "file", 4)
            (part,) = tmp_path.glob("*.part")
            part.rename(tmp_path / "moved")
            part.write_bytes(b"unrelated")
            assert await _put(client, base, 0, b"good") == 503
            assert (await client.delete(base, headers=_AUTH)).status == 204
        assert part.read_bytes() == b"unrelated"

    asyncio.run(run())


def test_large_chunk_offset_beyond_four_gib_is_not_truncated(tmp_path, monkeypatch):
    async def run():
        store = UploadStore(tmp_path, chunk_size=4)
        offset = 2**32
        monkeypatch.setattr(
            shutil, "disk_usage", lambda _: shutil._ntuple_diskusage(2**34, 0, 2**34)
        )
        async with TestClient(TestServer(_app(store))) as client:
            base = await _create(client, "large", offset + 4)
            writes = []
            monkeypatch.setattr(
                store,
                "_write_chunk",
                lambda _, index, data: writes.append((index, data)),
            )
            assert await _put(client, base, offset // 4, b"last") == 204
            assert writes == [(offset // 4, b"last")]
            status = await client.get(base, headers=_AUTH)
            assert (await status.json())["size"] == offset + 4

    asyncio.run(run())


def test_create_key_replays_in_parallel_and_is_scoped_to_owner(tmp_path):
    async def run():
        store = UploadStore(tmp_path, max_parallel_uploads=1, chunk_size=4)
        async with TestClient(TestServer(_app(store))) as client:
            headers = {**_AUTH, "Idempotency-Key": "transfer-abc"}
            manifest = {"name": "file", "size": 4}
            responses = await asyncio.gather(
                client.post("/api/uploads", headers=headers, json=manifest),
                client.post("/api/uploads", headers=headers, json=manifest),
            )
            assert [response.status for response in responses] == [201, 201]
            result = await responses[0].json()
            assert await responses[1].json() == result
            assert len(list(tmp_path.glob("*.part"))) == 1
            base = f"/api/uploads/{result['upload_id']}"
            assert await _put(client, base, 0, b"data") == 204

            response = await client.post("/api/uploads", headers=headers, json=manifest)
            assert response.status == 201
            assert await response.json() == result
            status = await client.get(base, headers=_AUTH)
            assert (await status.json())["received"] == [0]
            conflicting = await client.post(
                "/api/uploads", headers=headers, json={**manifest, "size": 5}
            )
            assert conflicting.status == 409
            assert (await conflicting.json())["error"] == "idempotency_conflict"

            other = await client.post(
                "/api/uploads",
                headers={**headers, "X-Test-Device": "phone-2"},
                json=manifest,
            )
            assert other.status == 201
            assert (await other.json())["upload_id"] != result["upload_id"]

            for value in ("", "a b", "a" * 129):
                invalid = await client.post(
                    "/api/uploads",
                    headers={**_AUTH, "Idempotency-Key": value},
                    json=manifest,
                )
                assert invalid.status == 400
                assert (await invalid.json())["error"] == "invalid_idempotency_key"

            assert (await client.delete(base, headers=_AUTH)).status == 204
            retry = await client.post("/api/uploads", headers=headers, json=manifest)
            assert await retry.json() == result
            assert (await client.get(base, headers=_AUTH)).status == 404

    asyncio.run(run())


def test_duplicate_discovery_refuses_symlinks_in_any_position(tmp_path):
    async def run():
        received = tmp_path / "Received"
        received.mkdir()
        outside = tmp_path / "other"
        outside.mkdir()
        target = outside / "file"
        target.write_bytes(b"data")
        try:
            (received / "folder").symlink_to(outside, target_is_directory=True)
            (received / "file").symlink_to(target)
        except OSError:
            pytest.skip("This platform does not allow creating symlinks.")
        async with TestClient(TestServer(_app(UploadStore(received)))) as client:
            for fields in ({}, {"relpath": "folder/file"}):
                response = await client.post(
                    "/api/uploads",
                    headers=_AUTH,
                    json={
                        "name": "file",
                        "size": 4,
                        "sha256": _hash(b"data"),
                        **fields,
                    },
                )
                assert response.status == 201
                assert "duplicate" not in await response.json()

    asyncio.run(run())


def test_duplicate_hashing_does_not_block_an_unrelated_create(tmp_path, monkeypatch):
    async def run():
        finished = threading.Event()
        hashing = asyncio.Event()
        loop = asyncio.get_running_loop()

        def find_duplicate(*_):
            loop.call_soon_threadsafe(hashing.set)
            assert finished.wait(5)

        store = UploadStore(tmp_path)
        monkeypatch.setattr(store, "_find_duplicate", find_duplicate)
        async with TestClient(TestServer(_app(store))) as client:
            task = asyncio.create_task(_create(client, "slow", 0, sha256=_hash(b"")))
            try:
                await asyncio.wait_for(hashing.wait(), timeout=2)
                await asyncio.wait_for(_create(client, "unrelated", 0), timeout=2)
                assert not task.done()
            finally:
                finished.set()
                await task

    asyncio.run(run())


def test_retry_recovers_upload_when_create_response_is_lost(tmp_path):
    async def run():
        committed = asyncio.Event()
        allow_response = asyncio.Event()

        @web.middleware
        async def delay_first_response(request, handler):
            response = await handler(request)
            if request.headers.get("X-Test-Drop-Create"):
                committed.set()
                await allow_response.wait()
            return response

        app = _app(UploadStore(tmp_path, max_parallel_uploads=1))
        app.middlewares.append(delay_first_response)
        async with TestClient(TestServer(app)) as client:
            body = b'{"name":"file","size":4}'
            _, writer = await asyncio.open_connection(
                client.server.host, client.server.port
            )
            try:
                writer.write(
                    (
                        "POST /api/uploads HTTP/1.1\r\n"
                        "Host: localhost\r\n"
                        "X-Test-Device: phone-1\r\n"
                        "X-Test-Drop-Create: true\r\n"
                        "Idempotency-Key: first-attempt\r\n"
                        f"Content-Length: {len(body)}\r\n\r\n"
                    ).encode("ascii")
                    + body
                )
                await writer.drain()
                await asyncio.wait_for(committed.wait(), timeout=5)
            finally:
                writer.transport.abort()
                allow_response.set()
            response = await client.post(
                "/api/uploads",
                headers={**_AUTH, "Idempotency-Key": "first-attempt"},
                data=body,
            )
            assert response.status == 201
            result = await response.json()
            base = f"/api/uploads/{result['upload_id']}"
            assert await _put(client, base, 0, b"data") == 204
            assert (await client.post(base + "/complete", headers=_AUTH)).status == 200
            assert (tmp_path / "file").read_bytes() == b"data"

    asyncio.run(run())

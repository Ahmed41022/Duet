"""HTTP routes for the resumable upload protocol."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from duet.core.uploads import UploadError, UploadStore

_CHUNK_HASH_HEADER = "X-Chunk-SHA256"
_MAX_JSON_BYTES = 32 * 1024


async def _json_object(
    request: web.Request, *, optional: bool = False
) -> dict[str, Any]:
    body = bytearray()
    async for piece in request.content.iter_chunked(4096):
        if len(body) + len(piece) > _MAX_JSON_BYTES:
            raise UploadError(
                413, "manifest_too_large", "The upload manifest is too large."
            )
        body.extend(piece)
    if optional and not body:
        return {}
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise UploadError(400, "invalid_json", "Provide a valid JSON object.") from exc
    if not isinstance(data, dict):
        raise UploadError(400, "invalid_json", "Provide a valid JSON object.")
    return data


def register_uploads(
    app: web.Application,
    store: UploadStore,
    get_device: Callable[[web.Request], str | None],
) -> None:
    """Install upload routes and clean up their partial files on app shutdown."""

    def authed(
        handler: Callable[[web.Request, str], Awaitable[web.Response]],
    ) -> Callable[[web.Request], Awaitable[web.Response]]:
        async def handle(request: web.Request) -> web.Response:
            try:
                device = get_device(request)
                if not device:
                    raise UploadError(
                        401, "pairing_required", "Pair this device to upload."
                    )
                return await handler(request, device)
            except ConnectionResetError:
                return web.Response(status=400)
            except UploadError as exc:
                return web.json_response(
                    {"error": exc.code, "message": str(exc)}, status=exc.status
                )

        return handle

    async def create(request: web.Request, device: str) -> web.Response:
        manifest = await _json_object(request)
        keys = request.headers.getall("Idempotency-Key", ())
        if len(keys) > 1:
            raise UploadError(
                400, "invalid_idempotency_key", "Provide only one idempotency key."
            )
        result = await store.create(
            device,
            name=manifest.get("name"),
            size=manifest.get("size"),
            sha256=manifest.get("sha256"),
            relpath=manifest.get("relpath"),
            idempotency_key=keys[0] if keys else None,
        )
        return web.json_response(result, status=201)

    async def put_chunk(request: web.Request, device: str) -> web.Response:
        index = request.match_info["index"]
        if not re.fullmatch(r"[0-9]{1,19}", index):
            raise UploadError(400, "invalid_chunk", "Provide a valid chunk index.")
        await store.put_chunk(
            device,
            request.match_info["upload_id"],
            int(index),
            request.headers.get(_CHUNK_HASH_HEADER),
            request.content.iter_chunked(64 * 1024),
            request.content_length,
        )
        return web.Response(status=204)

    async def status(request: web.Request, device: str) -> web.Response:
        return web.json_response(store.status(device, request.match_info["upload_id"]))

    async def complete(request: web.Request, device: str) -> web.Response:
        data = await _json_object(request, optional=True)
        result = await store.complete(
            device, request.match_info["upload_id"], sha256=data.get("sha256")
        )
        return web.json_response(result)

    async def discard(request: web.Request, device: str) -> web.Response:
        await store.discard(device, request.match_info["upload_id"])
        return web.Response(status=204)

    async def cleanup(_: web.Application) -> None:
        await store.close()

    app.router.add_post("/api/uploads", authed(create))
    app.router.add_put("/api/uploads/{upload_id}/chunks/{index}", authed(put_chunk))
    app.router.add_get("/api/uploads/{upload_id}", authed(status))
    app.router.add_delete("/api/uploads/{upload_id}", authed(discard))
    app.router.add_post("/api/uploads/{upload_id}/complete", authed(complete))
    app.on_cleanup.append(cleanup)

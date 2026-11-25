"""Authenticated phone-facing outbox routes and local offer notifications."""

import asyncio
import logging
import re
import sqlite3
from collections.abc import Iterator
from contextlib import suppress
from datetime import UTC
from email.utils import formatdate, parsedate_to_datetime
from typing import Protocol
from urllib.parse import quote

from aiohttp import web

from duet.core._outbox_stream import (
    file_chunks,
    open_entry,
    verify,
    zip_chunks,
)
from duet.core.outbox import Outbox, Source

_LOG = logging.getLogger(__name__)
_RANGE = re.compile(r"bytes=([0-9]*)-([0-9]*)", re.IGNORECASE)


class EventPublisher(Protocol):
    def publish(self, event: str, data: object) -> None: ...


def _range(header: str, size: int) -> tuple[int, int] | None:
    match = _RANGE.fullmatch(header.strip())
    if match is None:
        return None
    first, last = match.groups()
    if not first and not last:
        return None

    # Retain every valid offset without converting arbitrarily long decimals.
    limit = str(size + 1)

    def number(value: str) -> int:
        digits = value.lstrip("0")
        return int(digits or "0") if len(digits) <= len(limit) else size + 1

    start, end = (
        (number(first), min(number(last), size - 1) if last else size - 1)
        if first
        else (max(0, size - number(last)), size - 1)
    )
    if start >= size or end < start:
        raise web.HTTPRequestRangeNotSatisfiable(
            headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"}
        )
    return start, end


def _matches_if_range(value: str | None, etag: str, modified: int) -> bool:
    if value is None or value == etag:
        return True
    try:
        date = parsedate_to_datetime(value)
        return int(date.replace(tzinfo=date.tzinfo or UTC).timestamp()) == modified
    except (ValueError, OverflowError):
        return False


def _fetch(outbox: Outbox, identity: str) -> Source:
    source = outbox.get(identity)
    if source is None:
        raise web.HTTPNotFound(text="Offer not found")
    try:
        verify(source)
    except OSError:
        raise web.HTTPGone(text="The offered source is no longer available") from None
    return source


def _body(source: Source, start: int, length: int) -> Iterator[bytes]:
    if source.offer.is_folder:
        yield from zip_chunks(source)
        return
    with open_entry(source, source.entries[0]) as file:
        yield from file_chunks(file, source.entries[0], start, length)


async def _next_chunk(iterator: Iterator[bytes]) -> bytes | None:
    # Wait for any in-flight read before closing the generator on disconnect.
    task = asyncio.create_task(asyncio.to_thread(next, iterator, None))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(OSError):
            await task
        raise


async def _download(request: web.Request, outbox: Outbox) -> web.StreamResponse:
    source = await asyncio.to_thread(_fetch, outbox, request.match_info["id"])
    offer = source.offer
    size = offer.size
    etag = f'"{offer.id}"'
    modified = max(entry.signature[4] for entry in source.entries) // 1_000_000_000
    plain = offer.name.encode("ascii", "replace").decode("ascii").replace("?", "_")
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": "application/zip"
        if offer.is_folder
        else "application/octet-stream",
        "Content-Disposition": f'attachment; filename="{plain}"; '
        f"filename*=UTF-8''{quote(offer.name, safe='')}",
        "ETag": etag,
        "Last-Modified": formatdate(modified, usegmt=True),
    }
    span = None
    if (
        request.method == "GET"
        and "Range" in request.headers
        and _matches_if_range(request.headers.get("If-Range"), etag, modified)
    ):
        span = _range(request.headers["Range"], size)
    start, end = span if span is not None else (0, size - 1)
    if span is not None:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    response = web.StreamResponse(
        status=206 if span is not None else 200, headers=headers
    )
    response.content_length = length = end - start + 1
    if request.method == "HEAD" or not length:
        await response.prepare(request)
        return response
    stream = _body(source, start, length)
    try:
        try:
            chunk = await _next_chunk(stream)
            skip = start if offer.is_folder else 0
            while chunk is not None and skip >= len(chunk):
                skip -= len(chunk)
                chunk = await _next_chunk(stream)
            if chunk is None:
                raise OSError("The offered representation ended early")
        except OSError:
            raise web.HTTPGone(
                text="The offered source is no longer available"
            ) from None
        await response.prepare(request)
        while length:
            if chunk is None:
                raise OSError("The offered representation ended early")
            piece = chunk[skip : skip + length]
            skip = 0
            length -= len(piece)
            await response.write(piece)
            if not length:
                break
            chunk = await _next_chunk(stream)
    except OSError:
        # The sent headers describe the original representation. An incomplete
        # HTTP response lets clients discard the transfer or retry safely.
        if request.transport is not None:
            request.transport.close()
        response.force_close()
    finally:
        stream.close()
    return response


def register_outbox(
    app: web.Application,
    *,
    events: EventPublisher,
    outbox: Outbox | None = None,
    poll_interval: float = 0.5,
) -> None:
    """Attach outbox endpoints and the cross-process change watcher."""
    if not 0 < poll_interval < float("inf"):
        raise ValueError("The outbox polling interval must be finite and positive")
    store = outbox if outbox is not None else Outbox()

    async def listing(request: web.Request) -> web.Response:
        offers = await asyncio.to_thread(store.list)
        return web.json_response({"files": [offer.public() for offer in offers]})

    async def download(request: web.Request) -> web.StreamResponse:
        return await _download(request, store)

    async def watch(application: web.Application):
        version = await asyncio.to_thread(store.revision)

        async def poll() -> None:
            nonlocal version
            while True:
                await asyncio.sleep(poll_interval)
                try:
                    current = await asyncio.to_thread(store.revision)
                except (OSError, sqlite3.Error):
                    _LOG.warning("Could not check the outbox for changes")
                    continue
                if current != version:
                    version = current
                    events.publish("outbox", {})

        task = asyncio.create_task(poll())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app.router.add_get("/api/outbox", listing)
    app.router.add_get("/api/files/{id}", download)
    app.cleanup_ctx.append(watch)

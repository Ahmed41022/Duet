"""HTTP entry points for pairing, authenticated API requests, and the phone page."""

import asyncio
import json
import mimetypes
import time
from collections.abc import Callable
from importlib import resources
from importlib.resources.abc import Traversable

from aiohttp import web

from duet.core._network import is_private_ipv4
from duet.core.config import Config
from duet.core.pairing import PairingError, PairingManager, Session
from duet.core.uploads import UploadStore
from duet.server._events import EventHub
from duet.server.uploads import register_uploads

_COOKIE = "duet_session"
_CREDS_LIMIT = 4096
EVENTS = web.AppKey("events", EventHub)
SESSION = web.RequestKey("session", Session)


def session_for(request: web.Request) -> Session:
    """Return the authenticated device identity for a protected route."""
    return request[SESSION]


def create_app(
    pairing: PairingManager,
    *,
    host: str,
    port: int,
    config: Config | None = None,
    static_root: Traversable | None = None,
    heartbeat_interval: float = 15,
    on_paired: Callable[[], None] | None = None,
) -> web.Application:
    """Create the application without opening a socket."""
    if not is_private_ipv4(host) or not 1 <= port <= 65535:
        raise ValueError("The server requires a private IPv4 address and a valid port")
    if not 0 < heartbeat_interval < float("inf"):
        raise ValueError("The heartbeat interval must be finite and positive")
    authority = f"{host}:{port}" if port != 80 else host
    allowed_hosts = {authority, f"{host}:{port}"}
    allowed_origins = {f"http://{authority}", f"http://{host}:{port}"}
    assets = (
        resources.files("duet.server").joinpath("static")
        if static_root is None
        else static_root
    )

    @web.middleware
    async def guard(request: web.Request, handler: Callable) -> web.StreamResponse:
        hosts = request.headers.getall("Host", [])
        if len(hosts) != 1 or hosts[0] not in allowed_hosts:
            raise web.HTTPBadRequest(text="Invalid Host")
        origins = request.headers.getall("Origin", [])
        if origins and (len(origins) != 1 or origins[0] not in allowed_origins):
            raise web.HTTPForbidden(text="Invalid Origin")
        if request.path.startswith("/api/") and request.headers.get(
            "Sec-Fetch-Site", "none"
        ) not in {"none", "same-origin"}:
            raise web.HTTPForbidden(text="Cross-origin request")
        if request.path.startswith("/api/") and not (
            request.path == "/api/pair" and request.method == "POST"
        ):
            session = pairing.authenticate(request.cookies.get(_COOKIE))
            if session is None:
                raise web.HTTPUnauthorized(text="Pairing required")
            request[SESSION] = session
        return await handler(request)

    app = web.Application(middlewares=[guard])
    app[EVENTS] = EventHub()

    async def pair(request: web.Request) -> web.Response:
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text="Expected application/json")
        if request.content_length is not None and request.content_length > _CREDS_LIMIT:
            raise web.HTTPRequestEntityTooLarge(
                max_size=_CREDS_LIMIT, actual_size=request.content_length
            )
        body = bytearray()
        async for chunk in request.content.iter_chunked(_CREDS_LIMIT):
            body.extend(chunk)
            if len(body) > _CREDS_LIMIT:
                raise web.HTTPRequestEntityTooLarge(
                    max_size=_CREDS_LIMIT, actual_size=len(body)
                )
        try:
            values = json.loads(body)
        except (ValueError, RecursionError):
            raise web.HTTPBadRequest(text="Invalid pairing request") from None
        if not (
            isinstance(values, dict)
            and (values.keys() == {"pin"} or values.keys() == {"token"})
            and isinstance(next(iter(values.values())), str)
        ):
            raise web.HTTPBadRequest(text="Supply one token or PIN as a string")
        try:
            secret = pairing.pair(address=request.remote or "unknown", **values)
        except PairingError as exc:
            if exc.retry_after is not None:
                raise web.HTTPTooManyRequests(
                    text=str(exc), headers={"Retry-After": str(exc.retry_after)}
                ) from None
            raise web.HTTPUnauthorized(text=str(exc)) from None
        response = web.json_response({"paired": True})
        response.set_cookie(
            _COOKIE, secret, httponly=True, samesite="Strict", path="/", secure=False
        )
        if on_paired is not None:
            on_paired()
        return response

    async def events(request: web.Request) -> web.StreamResponse:
        session = session_for(request)
        secret = request.cookies[_COOKIE]
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        async with request.app[EVENTS].subscribe(session.id) as queue:
            try:
                await response.prepare(request)
                await asyncio.wait_for(response.write(b": connected\n\n"), timeout=5)
                next_heartbeat = time.monotonic() + heartbeat_interval
                while pairing.authenticate(secret) is not None:
                    timeout = min(
                        pairing.remaining(session),
                        max(0, next_heartbeat - time.monotonic()),
                        1.0,
                    )
                    try:
                        frame = await asyncio.wait_for(queue.get(), timeout=timeout)
                    except TimeoutError:
                        if time.monotonic() < next_heartbeat:
                            continue
                        frame = b": heartbeat\n\n"
                        next_heartbeat = time.monotonic() + heartbeat_interval
                    if frame is None or pairing.authenticate(secret) is None:
                        break
                    await asyncio.wait_for(response.write(frame), timeout=5)
            except (ConnectionError, TimeoutError):
                response.force_close()
        return response

    async def static(request: web.Request) -> web.Response:
        path = request.match_info.get("asset", "index.html")
        parts = path.split("/")
        if any(
            part in {"", ".", ".."} or "\\" in part or ":" in part for part in parts
        ):
            raise web.HTTPNotFound()
        asset = assets.joinpath(*parts)
        if not asset.is_file():
            raise web.HTTPNotFound()
        media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        return web.Response(body=asset.read_bytes(), content_type=media_type)

    async def protect_response(
        request: web.Request, response: web.StreamResponse
    ) -> None:
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"

    async def close_events(application: web.Application) -> None:
        application[EVENTS].close()

    config = config or Config()
    uploads = UploadStore(
        config.received_directory,
        max_file_size=config.max_file_size,
        max_parallel_uploads=config.max_parallel_uploads,
    )
    register_uploads(app, uploads, lambda request: session_for(request).id)
    app.router.add_post("/api/pair", pair)
    app.router.add_get("/api/events", events, allow_head=False)
    app.router.add_get("/", static)
    app.router.add_get("/{asset:assets/.+}", static)
    app.on_response_prepare.append(protect_response)
    app.on_shutdown.append(close_events)
    return app

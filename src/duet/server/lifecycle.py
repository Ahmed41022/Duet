"""Stop a listener when it loses its address or has been idle too long."""

import asyncio
from collections.abc import Callable
from contextlib import suppress
from time import monotonic
from typing import Literal

from aiohttp import web

from duet.core._idle import IdleTracker
from duet.core._network import is_address_assigned

StopReason = Literal["idle", "address_changed"]
_ACTIVITY_ROUTES = {
    ("GET", "/api/files/{id}"),
    ("POST", "/api/text"),
    ("POST", "/api/uploads"),
    ("PUT", "/api/uploads/{upload_id}/chunks/{index}"),
    ("POST", "/api/uploads/{upload_id}/complete"),
    ("DELETE", "/api/uploads/{upload_id}"),
}


def install_lifecycle(
    app: web.Application,
    host: str,
    idle_seconds: float,
    on_stop: Callable[[StopReason], None],
    *,
    poll_interval: float = 1.0,
    address_is_assigned: Callable[[str], bool] = is_address_assigned,
    clock: Callable[[], float] = monotonic,
) -> IdleTracker:
    """Install after the auth guard and notify the server owner on its event loop."""
    if not 0 < poll_interval < float("inf"):
        raise ValueError("Lifecycle polling interval must be finite and positive")
    tracker = IdleTracker(idle_seconds, clock=clock)

    @web.middleware
    async def monitor_activity(
        request: web.Request, handler: Callable
    ) -> web.StreamResponse:
        resource = request.match_info.route.resource
        route = (request.method, resource.canonical) if resource is not None else ()
        if route in _ACTIVITY_ROUTES:
            with tracker.hold():
                response = await handler(request)
                if 200 <= response.status < 300:
                    tracker.touch()
                return response
        response = await handler(request)
        if route == ("POST", "/api/pair") and 200 <= response.status < 300:
            tracker.touch()
        return response

    async def monitor(_: web.Application):
        tracker.touch()

        async def poll() -> None:
            while True:
                await asyncio.sleep(poll_interval)
                try:
                    assigned = await asyncio.to_thread(address_is_assigned, host)
                except OSError:
                    assigned = True
                if not assigned:
                    on_stop("address_changed")
                    return
                if tracker.expired():
                    on_stop("idle")
                    return

        task = asyncio.create_task(poll())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app.middlewares.append(monitor_activity)
    app.cleanup_ctx.append(monitor)
    return tracker

"""Session-lifetime shared text, held in memory on the PC."""

from collections import deque
from datetime import UTC, datetime
from uuid import uuid4

from aiohttp import web

from duet.server._events import EventHub


class SharedText:
    def __init__(self, events: EventHub) -> None:
        self._events = events
        self._messages: deque[dict[str, str]] = deque(maxlen=100)

    def messages(self) -> list[dict[str, str]]:
        return list(self._messages)

    def send(self, text: str, *, source: str) -> dict[str, str]:
        if not isinstance(text, str) or not text.strip() or len(text) > 16384:
            raise ValueError("Enter between 1 and 16,384 characters.")
        message = {
            "id": uuid4().hex,
            "text": text,
            "source": source,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._messages.append(message)
        self._events.publish("text", {"id": message["id"]})
        return message


TEXT = web.AppKey("shared_text", SharedText)


def register_text(app: web.Application, events: EventHub) -> None:
    shared = app[TEXT] = SharedText(events)

    async def list_messages(_: web.Request) -> web.Response:
        return web.json_response({"messages": shared.messages()})

    async def receive(request: web.Request) -> web.Response:
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("Expected a text message")
            message = shared.send(payload.get("text"), source="phone")
        except (ValueError, RecursionError) as exc:
            raise web.HTTPBadRequest(text="Invalid or oversized text message") from exc
        return web.json_response(message, status=201)

    app.router.add_get("/api/text", list_messages)
    app.router.add_post("/api/text", receive)

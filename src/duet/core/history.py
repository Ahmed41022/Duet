"""Persistent transfer history and an event-loop-local transfer observer."""

import asyncio
import logging
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from duet.core.outbox import default_database

_LOG = logging.getLogger(__name__)


class HistoryStore:
    """Keep local transfer metadata in a database shared by CLI and desktop."""

    def __init__(self, database: Path | None = None) -> None:
        self.database = (
            Path(database)
            if database is not None
            else default_database().with_name("history.sqlite3")
        )

    def _connect(self) -> sqlite3.Connection:
        self.database.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS transfers ("
                "id TEXT PRIMARY KEY, name TEXT NOT NULL, direction TEXT NOT NULL,"
                "status TEXT NOT NULL, completed_at TEXT NOT NULL, "
                "size INTEGER NOT NULL)"
            )
        except BaseException:
            connection.close()
            raise
        return connection

    def record(self, entry: dict) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "INSERT INTO transfers VALUES "
                "(:id, :name, :direction, :status, :completed_at, :size) "
                "ON CONFLICT(id) DO NOTHING",
                entry,
            )
            return cursor.rowcount == 1

    def list(self, limit: int = 100, *, query: str = "") -> list[dict]:
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            statement = "SELECT * FROM transfers"
            parameters: tuple = (limit,)
            if query := query.strip().casefold():
                connection.create_function("casefold", 1, str.casefold)
                statement += " WHERE instr(casefold(name), ?) > 0"
                parameters = (query, limit)
            records = connection.execute(
                statement + " ORDER BY completed_at DESC, id DESC LIMIT ?", parameters
            )
            return [dict(row) for row in records]


class TransferObserver:
    """Track active transfers and hand completed metadata to the local database."""

    def __init__(
        self,
        history: HistoryStore,
        *,
        on_receive: Callable[[str], None] | None = None,
    ) -> None:
        self.history = history
        self._on_receive = on_receive
        self._active: dict[str, tuple[float, dict]] = {}
        self._pending: set[asyncio.Task] = set()
        self.last_activity = time.monotonic()
        self.error: str | None = None

    def update(
        self, id: str, name: str, direction: str, bytes_done: int, bytes_total: int
    ) -> None:
        now = self.last_activity = time.monotonic()
        previous = self._active.get(id)
        start = previous[0] if previous else now
        elapsed = now - start
        self._active[id] = (
            start,
            {
                "id": id,
                "name": name,
                "direction": direction,
                "status": "active",
                "progress": min(1.0, bytes_done / bytes_total) if bytes_total else 0.0,
                "bytes_done": bytes_done,
                "bytes_total": bytes_total,
                "speed": bytes_done / elapsed if elapsed else 0.0,
            },
        )

    def finish(self, id: str, status: str, *, name: str | None = None) -> None:
        active = self._active.pop(id, None)
        if active is None:
            return
        self.last_activity = time.monotonic()
        transfer = active[1]
        entry = {
            "id": id,
            "name": name if name is not None else transfer["name"],
            "direction": transfer["direction"],
            "status": status,
            "completed_at": datetime.now(UTC).isoformat(),
            "size": transfer["bytes_total"],
        }
        task = asyncio.create_task(self._record(entry))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _record(self, entry: dict) -> None:
        try:
            inserted = await asyncio.to_thread(self.history.record, entry)
        except (sqlite3.Error, OSError) as exc:
            self.error = "Could not save transfer history"
            _LOG.warning("%s: %s", self.error, exc)
        else:
            if (
                inserted
                and self._on_receive is not None
                and entry["direction"] == "upload"
                and entry["status"] == "completed"
            ):
                self._on_receive(entry["name"])

    def active(self) -> list[dict]:
        return [dict(transfer) for _, transfer in self._active.values()]

    async def flush(self) -> None:
        for identity in tuple(self._active):
            self.finish(identity, "cancelled")
        if self._pending:
            await asyncio.gather(*self._pending)

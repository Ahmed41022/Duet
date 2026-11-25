"""The desktop's Qt-independent bridge to the LAN server and local state."""

import asyncio
import json
import sqlite3
import threading
import time
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from aiohttp import web

from duet.core._network import is_private_ipv4, select_bind_address
from duet.core.config import Config
from duet.core.device_status import DeviceStatus
from duet.core.history import HistoryStore, TransferObserver
from duet.core.outbox import Outbox, default_database
from duet.core.pairing import PairingManager, PairingOffer, Session
from duet.server.app import create_app
from duet.server.text import TEXT


class DesktopRuntime:
    """Own an asyncio worker and expose cached snapshots to the UI thread."""

    def __init__(
        self,
        *,
        config: Config | None = None,
        data_directory: Path | None = None,
        port: int = 8765,
        offer_ttl: float = 600,
    ) -> None:
        if not 1 <= port <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        if not 0 < offer_ttl < float("inf"):
            raise ValueError("Pairing lifetime must be finite and positive")
        data = (
            Path(data_directory)
            if data_directory is not None
            else default_database().parent
        )
        self._settings_path = data / "desktop.json"
        self._outbox = Outbox(data / "outbox.sqlite3")
        self._history = HistoryStore(data / "history.sqlite3")
        self._config = config or Config()
        self._port = port
        self._offer_ttl = offer_ttl
        self._bind: str | None = None
        self._runner: web.AppRunner | None = None
        self._app: web.Application | None = None
        self._url: str | None = None
        self._pairing: PairingManager | None = None
        self._pairing_offer: PairingOffer | None = None
        self._observer: TransferObserver | None = None
        self._device_status: DeviceStatus | None = None
        self._devices: dict[str, dict] = {}
        self._outbox_rows: list[dict] = []
        self._history_rows: list[dict] = []
        self._error: str | None = None
        self._refresh_error: str | None = None
        self._closed = False
        self._submission_lock = threading.Lock()
        self._snapshot_lock = threading.Lock()
        self._snapshot: dict = {}
        self._publish()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run, name="Duet desktop server", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._commands = asyncio.Lock()
        self._storage_lock = asyncio.Lock()
        self._initialized = self._loop.create_task(self._load_settings())
        self._refresh = self._loop.create_task(self._refresh_storage())
        self._tick = self._loop.create_task(self._publish_periodically())
        try:
            self._loop.run_forever()
        finally:
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.run_until_complete(self._loop.shutdown_default_executor())
            self._loop.close()

    async def _load_settings(self) -> None:
        try:
            values = await asyncio.to_thread(self._read_settings)
            if values is not None:
                self._config, self._bind = values
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self._error = f"Could not load desktop settings: {exc}"
        self._publish()

    def _read_settings(self) -> tuple[Config, str | None] | None:
        try:
            values = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        return self._settings(
            values["received_directory"], values["idle_timeout_seconds"], values["bind"]
        )

    def _settings(
        self,
        received_directory: Path | str,
        idle_timeout_seconds: int,
        bind: str | None,
    ) -> tuple[Config, str | None]:
        if not str(received_directory).strip():
            raise ValueError("Select a Received folder")
        if bind is not None and (
            not isinstance(bind, str) or not is_private_ipv4(bind)
        ):
            raise ValueError("Bind address must be a private RFC 1918 IPv4 address")
        config = replace(
            self._config,
            received_directory=Path(received_directory).expanduser().absolute(),
            idle_timeout_seconds=idle_timeout_seconds,
        )
        return config, bind

    def _save_settings(self, config: Config, bind: str | None) -> None:
        values = {
            "received_directory": str(config.received_directory),
            "idle_timeout_seconds": config.idle_timeout_seconds,
            "bind": bind,
        }
        parent = self._settings_path.parent
        parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with NamedTemporaryFile(dir=parent, delete=False) as temporary:
            path = Path(temporary.name)
        try:
            path.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
            path.replace(self._settings_path)
        finally:
            path.unlink(missing_ok=True)

    def snapshot(self) -> dict:
        """Return an independent copy without querying the worker or disk."""
        with self._snapshot_lock:
            snapshot = self._snapshot
        return deepcopy(snapshot)

    def _publish(self) -> None:
        pairing = self._pairing
        if pairing is not None:
            devices = pairing.devices()
            for device in devices:
                if device.id not in self._devices:
                    self._see_device(device)
            self._devices = {device.id: self._devices[device.id] for device in devices}
        offer = self._pairing_offer
        observer = self._observer
        messages = self._app[TEXT].messages() if self._app is not None else []
        snapshot = {
            "running": self._runner is not None,
            "url": self._url,
            "pin": offer.pin if offer else None,
            "token": offer.token if offer else None,
            "devices": [dict(device) for device in self._devices.values()],
            "transfers": observer.active() if observer else [],
            "history": self._history_rows,
            "outbox": self._outbox_rows,
            "messages": [
                {
                    "id": message["id"],
                    "text": message["text"],
                    "direction": "outgoing"
                    if message["source"] == "pc"
                    else "incoming",
                    "created_at": message["created_at"],
                }
                for message in messages
            ],
            "received_directory": str(self._config.received_directory),
            "idle_timeout_seconds": self._config.idle_timeout_seconds,
            "bind": self._bind,
            "error": self._error
            or self._refresh_error
            or (observer and observer.error),
            "network_name": None,
            "network_is_public": False,
        }
        with self._snapshot_lock:
            self._snapshot = snapshot

    async def _publish_periodically(self) -> None:
        while True:
            offer = self._pairing_offer
            if offer is not None and time.monotonic() >= offer.expires_at:
                self._rotate_offer()
            self._publish()
            await asyncio.sleep(0.15)

    async def _refresh_storage(self) -> None:
        while True:
            await self._read_storage()
            self._publish()
            await asyncio.sleep(0.5)

    async def _read_storage(self) -> None:
        async with self._storage_lock:
            try:
                offers, history = await asyncio.to_thread(self._storage_rows)
            except (OSError, sqlite3.Error):
                self._refresh_error = "Could not read the outbox or transfer history"
            else:
                self._outbox_rows = offers
                self._history_rows = history
                self._refresh_error = None

    def _storage_rows(self) -> tuple[list[dict], list[dict]]:
        offers = self._outbox.list() if self._outbox.database.exists() else []
        history = self._history.list() if self._history.database.exists() else []
        return [offer.public() for offer in offers], history

    def _see_device(self, session: Session) -> None:
        now = datetime.now(UTC).isoformat()
        device = self._devices.setdefault(
            session.id,
            {
                "id": session.id,
                "name": session.address,
                "connected_at": now,
            },
        )
        device["last_seen"] = now

    def _rotate_offer(self) -> None:
        if self._pairing is not None:
            self._pairing_offer = self._pairing.issue_offer()
            self._publish()

    async def _command(self, action: Callable[..., Coroutine], args: tuple) -> None:
        await asyncio.shield(self._initialized)
        async with self._commands:
            try:
                await action(*args)
            except (OSError, ValueError, sqlite3.Error) as exc:
                self._error = str(exc)
            else:
                self._error = None
            finally:
                self._publish()

    def _submit(self, action: Callable[..., Coroutine], *args: object) -> Future:
        with self._submission_lock:
            if self._closed:
                raise RuntimeError("The desktop runtime has closed")
            return asyncio.run_coroutine_threadsafe(
                self._command(action, args), self._loop
            )

    def start(self) -> Future:
        return self._submit(self._start)

    async def _start(self) -> None:
        if self._runner is not None:
            return
        host = await asyncio.to_thread(select_bind_address, self._bind)
        pairing = PairingManager(offer_ttl=self._offer_ttl)
        observer = TransferObserver(self._history)
        device_status = DeviceStatus(self._outbox.database.with_name("devices"))
        app = create_app(
            pairing,
            host=host,
            port=self._port,
            config=self._config,
            on_paired=self._rotate_offer,
            outbox=self._outbox,
            observer=observer,
            device_status=device_status,
            on_device_seen=self._see_device,
        )
        runner = web.AppRunner(
            app, access_log=None, handler_cancellation=True, shutdown_timeout=1
        )
        try:
            await runner.setup()
            await web.TCPSite(runner, host=host, port=self._port).start()
        except BaseException:
            await runner.cleanup()
            raise
        self._app, self._runner = app, runner
        self._url = (
            f"http://{host}:{self._port}" if self._port != 80 else f"http://{host}"
        )
        self._pairing, self._observer = pairing, observer
        self._device_status = device_status
        self._rotate_offer()

    def stop(self) -> Future:
        return self._submit(self._stop)

    async def _stop(self) -> None:
        if self._runner is None:
            return
        try:
            await self._runner.cleanup()
        finally:
            self._app = self._runner = self._pairing = self._pairing_offer = None
            self._url = None
            self._observer = None
            self._device_status = None
            self._devices.clear()
        await self._read_storage()

    def revoke(self, id: str) -> Future:
        return self._submit(self._revoke, id)

    async def _revoke(self, id: str) -> None:
        if self._pairing is not None and self._pairing.revoke(id):
            if self._device_status is not None:
                self._device_status.changed()

    def offer(self, path: Path | str) -> Future:
        return self._submit(self._offer, path)

    async def _offer(self, path: Path | str) -> None:
        await asyncio.to_thread(self._outbox.offer, path)
        await self._read_storage()

    def remove_offer(self, offer_id: str) -> Future:
        return self._submit(self._remove_offer, offer_id)

    async def _remove_offer(self, offer_id: str) -> None:
        await asyncio.to_thread(self._outbox.revoke, offer_id)
        await self._read_storage()

    def send_text(self, text: str) -> Future:
        return self._submit(self._send_text, text)

    async def _send_text(self, text: str) -> None:
        if self._app is None:
            raise ValueError("Start Duet before sending text")
        self._app[TEXT].send(text, source="pc")

    def configure(
        self,
        received_directory: Path | str,
        idle_timeout_seconds: int,
        bind: str | None = None,
    ) -> Future:
        return self._submit(
            self._configure, received_directory, idle_timeout_seconds, bind
        )

    async def _configure(
        self,
        received_directory: Path | str,
        idle_timeout_seconds: int,
        bind: str | None,
    ) -> None:
        config, bind = self._settings(received_directory, idle_timeout_seconds, bind)
        await asyncio.to_thread(self._save_settings, config, bind)
        self._config, self._bind = config, bind

    async def _shutdown(self) -> None:
        await asyncio.shield(self._initialized)
        async with self._commands:
            try:
                await self._stop()
            finally:
                self._refresh.cancel()
                self._tick.cancel()
                await asyncio.gather(self._refresh, self._tick, return_exceptions=True)
                self._publish()

    def close(self) -> None:
        """Stop the listener and join the worker before returning."""
        with self._submission_lock:
            if not self._closed:
                self._closed = True
                shutdown = asyncio.run_coroutine_threadsafe(
                    self._shutdown(), self._loop
                )
                try:
                    shutdown.result()
                finally:
                    self._loop.call_soon_threadsafe(self._loop.stop)
                    self._thread.join()

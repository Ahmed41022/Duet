"""Publish local paired-device status for the CLI and desktop listener."""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

import psutil

from duet.core.outbox import default_database
from duet.core.pairing import PairingManager

_LOG = logging.getLogger(__name__)
_INTERVAL = 0.5
_LEASE = 3.0


def _status_directory(directory: Path | None) -> Path:
    return (
        Path(directory)
        if directory is not None
        else default_database().with_name("devices")
    )


def _process_is_current(pid: int, started: float) -> bool:
    try:
        proc = psutil.Process(pid)
        return proc.create_time() == started and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def _read_status(path: Path) -> dict | None:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if (
            type(state["pid"]) is int
            and state["pid"] > 0
            and isinstance(state["lease"], (int, float))
            and isinstance(state["devices"], list)
            and _process_is_current(state["pid"], state["started"])
        ):
            return state
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def list_devices(directory: Path | None = None) -> list[dict[str, str]]:
    """Read unexpired pairing status from listeners owned by this user."""
    devices = []
    now = time.monotonic()
    for path in _status_directory(directory).glob("*.json"):
        state = _read_status(path)
        if state is None or state.get("lease", 0) <= now:
            continue
        devices.extend(
            {"id": device["id"], "address": device["address"]}
            for device in state["devices"]
            if isinstance(device, dict)
            and isinstance(device.get("id"), str)
            and isinstance(device.get("address"), str)
            and isinstance(device.get("expires"), (int, float))
            and device["expires"] > now
        )
    return sorted(devices, key=lambda device: (device["address"], device["id"]))


class DeviceStatus:
    """Refresh a local snapshot on the server loop and write it off-thread."""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = _status_directory(directory)
        self._path = self._directory / f"{uuid4().hex}.json"
        self._changed = asyncio.Event()

    def changed(self) -> None:
        """Request an early update after pairing or revocation."""
        self._changed.set()

    async def run(self, pairing: PairingManager, stop: asyncio.Event) -> None:
        pid = os.getpid()
        started = await asyncio.to_thread(psutil.Process(pid).create_time)
        failed = False
        try:
            while not stop.is_set():
                self._changed.clear()
                devices = [
                    {
                        "id": device.id,
                        "address": device.address,
                        "expires": device.expires_at,
                    }
                    for device in pairing.devices()
                ]
                try:
                    await asyncio.to_thread(self._write, pid, started, devices)
                except OSError as exc:
                    if not failed:
                        _LOG.warning("Could not publish local device status: %s", exc)
                    failed = True
                else:
                    failed = False
                try:
                    await asyncio.wait_for(self._changed.wait(), timeout=_INTERVAL)
                except TimeoutError:
                    pass
        finally:
            try:
                await asyncio.to_thread(self._path.unlink, missing_ok=True)
            except OSError as exc:
                _LOG.warning("Could not remove local device status: %s", exc)

    def _write(self, pid: int, started: float, devices: list[dict]) -> None:
        if not devices:
            self._path.unlink(missing_ok=True)
            return
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        state = {
            "pid": pid,
            "started": started,
            "lease": time.monotonic() + _LEASE,
            "devices": devices,
        }
        path = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self._directory, delete=False
            ) as temp:
                path = Path(temp.name)
                json.dump(state, temp)
            path.replace(self._path)
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

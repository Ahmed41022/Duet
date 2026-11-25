"""Resumable uploads and publication into the configured Received directory."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import os
import re
import shutil
import stat
import tempfile
import uuid
from collections.abc import AsyncIterable, Coroutine, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from duet.core.history import TransferObserver

DEFAULT_CHUNK_SIZE = 8 * 1024**2
_CREATE_KEY = re.compile(r"[!-~]{1,128}")
_HASH = re.compile(r"[0-9a-fA-F]{64}")
_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f-\x9f\ud800-\udfff]')
_RESERVED_NAME = re.compile(
    r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[1-9¹²³])",
    re.IGNORECASE,
)
_COMPONENT_BYTES = 180
_MAX_FOLDERS = 32
_T = TypeVar("_T")


class UploadError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _fit_component(stem: str, suffix: str = "", counter: str = "") -> str:
    available = _COMPONENT_BYTES - len((suffix + counter).encode("utf-8"))
    prefix = stem.encode("utf-8")[:available].decode("utf-8", errors="ignore")
    return (prefix.rstrip(" .") or "file") + counter + suffix


def _split_component(name: str) -> tuple[str, str]:
    stem, suffix = os.path.splitext(name)
    return (name, "") if len(suffix.encode("utf-8")) > 40 else (stem, suffix)


def clean_name(name: str) -> str:
    """Return a single portable component with room for collision suffixes."""
    cleaned = _INVALID_NAME.sub("_", name).strip(" .") or "file"
    if _RESERVED_NAME.fullmatch(cleaned.split(".", 1)[0].rstrip(" ")):
        cleaned = f"_{cleaned}"
    return _fit_component(*_split_component(cleaned))


def _upload_path(name: object, relpath: object) -> tuple[str, ...]:
    if not isinstance(name, str) or not name.strip():
        raise UploadError(400, "invalid_name", "Provide a non-empty file name.")
    if relpath is None:
        return (clean_name(name),)
    if not isinstance(relpath, str) or not relpath or "\\" in relpath:
        raise UploadError(
            400, "invalid_path", "Use a relative folder path with forward slashes."
        )
    parts = relpath.split("/")
    if (
        len(parts) > _MAX_FOLDERS + 1
        or any(part in {"", ".", ".."} for part in parts)
        or re.match(r"^[A-Za-z]:", parts[0])
        or parts[-1] != name
    ):
        raise UploadError(
            400, "invalid_path", "The relative path must end with the file name."
        )
    return tuple(clean_name(part) for part in parts)


def _digest(value: object, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise UploadError(
            400, "invalid_hash", "Provide a 64-character SHA-256 hex digest."
        )
    return value.lower()


def _storage_error(exc: OSError) -> UploadError:
    if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)} or getattr(
        exc, "winerror", None
    ) in {39, 112}:
        return UploadError(
            507, "insufficient_storage", "The PC has insufficient free space."
        )
    return UploadError(
        503, "storage_unavailable", "The PC could not access upload storage."
    )


def _is_reparse_point(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _identity(info: os.stat_result) -> tuple[int, int]:
    return (info.st_dev, info.st_ino)


def _check_directory(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or _is_reparse_point(info):
        raise UploadError(
            409, "unsafe_path", "An upload folder is blocked by an existing path."
        )


@contextmanager
def _duplicate_directory(path: Path) -> Iterator[int | None]:
    if os.name == "nt":
        for parent in reversed((path, *path.parents)):
            _check_directory(parent)
        yield None
        return

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        directory = os.open(path.anchor, flags)
        stack.callback(os.close, directory)
        for part in path.parts[1:]:
            directory = os.open(part, flags, dir_fd=directory)
            stack.callback(os.close, directory)
        yield directory


def _open_duplicate(path: Path, directory: int | None):
    if directory is None:
        return path.open("rb")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    return os.fdopen(os.open(path.name, flags, dir_fd=directory), "rb")


def _windows_path_matches(fd: int, expected: Path) -> bool:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    get_path = ctypes.WinDLL("kernel32").GetFinalPathNameByHandleW
    get_path.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    get_path.restype = wintypes.DWORD
    handle = msvcrt.get_osfhandle(fd)
    length = get_path(handle, None, 0, 0)
    if not length:
        return False
    buffer = ctypes.create_unicode_buffer(length)
    written = get_path(handle, buffer, length, 0)
    if not 0 < written < length:
        return False
    prefix = "\\\\?\\"
    actual = buffer.value.removeprefix(prefix)
    if actual.startswith("UNC\\"):
        actual = "\\\\" + actual[4:]
    return Path(actual) == Path(str(expected).removeprefix(prefix))


async def _finish_io(operation: Coroutine[Any, Any, _T]) -> _T:
    """Finish a filesystem mutation before releasing its lock on cancellation."""
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            # The disconnected client can inspect state when it reconnects.
            pass
        raise


@dataclass(slots=True)
class _Upload:
    id: str
    device: str
    path: tuple[str, ...]
    size: int
    sha256: str | None
    part: Path
    identity: tuple[int, int]
    chunk_size: int
    duplicate: asyncio.Task[dict[str, str | int] | None] | None = None
    received: dict[int, str] = field(default_factory=dict)
    written: int = 0
    canceled: bool = False
    result: dict[str, str | int] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def owns_stage(self) -> bool:
        try:
            info = self.part.lstat()
        except FileNotFoundError:
            return False
        return (
            stat.S_ISREG(info.st_mode)
            and not _is_reparse_point(info)
            and _identity(info) == self.identity
        )

    def check_stage(self) -> None:
        if not self.owns_stage():
            raise UploadError(
                503, "storage_unavailable", "The upload's staging file changed."
            )

    def remove_stage(self) -> None:
        if self.owns_stage():
            self.part.unlink(missing_ok=True)

    @property
    def chunk_count(self) -> int:
        return (self.size + self.chunk_size - 1) // self.chunk_size


class UploadStore:
    """Keep an upload's verified chunks across HTTP requests for the server lifetime."""

    def __init__(
        self,
        received_dir: Path | str,
        *,
        max_file_size: int | None = None,
        max_parallel_uploads: int = 3,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        observer: TransferObserver | None = None,
    ) -> None:
        if chunk_size <= 0 or max_parallel_uploads <= 0:
            raise ValueError(
                "Chunk size and maximum parallel uploads must be positive."
            )
        if max_file_size is not None and max_file_size < 0:
            raise ValueError("Maximum file size cannot be negative.")
        self.received_dir = Path(received_dir).absolute()
        self._observer = observer
        self.max_file_size = max_file_size
        self.max_parallel_uploads = max_parallel_uploads
        self.chunk_size = chunk_size
        self._uploads: dict[str, _Upload] = {}
        self._create_lock = asyncio.Lock()
        self._create_keys: dict[
            tuple[str, str], tuple[tuple[object, ...], _Upload]
        ] = {}

    async def create(
        self,
        device: str,
        *,
        name: object,
        size: object,
        sha256: object = None,
        relpath: object = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        path = _upload_path(name, relpath)
        expected_hash = _digest(sha256, optional=True)
        if type(size) is not int or not 0 <= size <= 2**63 - 1:
            raise UploadError(
                400, "invalid_size", "Provide a valid non-negative byte count."
            )
        if self.max_file_size is not None and size > self.max_file_size:
            raise UploadError(
                413, "file_too_large", "The file exceeds the PC's configured limit."
            )
        if idempotency_key is not None and not _CREATE_KEY.fullmatch(idempotency_key):
            raise UploadError(
                400,
                "invalid_idempotency_key",
                "Use 1-128 printable ASCII characters without spaces.",
            )
        key = (device, idempotency_key) if idempotency_key is not None else None
        manifest = (name, size, expected_hash, relpath)
        async with self._create_lock:
            prior = self._create_keys.get(key) if key is not None else None
            if prior is not None:
                previous_manifest, upload = prior
                if previous_manifest != manifest:
                    raise UploadError(
                        409,
                        "idempotency_conflict",
                        "This key already identifies a different upload.",
                    )
            else:
                upload = await _finish_io(
                    self._create(device, path, size, expected_hash, key, manifest)
                )

        result: dict[str, Any] = {
            "upload_id": upload.id,
            "chunk_size": upload.chunk_size,
        }
        if upload.duplicate is not None:
            duplicate = await asyncio.shield(upload.duplicate)
            if duplicate is not None:
                result["duplicate"] = duplicate
        return result

    async def _create(
        self,
        device: str,
        path: tuple[str, ...],
        size: int,
        sha256: str | None,
        key: tuple[str, str] | None,
        manifest: tuple[object, ...],
    ) -> _Upload:
        active = [
            upload
            for upload in self._uploads.values()
            if upload.result is None and not upload.canceled
        ]
        if (
            sum(upload.device == device for upload in active)
            >= self.max_parallel_uploads
        ):
            raise UploadError(
                429, "upload_limit", "This device has too many active uploads."
            )
        reserved = sum(upload.size - upload.written for upload in active)
        try:
            part, identity = await asyncio.to_thread(self._new_part, size + reserved)
        except OSError as exc:
            raise _storage_error(exc) from exc
        upload = _Upload(
            uuid.uuid4().hex,
            device,
            path,
            size,
            sha256,
            part,
            identity,
            self.chunk_size,
        )
        if sha256 is not None:
            upload.duplicate = asyncio.create_task(
                asyncio.to_thread(self._find_duplicate, path, size, sha256)
            )
        self._uploads[upload.id] = upload
        self._report_progress(upload)
        if key is not None:
            self._create_keys[key] = (manifest, upload)
        return upload

    def _new_part(self, required_bytes: int) -> tuple[Path, tuple[int, int]]:
        self.received_dir.mkdir(parents=True, exist_ok=True)
        _check_directory(self.received_dir)
        if shutil.disk_usage(self.received_dir).free < required_bytes:
            raise UploadError(
                507, "insufficient_storage", "The PC has insufficient free space."
            )
        fd, name = tempfile.mkstemp(
            prefix=".duet-", suffix=".part", dir=self.received_dir
        )
        try:
            return Path(name), _identity(os.fstat(fd))
        finally:
            os.close(fd)

    def _find_duplicate(
        self, path: tuple[str, ...], size: int, sha256: str
    ) -> dict[str, str | int] | None:
        parent = self.received_dir.joinpath(*path[:-1])
        try:
            with _duplicate_directory(parent) as directory:
                for target in self._targets(parent, path[-1]):
                    info = (
                        target.lstat()
                        if directory is None
                        else os.stat(
                            target.name, dir_fd=directory, follow_symlinks=False
                        )
                    )
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or _is_reparse_point(info)
                        or info.st_size != size
                    ):
                        continue
                    with _open_duplicate(target, directory) as source:
                        opened = os.fstat(source.fileno())
                        if (
                            not stat.S_ISREG(opened.st_mode)
                            or _identity(opened) != _identity(info)
                            or (
                                directory is None
                                and not _windows_path_matches(source.fileno(), target)
                            )
                        ):
                            continue
                        if hashlib.file_digest(source, "sha256").hexdigest() == sha256:
                            return {
                                "relpath": target.relative_to(
                                    self.received_dir
                                ).as_posix(),
                                "size": size,
                                "sha256": sha256,
                            }
        except (OSError, UploadError):
            # Existing files are optional matches and may change during discovery.
            pass
        return None

    @staticmethod
    def _targets(parent: Path, name: str) -> Iterator[Path]:
        stem, suffix = _split_component(name)
        for number in count():
            counter = f" ({number})" if number else ""
            yield parent / _fit_component(stem, suffix, counter)

    def _get(
        self, device: str, upload_id: str, *, include_canceled: bool = False
    ) -> _Upload:
        upload = self._uploads.get(upload_id)
        if (
            upload is None
            or upload.device != device
            or (upload.canceled and not include_canceled)
        ):
            raise UploadError(404, "upload_not_found", "Upload not found.")
        return upload

    def status(self, device: str, upload_id: str) -> dict[str, Any]:
        upload = self._get(device, upload_id)
        return {
            "upload_id": upload.id,
            "chunk_size": upload.chunk_size,
            "received": sorted(upload.received),
            "size": upload.size,
            "completed": upload.result is not None,
        }

    async def put_chunk(
        self,
        device: str,
        upload_id: str,
        index: int,
        sha256: str | None,
        body: AsyncIterable[bytes],
        content_length: int | None = None,
    ) -> None:
        upload = self._get(device, upload_id)
        expected_hash = _digest(sha256)
        if not 0 <= index < upload.chunk_count:
            raise UploadError(
                400, "invalid_chunk", "Chunk index is outside this upload."
            )
        expected_size = min(upload.chunk_size, upload.size - index * upload.chunk_size)
        if content_length is not None and content_length != expected_size:
            raise UploadError(
                400, "invalid_chunk_size", "The chunk has the wrong byte count."
            )
        async with upload.lock:
            self._get(device, upload_id)
            previous_hash = upload.received.get(index)
            if previous_hash is not None and expected_hash != previous_hash:
                raise UploadError(
                    409,
                    "chunk_conflict",
                    "A different chunk already occupies this index.",
                )
            data = bytearray()
            async for piece in body:
                if len(data) + len(piece) > expected_size:
                    raise UploadError(
                        400, "invalid_chunk_size", "The chunk has the wrong byte count."
                    )
                data.extend(piece)
            if len(data) != expected_size:
                raise UploadError(
                    400, "invalid_chunk_size", "The chunk has the wrong byte count."
                )
            await _finish_io(
                self._save_chunk(upload, index, expected_hash, data, previous_hash)
            )

    async def _save_chunk(
        self,
        upload: _Upload,
        index: int,
        expected_hash: str | None,
        data: bytearray,
        previous_hash: str | None,
    ) -> None:
        digest = await asyncio.to_thread(lambda: hashlib.sha256(data).hexdigest())
        if digest != expected_hash:
            raise UploadError(
                422, "chunk_hash_mismatch", "The chunk's SHA-256 does not match."
            )
        if previous_hash is None:
            try:
                await asyncio.to_thread(self._write_chunk, upload, index, data)
            except OSError as exc:
                raise _storage_error(exc) from exc
            upload.received[index] = digest
            upload.written += len(data)
            self._report_progress(upload)

    def _report_progress(self, upload: _Upload) -> None:
        if self._observer is not None:
            self._observer.update(
                upload.id, upload.path[-1], "upload", upload.written, upload.size
            )

    @staticmethod
    def _write_chunk(upload: _Upload, index: int, data: bytearray) -> None:
        upload.check_stage()
        with upload.part.open("r+b") as output:
            if _identity(os.fstat(output.fileno())) != upload.identity:
                raise UploadError(
                    503, "storage_unavailable", "The upload's staging file changed."
                )
            output.seek(index * upload.chunk_size)
            output.write(data)

    async def complete(
        self, device: str, upload_id: str, *, sha256: object = None
    ) -> dict[str, str | int]:
        upload = self._get(device, upload_id)
        digest = _digest(sha256, optional=True)
        async with upload.lock:
            self._get(device, upload_id)
            declared = upload.sha256
            if declared is not None and digest is not None and digest != declared:
                raise UploadError(
                    409,
                    "hash_conflict",
                    "The completion digest differs from the manifest.",
                )
            expected_hash = declared or digest
            if upload.result is not None:
                if (
                    expected_hash is not None
                    and upload.result["sha256"] != expected_hash
                ):
                    raise UploadError(
                        422, "file_hash_mismatch", "The file's SHA-256 does not match."
                    )
                return upload.result
            if len(upload.received) != upload.chunk_count:
                raise UploadError(
                    409, "missing_chunks", "Some chunks have not arrived."
                )
            return await _finish_io(self._complete(upload, expected_hash))

    async def _complete(
        self, upload: _Upload, expected_hash: str | None
    ) -> dict[str, str | int]:
        try:
            upload.result = await asyncio.to_thread(
                self._publish, upload, expected_hash
            )
        except OSError as exc:
            raise _storage_error(exc) from exc
        if self._observer is not None:
            self._observer.finish(
                upload.id, "completed", name=str(upload.result["name"])
            )
        return upload.result

    def _publish(
        self, upload: _Upload, expected_hash: str | None
    ) -> dict[str, str | int]:
        _check_directory(self.received_dir)
        upload.check_stage()
        with upload.part.open("rb") as source:
            info = os.fstat(source.fileno())
            if _identity(info) != upload.identity:
                raise UploadError(
                    503, "storage_unavailable", "The upload's staging file changed."
                )
            if info.st_size != upload.size:
                raise UploadError(
                    422, "file_size_mismatch", "The file's final size does not match."
                )
            whole_file = hashlib.sha256()
            for index in range(upload.chunk_count):
                chunk = source.read(upload.chunk_size)
                if hashlib.sha256(chunk).hexdigest() != upload.received[index]:
                    raise UploadError(
                        422,
                        "file_hash_mismatch",
                        "A stored chunk failed SHA-256 verification.",
                    )
                whole_file.update(chunk)
            digest = whole_file.hexdigest()
            if expected_hash is not None and digest != expected_hash:
                raise UploadError(
                    422, "file_hash_mismatch", "The file's SHA-256 does not match."
                )

        parent = self.received_dir
        for component in upload.path[:-1]:
            parent /= component
            try:
                parent.mkdir()
            except FileExistsError:
                pass
            _check_directory(parent)

        upload.check_stage()
        for target in self._targets(parent, upload.path[-1]):
            try:
                if os.name == "nt":
                    os.rename(upload.part, target)
                else:
                    os.link(upload.part, target)
                break
            except FileExistsError:
                continue

        # On POSIX the link atomically publishes the verified inode without replacing
        # an existing filename. Failure to remove the staging name must not republish.
        if os.name != "nt":
            try:
                upload.remove_stage()
            except OSError:
                pass
        return {
            "name": target.name,
            "relpath": target.relative_to(self.received_dir).as_posix(),
            "size": upload.size,
            "sha256": digest,
        }

    async def discard(self, device: str, upload_id: str) -> None:
        upload = self._get(device, upload_id, include_canceled=True)
        async with upload.lock:
            if upload.result is not None:
                raise UploadError(
                    409, "upload_completed", "A completed upload cannot be discarded."
                )
            if not upload.canceled:
                await _finish_io(self._discard(upload))

    async def _discard(self, upload: _Upload) -> None:
        try:
            await asyncio.to_thread(upload.remove_stage)
        except OSError as exc:
            raise _storage_error(exc) from exc
        upload.canceled = True
        if self._observer is not None:
            self._observer.finish(upload.id, "cancelled")
        upload.received.clear()

    async def close(self) -> None:
        """Remove staging files when the owning application shuts down."""
        async with self._create_lock:
            for upload in self._uploads.values():
                async with upload.lock:
                    if upload.canceled or (
                        os.name == "nt" and upload.result is not None
                    ):
                        continue
                    try:
                        await asyncio.to_thread(upload.remove_stage)
                    except OSError:
                        pass
            checks = [
                upload.duplicate
                for upload in self._uploads.values()
                if upload.duplicate
            ]
            if checks:
                await asyncio.gather(*checks, return_exceptions=True)
            self._uploads.clear()
            self._create_keys.clear()

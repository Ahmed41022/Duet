"""Open offered files safely and stream deterministic ZIP64 archives."""

import os
import stat
import struct
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from duet.core.outbox import Entry, OfferChanged, Source, is_link, signature

CHUNK_SIZE = 256 * 1024


def _check(value: os.stat_result, entry: Entry) -> None:
    if is_link(value) or signature(value) != entry.signature:
        raise OfferChanged("The source changed since it was offered")


def verify(source: Source) -> None:
    if source.path.resolve(strict=True) != source.path:
        raise OfferChanged("The source was redirected")
    for entry in source.entries:
        path = source.path.joinpath(*entry.relative)
        _check(path.lstat(), entry)


def _open_posix(path: Path) -> int:
    parts = path.parts
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    directory = os.open(parts[0], flags | os.O_DIRECTORY)
    try:
        for component in parts[1:-1]:
            child = os.open(component, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = child
        return os.open(parts[-1], flags, dir_fd=directory)
    finally:
        os.close(directory)


def _verify_windows_handle(file: BinaryIO, path: Path) -> None:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    get_path = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
    get_path.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    get_path.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_path(msvcrt.get_osfhandle(file.fileno()), buffer, len(buffer), 0)
    expected = str(path)
    if expected.startswith("\\\\") and not expected.startswith("\\\\?\\"):
        expected = "\\\\?\\UNC\\" + expected[2:]
    elif not expected.startswith("\\\\?\\"):
        expected = "\\\\?\\" + expected
    if not length or length >= len(buffer) or Path(buffer.value) != Path(expected):
        raise OfferChanged("The source was redirected")


@contextmanager
def open_entry(source: Source, entry: Entry) -> Iterator[BinaryIO]:
    path = source.path.joinpath(*entry.relative)
    _check(path.lstat(), entry)
    if os.name == "nt":
        stream = path.open("rb")
    else:
        stream = os.fdopen(_open_posix(path), "rb")
    with stream:
        if os.name == "nt":
            _verify_windows_handle(stream, path)
        _check(os.fstat(stream.fileno()), entry)
        yield stream


def file_chunks(
    file: BinaryIO, entry: Entry, start: int, length: int
) -> Iterator[bytes]:
    file.seek(start)
    while length:
        data = file.read(min(CHUNK_SIZE, length))
        _check(os.fstat(file.fileno()), entry)
        if not data:
            raise OfferChanged("The source was truncated")
        length -= len(data)
        yield data


# ZIP_STORED with UTF-8 names and ZIP64 sizes, offsets, and data descriptors.
# Fixed timestamps keep the archive byte-for-byte stable across requests.
_FLAGS = 0x808
_SENTINEL = 0xFFFFFFFF
_ZIP_DATE = 33  # 1980-01-01


def zip_chunks(source: Source) -> Iterator[bytes]:
    central = []
    offset = 0
    for entry in source.entries:
        name = entry.zip_name()
        size = 0 if entry.is_folder else entry.size
        local = (
            struct.pack(
                "<I5H3I2H",
                0x04034B50,
                45,
                _FLAGS,
                0,
                0,
                _ZIP_DATE,
                0,
                _SENTINEL,
                _SENTINEL,
                len(name),
                20,
            )
            + name
            + struct.pack("<HHQQ", 1, 16, 0, 0)
        )
        yield local
        crc = 0
        if not entry.is_folder:
            with open_entry(source, entry) as file:
                for chunk in file_chunks(file, entry, 0, size):
                    crc = zlib.crc32(chunk, crc)
                    yield chunk
        yield struct.pack("<IIQQ", 0x08074B50, crc, size, size)
        mode = (stat.S_IFDIR | 0o755) if entry.is_folder else (stat.S_IFREG | 0o644)
        attributes = (mode << 16) | (0x10 if entry.is_folder else 0)
        central.append(
            struct.pack(
                "<I6H3I5H2I",
                0x02014B50,
                0x32D,
                45,
                _FLAGS,
                0,
                0,
                _ZIP_DATE,
                crc,
                _SENTINEL,
                _SENTINEL,
                len(name),
                28,
                0,
                0,
                0,
                attributes,
                _SENTINEL,
            )
            + name
            + struct.pack("<HHQQQ", 1, 24, size, size, offset)
        )
        offset += len(local) + size + 24
    verify(source)
    yield from central
    length = sum(map(len, central))
    count = len(central)
    yield struct.pack(
        "<IQ2H2I4Q", 0x06064B50, 44, 0x32D, 45, 0, 0, count, count, length, offset
    )
    yield struct.pack("<IIQI", 0x07064B50, 0, offset + length, 1)
    yield struct.pack(
        "<I4H2IH", 0x06054B50, 0, 0, 0xFFFF, 0xFFFF, _SENTINEL, _SENTINEL, 0
    )

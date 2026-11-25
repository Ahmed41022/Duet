"""Persistent local offers shared by the CLI, desktop, and HTTP server."""

import json
import os
import secrets
import sqlite3
import stat
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path


def default_database() -> Path:
    if os.name == "nt" and (location := os.environ.get("LOCALAPPDATA")):
        return Path(location) / "Duet" / "outbox.sqlite3"
    if os.name != "nt" and (location := os.environ.get("XDG_DATA_HOME")):
        return Path(location) / "duet" / "outbox.sqlite3"
    return Path.home() / ".duet" / "outbox.sqlite3"


class OfferChanged(OSError):
    """The source changed since the PC user added it to the outbox."""


def is_link(value: os.stat_result) -> bool:
    return stat.S_ISLNK(value.st_mode) or bool(
        getattr(value, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def download_name(name: str) -> str:
    name = "".join(
        char if char.isprintable() and char not in '<>:"/\\|?*' else "_"
        for char in name
    ).strip(" .")
    stem = name.partition(".")[0].upper().rstrip()
    if stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} or (
        stem.startswith(("COM", "LPT"))
        and len(stem) == 4
        and stem[-1] in "123456789¹²³"
    ):
        name = "_" + name
    prefix = name.encode("utf-8")[:220].decode("utf-8", "ignore")
    return prefix.rstrip(" .") or "download"


@dataclass(frozen=True, slots=True)
class Entry:
    relative: tuple[str, ...]
    archive: str
    is_folder: bool
    signature: tuple[int, ...]

    @property
    def size(self) -> int:
        return self.signature[3]

    def zip_name(self) -> bytes:
        return (self.archive + ("/" if self.is_folder else "")).encode("utf-8")


def _zip_size(entries: tuple[Entry, ...]) -> int:
    # Local and central headers, ZIP64 extras, and descriptor per entry.
    # The trailer contains the ZIP64 end, locator, and standard end records.
    return (
        sum(
            30
            + 20
            + 24
            + 46
            + 28
            + 2 * len(entry.zip_name())
            + (0 if entry.is_folder else entry.size)
            for entry in entries
        )
        + 56
        + 20
        + 22
    )


def signature(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


@dataclass(frozen=True, slots=True)
class Offer:
    id: str
    name: str
    size: int
    is_folder: bool

    def public(self) -> dict[str, str | int | bool]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Source:
    offer: Offer
    path: Path
    entries: tuple[Entry, ...]


def _inventory(path: Path, archive_root: str) -> tuple[Entry, ...]:
    result = []

    def collect(relative: tuple[str, ...], archive: str) -> None:
        current = path.joinpath(*relative)
        value = current.lstat()
        if is_link(value):
            return
        folder = stat.S_ISDIR(value.st_mode)
        if not folder and not stat.S_ISREG(value.st_mode):
            return
        if len(archive.encode("utf-8")) >= 65_535:
            raise ValueError("A folder path is too long for a ZIP entry")
        result.append(Entry(relative, archive, folder, signature(value)))
        if folder:
            names = set()
            with os.scandir(current) as entries:
                for child in sorted(entries, key=lambda entry: entry.name):
                    child_stat = child.stat(follow_symlinks=False)
                    if is_link(child_stat) or not (
                        stat.S_ISDIR(child_stat.st_mode)
                        or stat.S_ISREG(child_stat.st_mode)
                    ):
                        continue
                    name = base = download_name(child.name)
                    index = 1
                    while name.casefold() in names:
                        name = f"{base} ({index})"
                        index += 1
                    names.add(name.casefold())
                    collect((*relative, child.name), f"{archive}/{name}")
            if signature(current.lstat()) != signature(value):
                raise OfferChanged("A folder changed while it was being offered")

    collect((), archive_root)
    if not result:
        raise ValueError("Select a regular file or folder")
    return tuple(result)


class Outbox:
    """Local access to offers. Public objects contain no source paths."""

    def __init__(self, database: Path | None = None) -> None:
        self.database = Path(database) if database is not None else default_database()

    @contextmanager
    def _connect(self):
        if not self.database.parent.is_dir():
            self.database.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS offers ("
                "id TEXT PRIMARY KEY, name TEXT NOT NULL, size INTEGER NOT NULL, "
                "is_folder INTEGER NOT NULL, path TEXT NOT NULL, "
                "manifest TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS revision ("
                "key INTEGER PRIMARY KEY CHECK(key = 1), value INTEGER NOT NULL)"
            )
            yield connection

    @staticmethod
    def _bump(connection: sqlite3.Connection) -> None:
        connection.execute(
            "INSERT INTO revision VALUES (1, 1) "
            "ON CONFLICT(key) DO UPDATE SET value = value + 1"
        )

    def offer(self, path: Path | str) -> Offer:
        path = Path(path).expanduser()
        if is_link(path.lstat()):
            raise ValueError("Select the original file or folder")
        name = download_name(path.name or "download")
        path = path.resolve(strict=True)
        entries = _inventory(path, name)
        folder = entries[0].is_folder
        offer = Offer(
            secrets.token_hex(16),
            f"{name}.zip" if folder else name,
            _zip_size(entries) if folder else entries[0].size,
            folder,
        )
        manifest = json.dumps([asdict(entry) for entry in entries])
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO offers VALUES (?, ?, ?, ?, ?, ?)",
                (
                    offer.id,
                    offer.name,
                    offer.size,
                    folder,
                    json.dumps(str(path)),
                    manifest,
                ),
            )
            self._bump(connection)
        return offer

    def list(self) -> list[Offer]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, name, size, is_folder FROM offers ORDER BY rowid"
            ).fetchall()
        return [
            Offer(identity, name, size, bool(folder))
            for identity, name, size, folder in rows
        ]

    def get(self, identity: str) -> Source | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT name, size, is_folder, path, manifest FROM offers WHERE id = ?",
                (identity,),
            ).fetchone()
        if row is None:
            return None
        name, size, folder, path, manifest = row
        return Source(
            Offer(identity, name, size, bool(folder)),
            Path(json.loads(path)),
            tuple(
                Entry(
                    tuple(e["relative"]),
                    e["archive"],
                    e["is_folder"],
                    tuple(e["signature"]),
                )
                for e in json.loads(manifest)
            ),
        )

    def revision(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM revision WHERE key = 1"
            ).fetchone()
        return row[0] if row else 0

    def revoke(self, identity: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM offers WHERE id = ?", (identity,))
            if cursor.rowcount:
                self._bump(connection)
                return True
        return False

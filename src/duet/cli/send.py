"""Offer a file or folder to paired phones through the local outbox."""

import sqlite3
import sys
from pathlib import Path

from duet.core.outbox import Outbox


def run_send(path: Path | str, *, outbox: Outbox | None = None) -> int:
    try:
        store = outbox if outbox is not None else Outbox()
        offer = store.offer(path)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Could not add to the outbox: {exc}", file=sys.stderr)
        return 1
    print(f"Added to the outbox: {offer.name}")
    return 0

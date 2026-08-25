"""Single-writer ownership for the PM scheduler runtime (ATLAS-068M).

The supported production topology is one Linux host and one file-backed SQLite
operational store.  A non-blocking exclusive ``flock`` on the existing database
file gives every checkout targeting that filesystem object the same kernel-owned
writer boundary.  The descriptor is held by :func:`pm_writer_ownership` for the
caller's complete context and is close-on-exec so an unrelated child cannot
inherit PM authority.

This module deliberately does not provide a fallback for in-memory SQLite,
SQLite URI stores, or network/database-server dialects.  An OS-local file lock
cannot prove distributed ownership, so those topologies fail closed.
"""

from __future__ import annotations

import errno
import fcntl
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from atlas.storage.db import Database

SUPPORTED_TOPOLOGY = (
    "ATLAS-068M supports only single-host file-backed SQLite PM ownership"
)


class PMWriterOwnershipError(RuntimeError):
    """Base class for deterministic PM writer-ownership refusals."""


class PMWriterAlreadyActiveError(PMWriterOwnershipError):
    """Another process already owns the operational store's PM scheduler."""

    def __init__(self) -> None:
        super().__init__("PM writer already active")


class PMWriterOwnershipUnsupportedError(PMWriterOwnershipError):
    """The store cannot use the approved single-host SQLite ownership guard."""

    def __init__(self) -> None:
        super().__init__(f"PM writer ownership unsupported: {SUPPORTED_TOPOLOGY}")


class PMWriterOwnershipUnavailableError(PMWriterOwnershipError):
    """The supported store exists, but its ownership lock cannot be established."""

    def __init__(self) -> None:
        super().__init__(
            "PM writer ownership unavailable for the file-backed SQLite store"
        )


def _canonical_sqlite_path(database: Database) -> Path:
    """Resolve the structured SQLAlchemy identity to one existing SQLite file.

    No raw URL is reparsed or rendered.  SQLite URI names are rejected rather
    than guessed because they may denote memory or other non-file storage.
    """

    url = database.engine.url
    database_name = url.database
    if (
        url.get_backend_name() != "sqlite"
        or not database_name
        or database_name == ":memory:"
        or database_name.startswith("file:")
    ):
        raise PMWriterOwnershipUnsupportedError

    try:
        return Path(database_name).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise PMWriterOwnershipUnavailableError from error


def _open_ownership_descriptor(path: Path) -> int:
    """Open an existing store read/write, without creation or truncation."""

    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PMWriterOwnershipUnavailableError from error

    try:
        # ``O_CLOEXEC`` is available on the supported Linux host.  Keeping the
        # explicit inheritable setting makes the guarantee true on an
        # implementation where the flag is absent or ignored.
        os.set_inheritable(descriptor, False)
        opened = os.fstat(descriptor)
        current = path.stat()
        valid_identity = stat.S_ISREG(opened.st_mode) and (
            opened.st_dev,
            opened.st_ino,
        ) == (current.st_dev, current.st_ino)
    except OSError:
        os.close(descriptor)
        raise PMWriterOwnershipUnavailableError from None
    if not valid_identity:
        os.close(descriptor)
        raise PMWriterOwnershipUnavailableError
    return descriptor


@contextmanager
def pm_writer_ownership(database: Database) -> Iterator[None]:
    """Hold exclusive PM writer authority for one complete scheduler call.

    Acquisition is non-blocking.  The kernel releases the advisory lock when
    the descriptor closes, including process death; no PID or stale lock file
    participates in authority.
    """

    descriptor = _open_ownership_descriptor(_canonical_sqlite_path(database))
    acquired = False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise PMWriterAlreadyActiveError from None
            raise PMWriterOwnershipUnavailableError from None
        yield
    finally:
        if acquired:
            # Closing the descriptor is the authoritative release path.
            with suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

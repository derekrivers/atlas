"""Engine and session factory (ATLAS-18).

Default database: SQLite at .atlas/atlas.db in the repo root
(gitignored). Override with ATLAS_DATABASE_URL. All sessions are
created here and consumed inside the storage package only; the public
currency of the package is Pydantic models (knowledge-core "Storage
architecture").
"""

from __future__ import annotations

import os
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from atlas.storage.tables import Base

DEFAULT_SQLITE_PATH = Path(".atlas") / "atlas.db"
ENV_VAR = "ATLAS_DATABASE_URL"


def resolve_url(url: str | None = None) -> str:
    if url:
        return url
    from_env = os.environ.get(ENV_VAR)
    if from_env:
        return from_env
    return f"sqlite:///{DEFAULT_SQLITE_PATH}"


class Database:
    """Owns the engine and hands sessions to the repositories."""

    def __init__(self, url: str | None = None) -> None:
        resolved = resolve_url(url)
        if resolved.startswith("sqlite:///") and ":memory:" not in resolved:
            Path(resolved.removeprefix("sqlite:///")).parent.mkdir(
                parents=True, exist_ok=True
            )
        self._engine = sa.create_engine(resolved)
        self._sessions = sessionmaker(self._engine)

    @property
    def engine(self) -> sa.Engine:
        return self._engine

    def session(self) -> Session:
        """For storage-package internals; consumers use repositories."""
        return self._sessions()

    def create_all(self) -> None:
        """Create the schema directly (test fixtures); real databases
        migrate via Alembic."""
        Base.metadata.create_all(self._engine)

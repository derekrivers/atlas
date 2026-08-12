"""Server-owned repository policy for acceptance-session HTTP requests."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from fastapi import HTTPException, status

ACCEPTANCE_REPOSITORIES_ENV: Final = "ATLAS_ACCEPTANCE_REPOSITORIES"


@dataclass(frozen=True)
class ConfiguredAcceptanceRepository:
    """One canonical owner/repository identity admitted by server policy."""

    owner: str
    name: str

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"


def _repository_component(value: str) -> str:
    if (
        not value
        or len(value) > 128
        or value in {".", ".."}
        or value.strip() != value
        or any(
            character
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
            for character in value
        )
    ):
        raise ValueError("repository slug must contain two safe name components")
    return value


def parse_repository_slug(slug: str) -> ConfiguredAcceptanceRepository:
    """Parse a slug only; URLs, ports, paths, queries and fragments are invalid."""

    if not isinstance(slug, str) or slug.count("/") != 1:
        raise ValueError("repository slug must use owner/repository")
    owner, name = slug.split("/", maxsplit=1)
    return ConfiguredAcceptanceRepository(
        owner=_repository_component(owner),
        name=_repository_component(name),
    )


class AcceptanceRepositoryPolicy:
    """Case-insensitive allowlist that returns configured canonical spelling."""

    def __init__(self, repositories: Iterable[str]) -> None:
        configured: dict[tuple[str, str], ConfiguredAcceptanceRepository] = {}
        for slug in repositories:
            repository = parse_repository_slug(slug)
            identity = (repository.owner.casefold(), repository.name.casefold())
            if identity in configured:
                raise ValueError("acceptance repository policy contains a duplicate")
            configured[identity] = repository
        self._configured = configured

    def require(self, slug: str) -> ConfiguredAcceptanceRepository:
        """Resolve one parsed identity without ever treating it as a URL."""

        try:
            requested = parse_repository_slug(slug)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="repository is not permitted for acceptance sessions",
            ) from error
        configured = self._configured.get(
            (requested.owner.casefold(), requested.name.casefold())
        )
        if configured is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="repository is not permitted for acceptance sessions",
            )
        return configured


def acceptance_repositories_from_env() -> tuple[str, ...]:
    """Read the comma-separated server allowlist without accepting empty entries."""

    raw = os.environ.get(ACCEPTANCE_REPOSITORIES_ENV, "")
    if not raw:
        return ()
    entries = tuple(item.strip() for item in raw.split(","))
    if any(not item for item in entries):
        raise ValueError(f"{ACCEPTANCE_REPOSITORIES_ENV} contains an empty entry")
    return entries

"""Shared loopback writable-API startup security preconditions."""

from __future__ import annotations

import ipaddress
import math
import os
from collections.abc import Mapping
from enum import StrEnum
from typing import Final

OPERATOR_TOKEN_ENV: Final = "ATLAS_OPERATOR_TOKEN"
WRITABLE_ROUTES_ENV: Final = "ATLAS_API_ENABLE_WRITES"
WRITABLE_BIND_HOST_ENV: Final = "ATLAS_API_BIND_HOST"
MIN_OPERATOR_TOKEN_CHARS: Final = 43
MIN_OPERATOR_TOKEN_ESTIMATED_BITS: Final = 128.0
MAX_OPERATOR_TOKEN_CHARS: Final = 512
LOOPBACK_HOSTNAMES: Final = frozenset({"localhost"})


class WritableApiPreconditionCode(StrEnum):
    """Named startup precondition failures for writable API serving."""

    TOKEN_MISSING = "ATLAS_OPERATOR_TOKEN_MISSING"
    TOKEN_WEAK = "ATLAS_OPERATOR_TOKEN_WEAK"
    REMOTE_UNSUPPORTED = "ATLAS_WRITABLE_REMOTE_UNSUPPORTED"


class WritableApiPreconditionError(RuntimeError):
    """Secret-free startup refusal for writable API serving."""

    def __init__(self, code: WritableApiPreconditionCode, message: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {message}")


def writable_routes_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Resolve the process-level writable-route switch."""
    value = (environ or os.environ).get(WRITABLE_ROUTES_ENV, "")
    return value in {"1", "true", "TRUE", "yes", "on"}


def operator_token_from_env(environ: Mapping[str, str] | None = None) -> str | None:
    """Read the operator bootstrap token without logging or transforming it."""
    return (environ or os.environ).get(OPERATOR_TOKEN_ENV)


def bind_host_from_env(environ: Mapping[str, str] | None = None) -> str:
    """Read the API bind host used for writable startup validation."""
    return (environ or os.environ).get(WRITABLE_BIND_HOST_ENV, "127.0.0.1")


def _estimated_entropy_bits(value: str) -> float:
    counts = {character: value.count(character) for character in set(value)}
    entropy_per_character = -sum(
        (count / len(value)) * math.log2(count / len(value))
        for count in counts.values()
    )
    return entropy_per_character * len(value)


def validate_operator_token(token: str | None) -> str:
    """Validate the documented bootstrap-token length and entropy contract."""
    if token is None or token == "":
        raise WritableApiPreconditionError(
            WritableApiPreconditionCode.TOKEN_MISSING,
            f"{OPERATOR_TOKEN_ENV} is required before enabling writable routes",
        )
    if len(token) < MIN_OPERATOR_TOKEN_CHARS or len(token) > MAX_OPERATOR_TOKEN_CHARS:
        raise WritableApiPreconditionError(
            WritableApiPreconditionCode.TOKEN_WEAK,
            (
                f"{OPERATOR_TOKEN_ENV} must be {MIN_OPERATOR_TOKEN_CHARS}-"
                f"{MAX_OPERATOR_TOKEN_CHARS} printable characters"
            ),
        )
    if any(ord(character) < 33 or ord(character) > 126 for character in token):
        raise WritableApiPreconditionError(
            WritableApiPreconditionCode.TOKEN_WEAK,
            f"{OPERATOR_TOKEN_ENV} must use printable non-whitespace ASCII",
        )
    if _estimated_entropy_bits(token) < MIN_OPERATOR_TOKEN_ESTIMATED_BITS:
        raise WritableApiPreconditionError(
            WritableApiPreconditionCode.TOKEN_WEAK,
            f"{OPERATOR_TOKEN_ENV} does not meet the entropy floor",
        )
    return token


def _split_host(host: str) -> str | None:
    if not host:
        return None
    if host.startswith("["):
        closing = host.find("]")
        if closing == -1:
            return None
        return host[1:closing].lower()
    if host.count(":") == 1:
        hostname, port = host.rsplit(":", maxsplit=1)
        if not port.isdigit():
            return None
        return hostname.lower()
    if ":" in host:
        return host.lower()
    return host.lower()


def is_loopback_host(host: str) -> bool:
    """Return whether a bind or HTTP host value is loopback-only."""
    hostname = _split_host(host)
    if hostname is None:
        return False
    if hostname in LOOPBACK_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def assert_writable_startup_preconditions(
    *,
    operator_token: str | None,
    bind_host: str,
) -> str:
    """Fail closed before serving writable routes in unsupported topologies."""
    token = validate_operator_token(operator_token)
    if not is_loopback_host(bind_host):
        raise WritableApiPreconditionError(
            WritableApiPreconditionCode.REMOTE_UNSUPPORTED,
            "writable routes are supported only on loopback HTTP",
        )
    return token

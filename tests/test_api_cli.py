"""CLI launch wiring for the HTTP adapter."""

from unittest.mock import Mock

import pytest
import uvicorn

from atlas import cli


def test_api_serve_uses_import_string_and_local_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = Mock()
    monkeypatch.setattr(uvicorn, "run", run)

    assert cli.main(["api", "serve"]) == cli.EXIT_OK
    run.assert_called_once_with(
        "atlas.api.app:app",
        host="127.0.0.1",
        port=8000,
    )


def test_api_serve_forwards_host_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock()
    monkeypatch.setattr(uvicorn, "run", run)

    assert (
        cli.main(["api", "serve", "--host", "0.0.0.0", "--port", "8099"]) == cli.EXIT_OK
    )
    run.assert_called_once_with(
        "atlas.api.app:app",
        host="0.0.0.0",
        port=8099,
    )

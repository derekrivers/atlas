"""CLI launch wiring for the HTTP adapter."""

import os
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


def test_api_serve_enable_writes_requires_operator_token(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run = Mock()
    monkeypatch.setattr(uvicorn, "run", run)
    monkeypatch.delenv("ATLAS_OPERATOR_TOKEN", raising=False)

    assert cli.main(["api", "serve", "--enable-writes"]) == cli.EXIT_PRECONDITION

    err = capsys.readouterr().err
    assert "ATLAS_OPERATOR_TOKEN_MISSING" in err
    assert "secret" not in err.lower()
    run.assert_not_called()


def test_api_serve_enable_writes_refuses_non_loopback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run = Mock()
    monkeypatch.setattr(uvicorn, "run", run)
    monkeypatch.setenv(
        "ATLAS_OPERATOR_TOKEN",
        "atlas-operator-token-0123456789ABCDEFGHJKLMNPQRSTxyz!@#",
    )

    assert (
        cli.main(["api", "serve", "--enable-writes", "--host", "0.0.0.0"])
        == cli.EXIT_PRECONDITION
    )

    err = capsys.readouterr().err
    assert "ATLAS_WRITABLE_REMOTE_UNSUPPORTED" in err
    assert "atlas-operator-token" not in err
    run.assert_not_called()


def test_api_serve_enable_writes_sets_import_string_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = Mock()
    monkeypatch.setattr(uvicorn, "run", run)
    monkeypatch.setenv(
        "ATLAS_OPERATOR_TOKEN",
        "atlas-operator-token-0123456789ABCDEFGHJKLMNPQRSTxyz!@#",
    )

    assert (
        cli.main(
            [
                "api",
                "serve",
                "--enable-writes",
                "--host",
                "127.0.0.1",
                "--port",
                "8010",
            ]
        )
        == cli.EXIT_OK
    )

    assert os.environ["ATLAS_API_ENABLE_WRITES"] == "1"
    assert os.environ["ATLAS_API_BIND_HOST"] == "127.0.0.1"
    run.assert_called_once_with(
        "atlas.api.app:app",
        host="127.0.0.1",
        port=8010,
    )

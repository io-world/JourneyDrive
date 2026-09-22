from __future__ import annotations

import json
import shutil
import ssl
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("fastapi")  # controller-side-only extra; not installed for the Windows thin-client build

from journeydrive_broker.config import Settings
from journeydrive_broker.ws_server import _build_server_ssl_context, _make_handler

requires_openssl = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not available")


def _base_settings(**overrides: object) -> Settings:
    return Settings(api_key="a" * 32, machines={"office-pc": "b" * 32}, **overrides)


def test_returns_none_when_tls_unconfigured() -> None:
    assert _build_server_ssl_context(_base_settings()) is None


@requires_openssl
def test_returns_ssl_context_when_tls_configured(tmp_path: Path) -> None:
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-days", "1", "-nodes",
            "-keyout", str(key_path), "-out", str(cert_path),
            "-subj", "/CN=test", "-addext", "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    settings = _base_settings(tls_cert_file=str(cert_path), tls_key_file=str(key_path))
    context = _build_server_ssl_context(settings)
    assert isinstance(context, ssl.SSLContext)


async def _empty_aiter(self):
    return
    yield  # pragma: no cover - makes this an async generator with no items


@pytest.mark.asyncio
async def test_handler_sends_config_push_after_ack() -> None:
    settings = _base_settings(machine_profiles={"office-pc": {"log_level": "DEBUG"}})
    registry = Mock()
    websocket = AsyncMock()
    websocket.recv.side_effect = [
        json.dumps({"machine_id": "office-pc", "api_key": "b" * 32}),
        json.dumps({"type": "monitors", "monitors": [{"index": 0, "left": 0, "top": 0, "width": 1920, "height": 1080}]}),
    ]
    websocket.__aiter__ = _empty_aiter

    handler = _make_handler(settings, registry)
    await handler(websocket)

    assert websocket.send.call_count == 2
    ack = json.loads(websocket.send.call_args_list[0].args[0])
    config_push = json.loads(websocket.send.call_args_list[1].args[0])
    assert ack == {"ok": True}
    assert config_push == {"type": "config", "log_level": "DEBUG"}
    registry.register.assert_called_once_with(
        "office-pc", websocket, monitors=[{"index": 0, "left": 0, "top": 0, "width": 1920, "height": 1080}]
    )


@pytest.mark.asyncio
async def test_handler_sends_empty_config_push_when_no_profile() -> None:
    settings = _base_settings()
    registry = Mock()
    websocket = AsyncMock()
    websocket.recv.side_effect = [
        json.dumps({"machine_id": "office-pc", "api_key": "b" * 32}),
        json.dumps({"type": "monitors", "monitors": []}),
    ]
    websocket.__aiter__ = _empty_aiter

    handler = _make_handler(settings, registry)
    await handler(websocket)

    config_push = json.loads(websocket.send.call_args_list[1].args[0])
    assert config_push == {"type": "config"}


@pytest.mark.asyncio
async def test_handler_drops_connection_on_malformed_monitors_frame() -> None:
    settings = _base_settings()
    registry = Mock()
    websocket = AsyncMock()
    websocket.recv.side_effect = [json.dumps({"machine_id": "office-pc", "api_key": "b" * 32}), "not json"]
    websocket.__aiter__ = _empty_aiter

    handler = _make_handler(settings, registry)
    await handler(websocket)

    registry.register.assert_not_called()
    assert websocket.send.call_count == 1  # ack only

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("fastapi")  # controller-side-only extra; not installed for the Windows thin-client build

from fastapi.testclient import TestClient

from journeycapture_broker.config import Settings
from journeycapture_broker.http_api import create_app
from journeycapture_broker.registry import MachineError, MachineNotConnected, MachineTimeout

API_KEY = "a" * 32


@pytest.fixture
def settings() -> Settings:
    return Settings(api_key=API_KEY, machines={"office-pc": "b" * 32})


@pytest.fixture
def registry() -> Mock:
    # connected_machines() is sync in the real ConnectionRegistry; only call() is
    # async — a plain AsyncMock() would make both async, which breaks the sync route.
    reg = Mock()
    reg.call = AsyncMock()
    return reg


@pytest.fixture
def client(settings: Settings, registry: Mock) -> TestClient:
    app = create_app(settings, registry)
    return TestClient(app)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


def test_requires_auth(client: TestClient) -> None:
    response = client.get("/machines")
    assert response.status_code == 401


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_routes_disabled(client: TestClient, path: str) -> None:
    # These bypass FastAPI's app-level `dependencies` auth check entirely (they're
    # added outside the dependency-injected router), so they must be disabled rather
    # than relied on to be auth-gated. See create_app's docs_url=None comment.
    response = client.get(path)
    assert response.status_code == 404


def test_list_machines(client: TestClient, auth_headers: dict, registry: Mock) -> None:
    registry.connected_machines.return_value = [
        {"machine_id": "office-pc", "monitors": [{"index": 0, "left": 0, "top": 0, "width": 1920, "height": 1080}]}
    ]
    response = client.get("/machines", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == [
        {"machine_id": "office-pc", "monitors": [{"index": 0, "left": 0, "top": 0, "width": 1920, "height": 1080}]}
    ]


def test_screenshot_monitors_returns_cached_value(client: TestClient, auth_headers: dict, registry: Mock) -> None:
    registry.get_monitors.return_value = [{"index": 0, "left": 0, "top": 0, "width": 1920, "height": 1080}]
    response = client.get("/machines/office-pc/screenshot/monitors", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == [{"index": 0, "left": 0, "top": 0, "width": 1920, "height": 1080}]
    registry.get_monitors.assert_called_once_with("office-pc")


def test_screenshot_monitors_404_when_not_connected(client: TestClient, auth_headers: dict, registry: Mock) -> None:
    registry.get_monitors.return_value = None
    response = client.get("/machines/office-pc/screenshot/monitors", headers=auth_headers)
    assert response.status_code == 404


def test_mcp_config_empty_by_default(client: TestClient, auth_headers: dict) -> None:
    response = client.get("/mcp-config", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {}


def test_mcp_config_returns_configured_profile(registry: Mock, auth_headers: dict) -> None:
    settings = Settings(
        api_key=API_KEY,
        machines={"office-pc": "b" * 32},
        mcp_profile={"save_screenshots": True, "screenshot_dir": "shots", "max_saved_screenshots": 50},
    )
    app = create_app(settings, registry)
    response = TestClient(app).get("/mcp-config", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"save_screenshots": True, "screenshot_dir": "shots", "max_saved_screenshots": 50}


def test_mcp_config_requires_auth(client: TestClient) -> None:
    response = client.get("/mcp-config")
    assert response.status_code == 401


def test_health(client: TestClient, auth_headers: dict, registry: Mock) -> None:
    registry.call.return_value = ({"status": "ok", "version": "0.2.0"}, None)
    response = client.get("/machines/office-pc/health", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.2.0"}
    registry.call.assert_called_once_with("office-pc", "health", {})


def test_machine_not_connected_returns_404(client: TestClient, auth_headers: dict, registry: Mock) -> None:
    registry.call.side_effect = MachineNotConnected("machine 'office-pc' is not connected")
    response = client.get("/machines/office-pc/health", headers=auth_headers)
    assert response.status_code == 404


def test_machine_timeout_returns_504(client: TestClient, auth_headers: dict, registry: Mock) -> None:
    registry.call.side_effect = MachineTimeout("timed out")
    response = client.get("/machines/office-pc/health", headers=auth_headers)
    assert response.status_code == 504


def test_machine_error_returns_400(client: TestClient, auth_headers: dict, registry: Mock) -> None:
    registry.call.side_effect = MachineError("bad params")
    response = client.post("/machines/office-pc/keyboard/key", json={"keys": ["bogus"]}, headers=auth_headers)
    assert response.status_code == 400


def test_mouse_move(client: TestClient, auth_headers: dict, registry: Mock) -> None:
    registry.call.return_value = ({"status": "ok", "x": 10, "y": 20}, None)
    response = client.post("/machines/office-pc/mouse/move", json={"x": 10, "y": 20}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "x": 10, "y": 20}
    registry.call.assert_called_once_with("office-pc", "mouse_move", {"x": 10, "y": 20, "relative": False})


def test_mouse_click_partial_xy_rejected(client: TestClient, auth_headers: dict, registry: Mock) -> None:
    response = client.post("/machines/office-pc/mouse/click", json={"x": 100}, headers=auth_headers)
    assert response.status_code == 422
    registry.call.assert_not_called()


def test_screenshot_returns_raw_bytes(client: TestClient, auth_headers: dict, registry: Mock) -> None:
    registry.call.return_value = ({"content_type": "image/jpeg"}, b"fake-jpeg-bytes")
    response = client.get("/machines/office-pc/screenshot", headers=auth_headers)
    assert response.status_code == 200
    assert response.content == b"fake-jpeg-bytes"
    assert response.headers["content-type"] == "image/jpeg"


def test_keyboard_type(client: TestClient, auth_headers: dict, registry: Mock) -> None:
    registry.call.return_value = ({"status": "ok", "length": 5}, None)
    response = client.post("/machines/office-pc/keyboard/type", json={"text": "hello"}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "length": 5}


def test_clipboard_get(client: TestClient, auth_headers: dict, registry: Mock) -> None:
    registry.call.return_value = ({"text": "hello from clipboard"}, None)
    response = client.get("/machines/office-pc/clipboard", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"text": "hello from clipboard"}
    registry.call.assert_called_once_with("office-pc", "clipboard_get", {})


def test_clipboard_set(client: TestClient, auth_headers: dict, registry: Mock) -> None:
    registry.call.return_value = ({"status": "ok"}, None)
    response = client.post("/machines/office-pc/clipboard", json={"text": "hello"}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    registry.call.assert_called_once_with("office-pc", "clipboard_set", {"text": "hello"})


def test_clipboard_set_requires_auth(client: TestClient) -> None:
    response = client.post("/machines/office-pc/clipboard", json={"text": "hello"})
    assert response.status_code == 401

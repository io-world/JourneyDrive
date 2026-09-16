from __future__ import annotations

import pytest

pytest.importorskip("mcp")  # controller-side-only extra; not installed for the Windows thin-client build

import httpx

from journeycapture_mcp.client import JourneyCaptureClient, JourneyCaptureError
from journeycapture_mcp.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(broker_host="192.168.1.10", broker_api_key="a" * 32)


def test_set_timeout_updates_underlying_httpx_client(settings: Settings) -> None:
    client = JourneyCaptureClient(settings)
    assert client._client.timeout != httpx.Timeout(42.0)
    client.set_timeout(42.0)
    assert client._client.timeout == httpx.Timeout(42.0)


def make_client(settings: Settings, handler) -> JourneyCaptureClient:
    client = JourneyCaptureClient(settings)
    client._client = httpx.AsyncClient(
        base_url=client._client.base_url,
        headers=client._client.headers,
        transport=httpx.MockTransport(handler),
    )
    return client


@pytest.mark.asyncio
async def test_list_machines(settings: Settings) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["api_key"] = request.headers.get("X-API-Key")
        return httpx.Response(
            200,
            json=[
                {"machine_id": "office-pc", "monitors": []},
                {"machine_id": "home-pc", "monitors": []},
            ],
        )

    client = make_client(settings, handler)
    result = await client.list_machines()
    assert result == [
        {"machine_id": "office-pc", "monitors": []},
        {"machine_id": "home-pc", "monitors": []},
    ]
    assert seen["path"] == "/machines"
    assert seen["api_key"] == "a" * 32


@pytest.mark.asyncio
async def test_health_hits_machine_namespaced_path(settings: Settings) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"status": "ok", "version": "0.2.0"})

    client = make_client(settings, handler)
    result = await client.health("office-pc")
    assert result == {"status": "ok", "version": "0.2.0"}
    assert seen["path"] == "/machines/office-pc/health"


@pytest.mark.asyncio
async def test_move_mouse_posts_body_to_machine_path(settings: Settings) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = request.content
        return httpx.Response(200, json={"status": "ok", "x": 10, "y": 20})

    client = make_client(settings, handler)
    result = await client.move_mouse("office-pc", 10, 20)
    assert result == {"status": "ok", "x": 10, "y": 20}
    assert seen["method"] == "POST"
    assert seen["path"] == "/machines/office-pc/mouse/move"
    assert b'"x":10' in seen["body"] or b'"x": 10' in seen["body"]


@pytest.mark.asyncio
async def test_screenshot_returns_bytes_and_content_type(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"fake-jpeg-bytes", headers={"content-type": "image/jpeg"})

    client = make_client(settings, handler)
    data, content_type = await client.screenshot("office-pc")
    assert data == b"fake-jpeg-bytes"
    assert content_type == "image/jpeg"


@pytest.mark.asyncio
async def test_error_response_raises_journeycapture_error(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "machine 'office-pc' is not connected"})

    client = make_client(settings, handler)
    with pytest.raises(JourneyCaptureError, match="404"):
        await client.health("office-pc")


@pytest.mark.asyncio
async def test_get_mcp_config_returns_profile(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"save_screenshots": True, "max_saved_screenshots": 50})

    client = make_client(settings, handler)
    result = await client.get_mcp_config()
    assert result == {"save_screenshots": True, "max_saved_screenshots": 50}


@pytest.mark.asyncio
async def test_get_mcp_config_tolerates_404(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = make_client(settings, handler)
    result = await client.get_mcp_config()
    assert result == {}


@pytest.mark.asyncio
async def test_get_mcp_config_raises_on_other_errors(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    client = make_client(settings, handler)
    with pytest.raises(JourneyCaptureError, match="500"):
        await client.get_mcp_config()


@pytest.mark.asyncio
async def test_click_mouse_omits_unset_coordinates(settings: Settings) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        return httpx.Response(200, json={"status": "ok"})

    client = make_client(settings, handler)
    await client.click_mouse("office-pc")
    assert b"x" not in seen["body"]
    assert b"y" not in seen["body"]


@pytest.mark.asyncio
async def test_get_clipboard_hits_machine_namespaced_path(settings: Settings) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"text": "hello from clipboard"})

    client = make_client(settings, handler)
    result = await client.get_clipboard("office-pc")
    assert result == "hello from clipboard"
    assert seen["method"] == "GET"
    assert seen["path"] == "/machines/office-pc/clipboard"


@pytest.mark.asyncio
async def test_set_clipboard_posts_text_to_machine_path(settings: Settings) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = request.content
        return httpx.Response(200, json={"status": "ok"})

    client = make_client(settings, handler)
    result = await client.set_clipboard("office-pc", "hello")
    assert result == {"status": "ok"}
    assert seen["method"] == "POST"
    assert seen["path"] == "/machines/office-pc/clipboard"
    assert b"hello" in seen["body"]

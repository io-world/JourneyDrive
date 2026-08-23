from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from PIL import Image as PILImage

pytest.importorskip("mcp")  # controller-side-only extra; not installed for the Windows thin-client build

from mcp.server.mcpserver.exceptions import ToolError

from journeycapture_mcp.config import Settings
from journeycapture_mcp.server import build_server

MACHINE = "office-pc"


def _fake_screenshot_bytes(width: int = 200, height: int = 100) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (width, height), color=(10, 20, 30)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def client() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(broker_host="192.168.1.10", broker_api_key="a" * 32, screenshot_dir=str(tmp_path / "screenshots"))


@pytest.fixture
def server(client: AsyncMock, settings: Settings):
    return build_server(client, settings)


@pytest.mark.asyncio
async def test_list_machines_calls_client(server, client: AsyncMock) -> None:
    client.list_machines.return_value = ["office-pc", "home-pc"]
    result = await server.call_tool("list_machines", {})
    client.list_machines.assert_called_once_with()
    assert not result.is_error


@pytest.mark.asyncio
async def test_health_check_calls_client(server, client: AsyncMock) -> None:
    client.health.return_value = {"status": "ok", "version": "0.2.0"}
    result = await server.call_tool("health_check", {"machine": MACHINE})
    client.health.assert_called_once_with(MACHINE)
    assert not result.is_error


@pytest.mark.asyncio
async def test_move_mouse_passes_through_args(server, client: AsyncMock) -> None:
    client.move_mouse.return_value = {"status": "ok", "x": 5, "y": 6}
    await server.call_tool("move_mouse", {"machine": MACHINE, "x": 5, "y": 6, "relative": True})
    client.move_mouse.assert_called_once_with(MACHINE, 5, 6, relative=True)


@pytest.mark.asyncio
async def test_click_mouse_passes_through_args(server, client: AsyncMock) -> None:
    client.click_mouse.return_value = {"status": "ok"}
    await server.call_tool("click_mouse", {"machine": MACHINE, "button": "right", "action": "down"})
    client.click_mouse.assert_called_once_with(MACHINE, button="right", action="down", clicks=1, x=None, y=None)


@pytest.mark.asyncio
async def test_type_text_passes_through_text(server, client: AsyncMock) -> None:
    client.type_text.return_value = {"status": "ok", "length": 5}
    await server.call_tool("type_text", {"machine": MACHINE, "text": "hello"})
    client.type_text.assert_called_once_with(MACHINE, "hello")


@pytest.mark.asyncio
async def test_type_text_logs_length_not_text(server, client: AsyncMock, caplog) -> None:
    secret = "s3cr3t-password"
    client.type_text.return_value = {"status": "ok", "length": len(secret)}
    with caplog.at_level(logging.INFO, logger="journeycapture_mcp.server"):
        await server.call_tool("type_text", {"machine": MACHINE, "text": secret})
    assert secret not in caplog.text
    assert f"{len(secret)} character" in caplog.text


@pytest.mark.asyncio
async def test_click_mouse_logs_call(server, client: AsyncMock, caplog) -> None:
    client.click_mouse.return_value = {"status": "ok"}
    with caplog.at_level(logging.INFO, logger="journeycapture_mcp.server"):
        await server.call_tool("click_mouse", {"machine": MACHINE, "clicks": 2, "x": 100, "y": 200})
    assert "click_mouse" in caplog.text
    assert "clicks=2" in caplog.text


@pytest.mark.asyncio
async def test_click_mouse_with_fractional_coordinates(server, client: AsyncMock) -> None:
    client.list_monitors.return_value = [
        {"index": 0, "left": 0, "top": 0, "width": 1920, "height": 1080},
        {"index": 1, "left": 0, "top": 0, "width": 1920, "height": 1080},
    ]
    client.click_mouse.return_value = {"status": "ok"}
    await server.call_tool("click_mouse", {"machine": MACHINE, "fx": 0.25, "fy": 0.5})
    client.click_mouse.assert_called_once_with(MACHINE, button="left", action="click", clicks=1, x=480, y=540)


@pytest.mark.asyncio
async def test_move_mouse_with_fractional_coordinates(server, client: AsyncMock) -> None:
    client.list_monitors.return_value = [
        {"index": 0, "left": 0, "top": 0, "width": 1920, "height": 1080},
        {"index": 1, "left": 0, "top": 0, "width": 1920, "height": 1080},
    ]
    client.move_mouse.return_value = {"status": "ok", "x": 960, "y": 540}
    await server.call_tool("move_mouse", {"machine": MACHINE, "fx": 0.5, "fy": 0.5})
    client.move_mouse.assert_called_once_with(MACHINE, 960, 540, relative=False)


@pytest.mark.asyncio
async def test_fractional_coordinates_use_specified_monitor(server, client: AsyncMock) -> None:
    client.list_monitors.return_value = [
        {"index": 0, "left": 0, "top": 0, "width": 3840, "height": 1080},
        {"index": 1, "left": 0, "top": 0, "width": 1920, "height": 1080},
        {"index": 2, "left": 1920, "top": 0, "width": 1920, "height": 1080},
    ]
    client.click_mouse.return_value = {"status": "ok"}
    await server.call_tool("click_mouse", {"machine": MACHINE, "fx": 0.5, "fy": 0.5, "monitor": 2})
    client.click_mouse.assert_called_once_with(MACHINE, button="left", action="click", clicks=1, x=2880, y=540)


@pytest.mark.asyncio
async def test_click_mouse_both_xy_and_fxfy_raises(server, client: AsyncMock) -> None:
    with pytest.raises(ToolError, match="not both"):
        await server.call_tool("click_mouse", {"machine": MACHINE, "x": 1, "y": 2, "fx": 0.1, "fy": 0.1})


@pytest.mark.asyncio
async def test_move_mouse_neither_xy_nor_fxfy_raises(server, client: AsyncMock) -> None:
    with pytest.raises(ToolError, match="Provide either"):
        await server.call_tool("move_mouse", {"machine": MACHINE})


@pytest.mark.asyncio
async def test_move_mouse_fxfy_with_relative_raises(server, client: AsyncMock) -> None:
    with pytest.raises(ToolError, match="relative"):
        await server.call_tool("move_mouse", {"machine": MACHINE, "fx": 0.1, "fy": 0.1, "relative": True})


@pytest.mark.asyncio
async def test_send_keys_passes_through_args(server, client: AsyncMock) -> None:
    client.send_keys.return_value = {"status": "ok"}
    await server.call_tool("send_keys", {"machine": MACHINE, "keys": ["ctrl", "c"], "action": "tap"})
    client.send_keys.assert_called_once_with(MACHINE, ["ctrl", "c"], action="tap")


@pytest.mark.asyncio
async def test_take_screenshot_returns_image_content(server, client: AsyncMock) -> None:
    client.screenshot.return_value = (b"fake-jpeg-bytes", "image/jpeg")
    result = await server.call_tool("take_screenshot", {"machine": MACHINE})
    assert not result.is_error
    assert result.content[0].type == "image"
    assert result.content[0].mime_type == "image/jpeg"


@pytest.mark.asyncio
async def test_take_screenshot_does_not_save_by_default(server, client: AsyncMock, settings: Settings) -> None:
    client.screenshot.return_value = (b"fake-jpeg-bytes", "image/jpeg")
    await server.call_tool("take_screenshot", {"machine": MACHINE})
    assert not Path(settings.screenshot_dir).exists()


@pytest.mark.asyncio
async def test_take_screenshot_saves_when_enabled(client: AsyncMock, tmp_path) -> None:
    settings = Settings(
        broker_host="192.168.1.10",
        broker_api_key="a" * 32,
        save_screenshots=True,
        screenshot_dir=str(tmp_path / "shots"),
    )
    server = build_server(client, settings)
    client.screenshot.return_value = (b"fake-jpeg-bytes", "image/jpeg")

    await server.call_tool("take_screenshot", {"machine": MACHINE})

    saved = list((tmp_path / "shots").glob("*.jpeg"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == b"fake-jpeg-bytes"


@pytest.mark.asyncio
async def test_take_screenshot_prunes_beyond_cap(client: AsyncMock, tmp_path) -> None:
    settings = Settings(
        broker_host="192.168.1.10",
        broker_api_key="a" * 32,
        save_screenshots=True,
        screenshot_dir=str(tmp_path / "shots"),
        max_saved_screenshots=3,
    )
    server = build_server(client, settings)
    client.screenshot.return_value = (b"fake-jpeg-bytes", "image/jpeg")

    for _ in range(5):
        await server.call_tool("take_screenshot", {"machine": MACHINE})

    assert len(list((tmp_path / "shots").glob("*.jpeg"))) == 3


@pytest.mark.asyncio
async def test_take_screenshot_no_prune_when_cap_disabled(client: AsyncMock, tmp_path) -> None:
    settings = Settings(
        broker_host="192.168.1.10",
        broker_api_key="a" * 32,
        save_screenshots=True,
        screenshot_dir=str(tmp_path / "shots"),
        max_saved_screenshots=0,
    )
    server = build_server(client, settings)
    client.screenshot.return_value = (b"fake-jpeg-bytes", "image/jpeg")

    for _ in range(5):
        await server.call_tool("take_screenshot", {"machine": MACHINE})

    assert len(list((tmp_path / "shots").glob("*.jpeg"))) == 5


@pytest.mark.asyncio
async def test_list_monitors_calls_client(server, client: AsyncMock) -> None:
    client.list_monitors.return_value = [{"index": 0, "left": 0, "top": 0, "width": 1920, "height": 1080}]
    result = await server.call_tool("list_monitors", {"machine": MACHINE})
    client.list_monitors.assert_called_once_with(MACHINE)
    assert not result.is_error


@pytest.mark.asyncio
async def test_preview_click_returns_annotated_png(server, client: AsyncMock) -> None:
    client.list_monitors.return_value = [{"index": 0, "left": 0, "top": 0, "width": 200, "height": 100}]
    client.screenshot.return_value = (_fake_screenshot_bytes(200, 100), "image/jpeg")
    result = await server.call_tool("preview_click", {"machine": MACHINE, "fx": 0.5, "fy": 0.5})
    assert not result.is_error
    assert result.content[0].type == "image"
    assert result.content[0].mime_type == "image/png"


@pytest.mark.asyncio
async def test_preview_click_does_not_call_click_mouse(server, client: AsyncMock) -> None:
    client.list_monitors.return_value = [{"index": 0, "left": 0, "top": 0, "width": 200, "height": 100}]
    client.screenshot.return_value = (_fake_screenshot_bytes(200, 100), "image/jpeg")
    await server.call_tool("preview_click", {"machine": MACHINE, "fx": 0.5, "fy": 0.5})
    client.click_mouse.assert_not_called()
    client.move_mouse.assert_not_called()


@pytest.mark.asyncio
async def test_preview_click_draws_marker_at_resolved_position(server, client: AsyncMock) -> None:
    background = (10, 20, 30)
    client.list_monitors.return_value = [{"index": 0, "left": 0, "top": 0, "width": 200, "height": 100}]
    client.screenshot.return_value = (_fake_screenshot_bytes(200, 100), "image/jpeg")

    result = await server.call_tool("preview_click", {"machine": MACHINE, "fx": 0.5, "fy": 0.5})

    annotated_bytes = base64.b64decode(result.content[0].data)
    annotated = PILImage.open(io.BytesIO(annotated_bytes)).convert("RGB")
    # fx=0.5, fy=0.5 on a 200x100 image resolves to (100, 50) — the crosshair's
    # own pixel there must no longer be the plain background color.
    assert annotated.getpixel((100, 50)) != background


@pytest.mark.asyncio
async def test_preview_click_uses_same_monitor_for_resolve_and_capture(server, client: AsyncMock) -> None:
    client.list_monitors.return_value = [
        {"index": 0, "left": 0, "top": 0, "width": 3840, "height": 1080},
        {"index": 1, "left": 0, "top": 0, "width": 1920, "height": 1080},
        {"index": 2, "left": 1920, "top": 0, "width": 1920, "height": 1080},
    ]
    client.screenshot.return_value = (_fake_screenshot_bytes(1920, 1080), "image/jpeg")
    await server.call_tool("preview_click", {"machine": MACHINE, "fx": 0.5, "fy": 0.5, "monitor": 2})
    client.screenshot.assert_called_once_with(MACHINE, monitor=2)


@pytest.mark.asyncio
async def test_preview_click_logs_call(server, client: AsyncMock, caplog) -> None:
    client.list_monitors.return_value = [{"index": 0, "left": 0, "top": 0, "width": 200, "height": 100}]
    client.screenshot.return_value = (_fake_screenshot_bytes(200, 100), "image/jpeg")
    with caplog.at_level(logging.INFO, logger="journeycapture_mcp.server"):
        await server.call_tool("preview_click", {"machine": MACHINE, "fx": 0.5, "fy": 0.5})
    assert "preview_click" in caplog.text

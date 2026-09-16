from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from journeycapture_mac_thinclient import ws_client
from journeycapture_mac_thinclient.config import Config


@pytest.fixture
def config() -> Config:
    return Config(broker_host="192.168.1.10", machine_id="macbook-pro", api_key="a" * 32)


def test_handle_health(config: Config) -> None:
    result = ws_client._handle_health(config, {})
    assert result["status"] == "ok"
    assert "version" in result


def test_handle_mouse_move(config: Config) -> None:
    with patch("journeycapture_mac_thinclient.ws_client.input_control.move_mouse", return_value=(10, 20)) as mock_move:
        result = ws_client._handle_mouse_move(config, {"x": 10, "y": 20})
    mock_move.assert_called_once_with(10, 20, relative=False)
    assert result == {"status": "ok", "x": 10, "y": 20}


def test_handle_mouse_click(config: Config) -> None:
    with patch("journeycapture_mac_thinclient.ws_client.input_control.click_mouse") as mock_click:
        result = ws_client._handle_mouse_click(config, {"button": "left"})
    mock_click.assert_called_once_with(button="left", action="click", clicks=1, x=None, y=None)
    assert result == {"status": "ok"}


def test_handle_mouse_click_partial_xy_raises(config: Config) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ws_client._handle_mouse_click(config, {"x": 100})


def test_handle_keyboard_type(config: Config) -> None:
    with patch("journeycapture_mac_thinclient.ws_client.input_control.type_text", return_value=5):
        result = ws_client._handle_keyboard_type(config, {"text": "hello"})
    assert result == {"status": "ok", "length": 5}


def test_handle_clipboard_get(config: Config) -> None:
    with patch("journeycapture_mac_thinclient.ws_client.input_control.get_clipboard", return_value="hi"):
        result = ws_client._handle_clipboard_get(config, {})
    assert result == {"text": "hi"}


def test_handle_clipboard_set(config: Config) -> None:
    with patch("journeycapture_mac_thinclient.ws_client.input_control.set_clipboard") as mock_set:
        result = ws_client._handle_clipboard_set(config, {"text": "hello"})
    mock_set.assert_called_once_with("hello")
    assert result == {"status": "ok"}


def test_handle_keyboard_key_unknown_key_raises_dispatch_error(config: Config) -> None:
    with patch(
        "journeycapture_mac_thinclient.ws_client.input_control.send_keys",
        side_effect=ValueError("unknown key: 'bogus'"),
    ):
        with pytest.raises(ws_client.DispatchError):
            ws_client._handle_keyboard_key(config, {"keys": ["bogus"]})


@pytest.mark.asyncio
async def test_dispatch_unknown_method_sends_error(config: Config) -> None:
    websocket = AsyncMock()
    await ws_client._dispatch(websocket, config, {"id": "1", "method": "nope", "params": {}})
    sent = json.loads(websocket.send.call_args.args[0])
    assert sent["id"] == "1"
    assert "unknown method" in sent["error"]["message"]


@pytest.mark.asyncio
async def test_dispatch_success_sends_result(config: Config) -> None:
    websocket = AsyncMock()
    with patch("journeycapture_mac_thinclient.ws_client.input_control.scroll_mouse") as mock_scroll:
        await ws_client._dispatch(websocket, config, {"id": "2", "method": "mouse_scroll", "params": {"dx": 1, "dy": -1}})
    mock_scroll.assert_called_once_with(dx=1, dy=-1)
    sent = json.loads(websocket.send.call_args.args[0])
    assert sent == {"id": "2", "result": {"status": "ok"}}


@pytest.mark.asyncio
async def test_dispatch_validation_error_sends_error(config: Config) -> None:
    websocket = AsyncMock()
    await ws_client._dispatch(websocket, config, {"id": "3", "method": "mouse_click", "params": {"x": 100}})
    sent = json.loads(websocket.send.call_args.args[0])
    assert sent["id"] == "3"
    assert "error" in sent


@pytest.mark.asyncio
async def test_handle_screenshot_sends_metadata_then_binary_frame(config: Config) -> None:
    websocket = AsyncMock()
    with patch(
        "journeycapture_mac_thinclient.ws_client.capture.take_screenshot",
        return_value=(b"fake-jpeg-bytes", "image/jpeg"),
    ):
        await ws_client._dispatch(websocket, config, {"id": "4", "method": "screenshot", "params": {}})

    assert websocket.send.call_count == 2
    first_call, second_call = websocket.send.call_args_list
    metadata = json.loads(first_call.args[0])
    assert metadata == {"id": "4", "result": {"content_type": "image/jpeg"}}
    assert second_call.args[0] == b"fake-jpeg-bytes"


@pytest.mark.asyncio
async def test_handle_screenshot_bad_monitor_sends_error(config: Config) -> None:
    websocket = AsyncMock()
    with patch(
        "journeycapture_mac_thinclient.ws_client.capture.take_screenshot",
        side_effect=ValueError("monitor index 9 out of range"),
    ):
        await ws_client._dispatch(websocket, config, {"id": "5", "method": "screenshot", "params": {"monitor": 9}})

    websocket.send.assert_called_once()
    sent = json.loads(websocket.send.call_args.args[0])
    assert sent == {"id": "5", "error": {"message": "monitor index 9 out of range"}}


def test_apply_config_push_updates_screenshot_and_log_level(config: Config) -> None:
    ws_client._apply_config_push(
        config, {"screenshot": {"format": "png", "quality": 90, "monitor": 1}, "log_level": "DEBUG"}
    )
    assert config.screenshot.format == "png"
    assert config.screenshot.quality == 90
    assert config.screenshot.monitor == 1
    assert config.log_level == "DEBUG"


def test_apply_config_push_empty_leaves_config_unchanged(config: Config) -> None:
    original_screenshot = config.screenshot
    original_log_level = config.log_level
    ws_client._apply_config_push(config, {"type": "config"})
    assert config.screenshot == original_screenshot
    assert config.log_level == original_log_level


@pytest.mark.asyncio
async def test_run_applies_broker_pushed_config_then_stops(config: Config) -> None:
    websocket = AsyncMock()
    websocket.recv.side_effect = [
        json.dumps({"ok": True}),
        json.dumps(
            {"type": "config", "screenshot": {"format": "png", "quality": 90, "monitor": 1}, "log_level": "DEBUG"}
        ),
    ]

    async def empty_aiter(self):
        return
        yield  # pragma: no cover - makes this an async generator with no items

    websocket.__aiter__ = empty_aiter

    async def fake_connect(*args, **kwargs):
        yield websocket

    fake_monitor = Mock()
    fake_monitor.model_dump.return_value = {"index": 0, "left": 0, "top": 0, "width": 1920, "height": 1080}
    with (
        patch("journeycapture_mac_thinclient.ws_client.websockets.connect", fake_connect),
        patch("journeycapture_mac_thinclient.ws_client.capture.list_monitors", return_value=[fake_monitor]),
    ):
        await ws_client.run(config)

    assert config.screenshot.format == "png"
    assert config.log_level == "DEBUG"
    assert websocket.send.call_args_list[1].args[0] == json.dumps(
        {"type": "monitors", "monitors": [{"index": 0, "left": 0, "top": 0, "width": 1920, "height": 1080}]}
    )


@pytest.mark.asyncio
async def test_run_raises_on_rejected_registration(config: Config) -> None:
    websocket = AsyncMock()
    websocket.recv.return_value = json.dumps({"ok": False, "error": "unknown machine_id or invalid api_key"})

    async def fake_connect(*args, **kwargs):
        yield websocket

    with patch("journeycapture_mac_thinclient.ws_client.websockets.connect", fake_connect):
        with pytest.raises(ws_client.RegistrationRejected, match="invalid api_key"):
            await ws_client.run(config)

from __future__ import annotations

import asyncio
import json
import logging
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Callable

import websockets
from pydantic import ValidationError
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import process_exception as _default_process_exception

# tls_pinning is pure-stdlib TLS/certificate-fingerprint logic with no OS-specific
# code — reused directly from journeycapture_windows_thinclient rather than
# duplicated, the same way journeycapture_mcp.client already does, so a future fix
# to the pinning logic doesn't need to be applied twice.
from journeycapture_windows_thinclient import tls_pinning
from journeycapture_windows_thinclient.tls_pinning import CertificateFingerprintMismatch  # noqa: F401

from journeycapture_mac_thinclient import capture, input_control
from journeycapture_mac_thinclient.config import Config, ScreenshotConfig
from journeycapture_mac_thinclient.schemas import (
    ClipboardSetRequest,
    KeyboardKeyRequest,
    KeyboardTypeRequest,
    MouseClickRequest,
    MouseMoveRequest,
    MouseScrollRequest,
)

logger = logging.getLogger(__name__)

try:
    _VERSION = version("journeycapture")
except PackageNotFoundError:
    _VERSION = "0.0.0"


class DispatchError(Exception):
    """Raised by a handler for a client-facing error (bad params, unknown key, etc.)."""


class RegistrationRejected(Exception):
    """The broker rejected our machine_id/api_key at the handshake — not recoverable
    by reconnecting, since the credentials themselves are wrong."""


class BrokerUnreachable(Exception):
    """Couldn't establish the very first connection to the broker after retrying —
    distinct from losing a connection that was already established, which keeps
    retrying indefinitely (see run()) since the broker coming back is expected."""


_INITIAL_CONNECT_ATTEMPTS = 5


def _apply_config_push(config: Config, push: dict) -> None:
    """Apply operational config the broker pushed right after the handshake ack
    (see docs/BROKER.md's "Broker-pushed config") on top of this process's local
    config.json. Only screenshot/log_level are broker-owned — broker_host/port/
    machine_id/api_key/broker_tls/broker_cert_fingerprint (needed just to reach and
    trust the broker in the first place) and log_file (a local filesystem path, and
    switching a live RotatingFileHandler's target mid-process isn't worth the
    complexity) stay local-only, unaffected by this. A field the broker doesn't
    mention leaves this process's existing value (local config, or the pydantic
    default) untouched — this is what makes a broker with no machine_profile for
    this machine, or an older broker that never sends "type": "config" at all,
    behave exactly as before this existed.
    """
    if "screenshot" in push:
        config.screenshot = ScreenshotConfig.model_validate(push["screenshot"])
        logger.info("broker pushed screenshot config: %s", config.screenshot)
    if "log_level" in push:
        config.log_level = push["log_level"]
        logging.getLogger().setLevel(config.log_level)
        logger.info("broker pushed log_level: %s", config.log_level)


def _handle_health(config: Config, params: dict) -> Any:
    return {"status": "ok", "version": _VERSION}


def _handle_screenshot_monitors(config: Config, params: dict) -> Any:
    return [m.model_dump() for m in capture.list_monitors()]


def _handle_mouse_move(config: Config, params: dict) -> Any:
    body = MouseMoveRequest.model_validate(params)
    x, y = input_control.move_mouse(body.x, body.y, relative=body.relative)
    logger.info("mouse move to (%d, %d) relative=%s", x, y, body.relative)
    return {"status": "ok", "x": x, "y": y}


def _handle_mouse_click(config: Config, params: dict) -> Any:
    body = MouseClickRequest.model_validate(params)
    logger.info("mouse %s button=%s clicks=%d x=%s y=%s", body.action, body.button, body.clicks, body.x, body.y)
    input_control.click_mouse(button=body.button, action=body.action, clicks=body.clicks, x=body.x, y=body.y)
    return {"status": "ok"}


def _handle_mouse_scroll(config: Config, params: dict) -> Any:
    body = MouseScrollRequest.model_validate(params)
    logger.info("mouse scroll dx=%d dy=%d", body.dx, body.dy)
    input_control.scroll_mouse(dx=body.dx, dy=body.dy)
    return {"status": "ok"}


def _handle_keyboard_type(config: Config, params: dict) -> Any:
    body = KeyboardTypeRequest.model_validate(params)
    logger.info("keyboard type %d character(s)", len(body.text))
    length = input_control.type_text(body.text)
    return {"status": "ok", "length": length}


def _handle_keyboard_key(config: Config, params: dict) -> Any:
    body = KeyboardKeyRequest.model_validate(params)
    logger.info("keyboard key %s action=%s", body.keys, body.action)
    try:
        input_control.send_keys(body.keys, action=body.action)
    except ValueError as e:
        raise DispatchError(str(e)) from e
    return {"status": "ok"}


def _handle_clipboard_get(config: Config, params: dict) -> Any:
    text = input_control.get_clipboard()
    logger.info("clipboard get -> %d character(s)", len(text))
    return {"text": text}


def _handle_clipboard_set(config: Config, params: dict) -> Any:
    body = ClipboardSetRequest.model_validate(params)
    logger.info("clipboard set %d character(s)", len(body.text))
    input_control.set_clipboard(body.text)
    return {"status": "ok"}


_HANDLERS: dict[str, Callable[[Config, dict], Any]] = {
    "health": _handle_health,
    "screenshot_monitors": _handle_screenshot_monitors,
    "mouse_move": _handle_mouse_move,
    "mouse_click": _handle_mouse_click,
    "mouse_scroll": _handle_mouse_scroll,
    "keyboard_type": _handle_keyboard_type,
    "keyboard_key": _handle_keyboard_key,
    "clipboard_get": _handle_clipboard_get,
    "clipboard_set": _handle_clipboard_set,
}


def _do_screenshot(config: Config, params: dict) -> tuple[bytes, str]:
    fmt = params.get("format") or config.screenshot.format
    quality = params.get("quality") if params.get("quality") is not None else config.screenshot.quality
    monitor = params.get("monitor") if params.get("monitor") is not None else config.screenshot.monitor
    logger.info("screenshot monitor=%d format=%s", monitor, fmt)
    return capture.take_screenshot(monitor=monitor, format=fmt, quality=quality)


async def _handle_screenshot(websocket: ClientConnection, request_id: str, config: Config, params: dict) -> None:
    try:
        image_bytes, content_type = await asyncio.to_thread(_do_screenshot, config, params)
    except ValueError as e:
        await websocket.send(json.dumps({"id": request_id, "error": {"message": str(e)}}))
        return
    # Two frames: JSON metadata first, then the raw image bytes as a binary frame —
    # no base64. Safe because the broker processes one request per connection at a
    # time, so it knows this next frame belongs to this response.
    await websocket.send(json.dumps({"id": request_id, "result": {"content_type": content_type}}))
    await websocket.send(image_bytes)


async def _dispatch(websocket: ClientConnection, config: Config, message: dict) -> None:
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    if method == "screenshot":
        await _handle_screenshot(websocket, request_id, config, params)
        return

    handler = _HANDLERS.get(method)
    if handler is None:
        await websocket.send(json.dumps({"id": request_id, "error": {"message": f"unknown method: {method!r}"}}))
        return

    try:
        # Runs in a thread — several of these handlers block for real (type_text can
        # take up to ~40s), and blocking the event loop would starve the websocket
        # library's own keepalive pings, risking the broker timing out the connection.
        result = await asyncio.to_thread(handler, config, params)
    except (ValidationError, DispatchError) as e:
        await websocket.send(json.dumps({"id": request_id, "error": {"message": str(e)}}))
        return

    await websocket.send(json.dumps({"id": request_id, "result": result}))


async def run(config: Config) -> None:
    ssl_context = None
    if config.broker_tls:
        # Blocking (raw-socket) fingerprint verification, done once up front rather
        # than per-(re)connect — see tls_pinning.fetch_pinned_ssl_context. Run off
        # the event loop like every other blocking call in this module.
        ssl_context = await asyncio.to_thread(
            tls_pinning.fetch_pinned_ssl_context,
            config.broker_host,
            config.broker_port,
            config.broker_cert_fingerprint,
        )
    scheme = "wss" if config.broker_tls else "ws"
    uri = f"{scheme}://{config.broker_host}:{config.broker_port}"
    connected_once = False
    attempts = 0

    def process_exception(exc: Exception) -> Exception | None:
        nonlocal attempts
        # Preserve the library's own fatal-vs-retryable classification first —
        # only add our own bounded-attempts logic on top of what it already
        # considers retryable (network errors like refused/unreachable/DNS failure).
        classified = _default_process_exception(exc)
        if classified is not None:
            return classified
        if connected_once:
            return None  # reached the broker before; keep retrying forever on drops
        attempts += 1
        if attempts >= _INITIAL_CONNECT_ATTEMPTS:
            return BrokerUnreachable(f"could not reach broker at {uri} after {attempts} attempts: {exc}")
        return None

    async for websocket in websockets.connect(
        uri, max_size=None, process_exception=process_exception, ssl=ssl_context
    ):
        connected_once = True
        try:
            await websocket.send(json.dumps({"machine_id": config.machine_id, "api_key": config.api_key}))
            ack = json.loads(await websocket.recv())
            if not ack.get("ok"):
                raise RegistrationRejected(ack.get("error", "broker rejected registration"))

            logger.info("registered with broker %s as machine_id=%s", uri, config.machine_id)

            # The broker always sends exactly one more frame right after the ack —
            # its pushed operational config for this machine_id, {} if it has none
            # configured. Applied fresh on every (re)connect, not just the first.
            config_push = json.loads(await websocket.recv())
            _apply_config_push(config, config_push)

            async for raw in websocket:
                if isinstance(raw, bytes):
                    continue  # the broker never sends us binary frames
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("ignoring malformed message from broker: %r", raw)
                    continue
                await _dispatch(websocket, config, message)
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("lost connection to broker (%s), reconnecting...", e)
            continue

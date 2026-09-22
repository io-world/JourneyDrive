from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Literal

import pyperclip
from pynput.keyboard import Controller as KeyboardController
from pynput.keyboard import Key, KeyCode
from pynput.mouse import Button, Controller as MouseController

logger = logging.getLogger(__name__)

# On macOS, pynput needs this process to be trusted under System Settings ->
# Privacy & Security -> Accessibility (for mouse/keyboard control) and -> Screen
# Recording (for capture.py's mss calls) — a one-time manual grant that can't be
# scripted around, and that macOS can silently withhold without prompting
# depending on version. See docs/MACOS_BUILD.md. Verified live on a real Mac:
# pynput's reported cursor position and mss's monitor bounds already agree on the
# same real-pixel coordinate space (no Retina "points" vs pixels mismatch), so
# once trust is granted, x/y and fx/fy behave identically to the Windows agent.

_mouse = MouseController()
_keyboard = KeyboardController()
_lock = threading.Lock()

_BUTTONS = {
    "left": Button.left,
    "right": Button.right,
    "middle": Button.middle,
}

# How long a button/key can be held (action="down"/"press") before it's released
# automatically if no matching "up"/"release" request ever arrives — caps the blast
# radius of a dropped request leaving input stuck on the remote machine.
_AUTO_RELEASE_SECONDS = 10.0

_held_buttons: dict[Button, threading.Timer] = {}
_held_keys: dict[Key | KeyCode, threading.Timer] = {}


def _cancel_pending_release(store: dict, key: object) -> None:
    timer = store.pop(key, None)
    if timer is not None:
        timer.cancel()


def _schedule_auto_release(store: dict, key: object, release: Callable[[], None]) -> None:
    _cancel_pending_release(store, key)

    def _fire() -> None:
        with _lock:
            if store.get(key) is not timer:
                return  # cancelled/replaced by an explicit release before we got the lock
            store.pop(key, None)
            release()
        logger.warning(
            "auto-released %r after %.0fs with no matching release request", key, _AUTO_RELEASE_SECONDS
        )

    timer = threading.Timer(_AUTO_RELEASE_SECONDS, _fire)
    timer.daemon = True
    store[key] = timer
    timer.start()


def move_mouse(x: int, y: int, relative: bool = False) -> tuple[int, int]:
    with _lock:
        if relative:
            _mouse.move(x, y)
        else:
            _mouse.position = (x, y)
        pos = _mouse.position
    return int(pos[0]), int(pos[1])


def click_mouse(
    button: Literal["left", "right", "middle"] = "left",
    action: Literal["click", "down", "up"] = "click",
    clicks: int = 1,
    x: int | None = None,
    y: int | None = None,
) -> None:
    with _lock:
        if x is not None and y is not None:
            _mouse.position = (x, y)
        btn = _BUTTONS[button]
        if action == "click":
            _mouse.click(btn, clicks)
        elif action == "down":
            _mouse.press(btn)
            _schedule_auto_release(_held_buttons, btn, lambda: _mouse.release(btn))
        elif action == "up":
            _cancel_pending_release(_held_buttons, btn)
            _mouse.release(btn)


def scroll_mouse(dx: int = 0, dy: int = 0) -> None:
    with _lock:
        _mouse.scroll(dx, dy)


def type_text(text: str, interval: float = 0.01) -> int:
    with _lock:
        for char in text:
            _keyboard.type(char)
            time.sleep(interval)
    return len(text)


def _resolve_key(name: str) -> Key | KeyCode:
    special = getattr(Key, name, None)
    if isinstance(special, Key):
        return special
    if len(name) == 1:
        return KeyCode.from_char(name)
    raise ValueError(f"unknown key: {name!r}")


def send_keys(keys: list[str], action: Literal["press", "release", "tap"] = "tap") -> None:
    resolved = [_resolve_key(k) for k in keys]
    with _lock:
        if action == "press":
            for key in resolved:
                _keyboard.press(key)
                _schedule_auto_release(_held_keys, key, lambda k=key: _keyboard.release(k))
        elif action == "release":
            for key in resolved:
                _cancel_pending_release(_held_keys, key)
                _keyboard.release(key)
        elif action == "tap":
            for key in resolved:
                _keyboard.press(key)
            for key in reversed(resolved):
                _keyboard.release(key)


def get_clipboard() -> str:
    return pyperclip.paste() or ""


def set_clipboard(text: str) -> None:
    pyperclip.copy(text)

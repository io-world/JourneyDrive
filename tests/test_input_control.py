from __future__ import annotations

import time
from unittest.mock import Mock, patch

import pytest

from journeycapture_windows_thinclient import input_control


@pytest.fixture(autouse=True)
def mocked_controllers(monkeypatch):
    mouse = Mock()
    keyboard = Mock()
    monkeypatch.setattr(input_control, "_mouse", mouse)
    monkeypatch.setattr(input_control, "_keyboard", keyboard)
    monkeypatch.setattr(input_control, "_AUTO_RELEASE_SECONDS", 0.05)
    input_control._held_buttons.clear()
    input_control._held_keys.clear()
    return mouse, keyboard


def test_mouse_down_auto_releases_after_timeout(mocked_controllers):
    mouse, _ = mocked_controllers
    input_control.click_mouse(button="left", action="down")
    mouse.release.assert_not_called()
    time.sleep(0.15)
    mouse.release.assert_called_once_with(input_control._BUTTONS["left"])


def test_mouse_up_cancels_pending_auto_release(mocked_controllers):
    mouse, _ = mocked_controllers
    input_control.click_mouse(button="left", action="down")
    input_control.click_mouse(button="left", action="up")
    mouse.release.reset_mock()
    time.sleep(0.15)
    mouse.release.assert_not_called()


def test_keyboard_press_auto_releases_after_timeout(mocked_controllers):
    _, keyboard = mocked_controllers
    input_control.send_keys(["a"], action="press")
    keyboard.release.assert_not_called()
    time.sleep(0.15)
    keyboard.release.assert_called_once()


def test_keyboard_release_cancels_pending_auto_release(mocked_controllers):
    _, keyboard = mocked_controllers
    input_control.send_keys(["a"], action="press")
    input_control.send_keys(["a"], action="release")
    keyboard.release.reset_mock()
    time.sleep(0.15)
    keyboard.release.assert_not_called()


def test_keyboard_tap_does_not_schedule_auto_release(mocked_controllers):
    _, keyboard = mocked_controllers
    input_control.send_keys(["a"], action="tap")
    assert input_control._held_keys == {}


def test_get_clipboard_returns_pasted_text():
    with patch("journeycapture_windows_thinclient.input_control.pyperclip") as mock_pyperclip:
        mock_pyperclip.paste.return_value = "hello"
        assert input_control.get_clipboard() == "hello"


def test_get_clipboard_returns_empty_string_for_none():
    with patch("journeycapture_windows_thinclient.input_control.pyperclip") as mock_pyperclip:
        mock_pyperclip.paste.return_value = None
        assert input_control.get_clipboard() == ""


def test_set_clipboard_copies_text():
    with patch("journeycapture_windows_thinclient.input_control.pyperclip") as mock_pyperclip:
        input_control.set_clipboard("hello")
        mock_pyperclip.copy.assert_called_once_with("hello")

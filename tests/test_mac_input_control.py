from __future__ import annotations

import time
from unittest.mock import Mock

import pytest

from journeycapture_mac_thinclient import input_control


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

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class MonitorInfo(BaseModel):
    index: int
    left: int
    top: int
    width: int
    height: int


class StatusResponse(BaseModel):
    status: Literal["ok"] = "ok"


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str


class MouseMoveRequest(BaseModel):
    x: int = Field(description="Target X coordinate. In absolute mode (default), (0, 0) is the primary monitor's top-left corner; monitors above/left of it have negative coordinates. In relative mode, this is a pixel offset from the current position.")
    y: int = Field(description="Target Y coordinate. Same coordinate space as x.")
    relative: bool = Field(default=False, description="If true, x/y are offsets from the current cursor position instead of absolute screen coordinates.")


class MouseMoveResponse(StatusResponse):
    x: int
    y: int


class MouseClickRequest(BaseModel):
    button: Literal["left", "right", "middle"] = "left"
    action: Literal["click", "down", "up"] = "click"
    clicks: int = Field(default=1, ge=1, le=10, description="Number of clicks to send (e.g. 2 for a double-click).")
    x: int | None = Field(default=None, description="Move here first. Must be given together with y, or omitted along with it to click at the current cursor position.")
    y: int | None = Field(default=None, description="Move here first. Must be given together with x, or omitted along with it to click at the current cursor position.")

    @model_validator(mode="after")
    def _check_xy_together(self) -> "MouseClickRequest":
        if (self.x is None) != (self.y is None):
            raise ValueError("x and y must be given together, or both omitted to click at the current cursor position.")
        return self


class MouseScrollRequest(BaseModel):
    dx: int = Field(default=0, ge=-100, le=100, description="Horizontal scroll in wheel notches (not pixels). Positive scrolls right, negative scrolls left.")
    dy: int = Field(default=0, ge=-100, le=100, description="Vertical scroll in wheel notches (not pixels). Positive scrolls up, negative scrolls down.")


class KeyboardTypeRequest(BaseModel):
    text: str = Field(max_length=4000, description="Text to type into whatever window currently has focus. Typed one character at a time with a small delay between each to avoid dropped keystrokes.")


class KeyboardTypeResponse(StatusResponse):
    length: int


class KeyboardKeyRequest(BaseModel):
    keys: list[str] = Field(description="Key names to send together as a chord, e.g. [\"ctrl\", \"alt\", \"delete\"]. Each is either a single printable character or a pynput.keyboard.Key name (enter, tab, esc, backspace, space, shift, ctrl, alt, cmd, up, down, left, right, f1-f20, etc).")
    action: Literal["press", "release", "tap"] = Field(default="tap", description="'tap' presses then releases all keys (default). 'press'/'release' hold or let go without the other half, for building a custom sequence across multiple requests.")

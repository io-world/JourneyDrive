"""The agent's tool surface — deliberately small: UI actions for the current step,
one aiming aid, and two control tools. No open-ended tools, so there's nothing to
wander off and explore with (see docs/AUTOMATION.md's "Keeping the model on track").
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass, field

from journeydrive_mcp.client import JourneyDriveClient, JourneyDriveError
from journeydrive_mcp.geometry import draw_crosshair, fraction_to_pixel, resolve_monitor

logger = logging.getLogger(__name__)

STEP_COMPLETE = "step_complete"
STEP_FAILED = "step_failed"
PREVIEW_CLICK = "preview_click"
WAIT = "wait"

_MAX_WAIT_SECONDS = 10.0


def _tool(name: str, description: str, properties: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


_FX = {"type": "number", "description": "Horizontal position as a fraction of the screenshot's width, 0.0 (left edge) to 1.0 (right edge)."}
_FY = {"type": "number", "description": "Vertical position as a fraction of the screenshot's height, 0.0 (top edge) to 1.0 (bottom edge)."}

# Order is fixed: the tool list is part of the cached prompt prefix.
TOOLS: list[dict] = [
    _tool(
        "click",
        "Click at a position on the screen. Give the position as fractions of the screenshot (fx/fy), never pixels. "
        "clicks=2 is a double-click (e.g. to open a desktop icon).",
        {
            "fx": _FX,
            "fy": _FY,
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
            "clicks": {"type": "integer", "enum": [1, 2, 3]},
        },
    ),
    _tool("move", "Move the mouse cursor without clicking, e.g. to hover over a menu.", {"fx": _FX, "fy": _FY}),
    _tool(
        "scroll",
        "Scroll the mouse wheel at the current cursor position, in wheel notches. Positive dy scrolls up, negative "
        "scrolls down; positive dx scrolls right, negative scrolls left. Move the cursor over the area first.",
        {"dy": {"type": "integer"}, "dx": {"type": "integer"}},
    ),
    _tool(
        "type_text",
        "Type printable text into the focused window, one character at a time. A trailing \\n presses Enter. For "
        "long text, set_clipboard and paste instead — it is much faster.",
        {"text": {"type": "string"}},
    ),
    _tool(
        "send_keys",
        "Press a key or chord, e.g. [\"enter\"], [\"ctrl\", \"l\"], [\"cmd\", \"space\"]. Each key is a single "
        "character or a key name (enter, tab, esc, backspace, space, shift, ctrl, alt, cmd, up, down, left, right, "
        "f1-f20).",
        {"keys": {"type": "array", "items": {"type": "string"}}},
    ),
    _tool(
        "set_clipboard",
        "Put text on the machine's clipboard. Then paste it with send_keys: [\"ctrl\", \"v\"] on Windows, "
        "[\"cmd\", \"v\"] on macOS.",
        {"text": {"type": "string"}},
    ),
    _tool(
        WAIT,
        f"Wait for the screen to settle, e.g. while an app launches or a page loads (at most {_MAX_WAIT_SECONDS:g} "
        "seconds). You get a fresh screenshot afterwards.",
        {"seconds": {"type": "number"}},
    ),
    _tool(
        PREVIEW_CLICK,
        "Show where a click at fx/fy would land, WITHOUT clicking: returns the screen with a magenta marker at that "
        "point. Use it before clicking a small or crowded target, and adjust fx/fy until the marker sits on it.",
        {"fx": _FX, "fy": _FY},
    ),
    _tool(
        STEP_COMPLETE,
        "Call this once the current step's success check is visibly met on screen. An independent reviewer checks "
        "the screen before the step is accepted.",
        {"summary": {"type": "string", "description": "One sentence: what you did and what is on screen now."}},
    ),
    _tool(
        STEP_FAILED,
        "Call this if the current step can't be done from here — the screen isn't what the step assumes, you're "
        "stuck, or you've tried the obvious approaches. Giving up cleanly is better than exploring.",
        {"reason": {"type": "string", "description": "One sentence: what's blocking the step."}},
    ),
]

TOOL_NAMES = {t["name"] for t in TOOLS}


def image_block(png: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(png).decode()}}


@dataclass
class ToolOutcome:
    text: str
    is_error: bool = False
    # False for tools whose result already shows the screen (preview_click) —
    # observe then skips its usual fresh screenshot.
    wants_screenshot: bool = True
    # Extra content for the tool_result (preview_click's annotated screenshot).
    images: list[bytes] = field(default_factory=list)


class ToolExecutor:
    """Runs one UI tool call against one machine through the broker."""

    def __init__(self, client: JourneyDriveClient, machine: str, monitor: int | None) -> None:
        self._client = client
        self._machine = machine
        self._monitor = monitor

    async def _target_monitor(self) -> dict:
        # The broker serves this from its per-connection cache, not a live round
        # trip to the machine (docs/BROKER.md's "Monitor layout is cached").
        return resolve_monitor(await self._client.list_monitors(self._machine), self._monitor)

    async def screenshot(self) -> bytes:
        monitor = await self._target_monitor()
        data, _content_type = await self._client.screenshot(self._machine, format="png", monitor=monitor["index"])
        return data

    async def _pixel(self, fx: float, fy: float) -> tuple[int, int]:
        if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
            raise ValueError(f"fx/fy must each be between 0.0 and 1.0, got fx={fx}, fy={fy}")
        return fraction_to_pixel(await self._target_monitor(), fx, fy)

    async def execute(self, name: str, tool_input: dict) -> ToolOutcome:
        try:
            return await self._execute(name, tool_input)
        except (JourneyDriveError, ValueError, KeyError, TypeError) as e:
            # Returned to the model as an error result rather than crashing the
            # run — the action budget still bounds how often it can retry.
            logger.warning("tool %s failed: %s", name, e)
            return ToolOutcome(text=f"Error: {e}", is_error=True)

    async def _execute(self, name: str, tool_input: dict) -> ToolOutcome:
        machine = self._machine
        if name == "click":
            x, y = await self._pixel(tool_input["fx"], tool_input["fy"])
            await self._client.click_mouse(machine, button=tool_input["button"], clicks=tool_input["clicks"], x=x, y=y)
            return ToolOutcome(text=f"Clicked {tool_input['button']} x{tool_input['clicks']}.")
        if name == "move":
            x, y = await self._pixel(tool_input["fx"], tool_input["fy"])
            await self._client.move_mouse(machine, x, y)
            return ToolOutcome(text="Moved the cursor.")
        if name == "scroll":
            await self._client.scroll_mouse(machine, dx=tool_input["dx"], dy=tool_input["dy"])
            return ToolOutcome(text="Scrolled.")
        if name == "type_text":
            await self._client.type_text(machine, tool_input["text"])
            return ToolOutcome(text=f"Typed {len(tool_input['text'])} character(s).")
        if name == "send_keys":
            await self._client.send_keys(machine, tool_input["keys"])
            return ToolOutcome(text=f"Pressed {'+'.join(tool_input['keys'])}.")
        if name == "set_clipboard":
            await self._client.set_clipboard(machine, tool_input["text"])
            return ToolOutcome(text=f"Clipboard set ({len(tool_input['text'])} character(s)). Paste it with send_keys.")
        if name == WAIT:
            seconds = max(0.0, min(_MAX_WAIT_SECONDS, float(tool_input["seconds"])))
            await asyncio.sleep(seconds)
            return ToolOutcome(text=f"Waited {seconds:g}s.")
        if name == PREVIEW_CLICK:
            monitor = await self._target_monitor()
            x, y = await self._pixel(tool_input["fx"], tool_input["fy"])
            data, _content_type = await self._client.screenshot(machine, format="png", monitor=monitor["index"])
            annotated = draw_crosshair(data, x - monitor["left"], y - monitor["top"])
            return ToolOutcome(
                text="The magenta marker shows where that click would land. Nothing was clicked.",
                wants_screenshot=False,
                images=[annotated],
            )
        raise ValueError(f"unknown tool {name!r}")

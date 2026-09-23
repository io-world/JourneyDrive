"""Monitor/coordinate helpers shared by the MCP server and journeydrive_automation.

Pure functions over the broker's monitor list (list_monitors / list_machines
shape: index/left/top/width/height) and raw screenshot bytes — no client or I/O,
so both callers resolve fx/fy and draw preview markers identically.
"""

from __future__ import annotations

import io

from PIL import Image as PILImage
from PIL import ImageDraw


def default_monitor_index(monitors: list[dict]) -> int:
    """The primary physical monitor (index 1) when there is one, else the
    all-monitors bounding box (index 0) — the default every fx/fy tool uses."""
    return 1 if len(monitors) > 1 else 0


def resolve_monitor(monitors: list[dict], index: int | None) -> dict:
    resolved_index = index if index is not None else default_monitor_index(monitors)
    try:
        return monitors[resolved_index]
    except IndexError:
        raise ValueError(f"monitor index {resolved_index} out of range (0..{len(monitors) - 1})") from None


def fraction_to_pixel(monitor: dict, fx: float, fy: float) -> tuple[int, int]:
    """Absolute screen pixel for a 0.0-1.0 fraction of the given monitor."""
    return monitor["left"] + round(fx * monitor["width"]), monitor["top"] + round(fy * monitor["height"])


def draw_crosshair(data: bytes, marker_x: int, marker_y: int) -> bytes:
    """Return a PNG copy of screenshot `data` with a magenta marker at
    (marker_x, marker_y), in that image's own pixel coordinates."""
    with PILImage.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        marker_x = max(0, min(img.width - 1, marker_x))
        marker_y = max(0, min(img.height - 1, marker_y))
        draw = ImageDraw.Draw(img)
        color = (255, 0, 255)  # magenta — visible against most desktop backgrounds
        outline = (0, 0, 0)
        # Scale marker so it stays visible when the image is rendered small in a chat UI.
        s = max(img.width, img.height) / 1000
        size, half_width, gap = max(14, int(22 * s)), max(5, int(8 * s)), max(4, int(5 * s))
        # Four filled arrowheads pointing at the target, not a thin-line cross —
        # a solid fill survives JPEG block compression far better than a 1-3px
        # line, which is what made the old crosshair blur into the background.
        arrows = [
            [(marker_x, marker_y - gap), (marker_x - half_width, marker_y - gap - size), (marker_x + half_width, marker_y - gap - size)],  # top, points down
            [(marker_x, marker_y + gap), (marker_x - half_width, marker_y + gap + size), (marker_x + half_width, marker_y + gap + size)],  # bottom, points up
            [(marker_x - gap, marker_y), (marker_x - gap - size, marker_y - half_width), (marker_x - gap - size, marker_y + half_width)],  # left, points right
            [(marker_x + gap, marker_y), (marker_x + gap + size, marker_y - half_width), (marker_x + gap + size, marker_y + half_width)],  # right, points left
        ]
        for triangle in arrows:
            draw.polygon(triangle, fill=color, outline=outline, width=2)
        r = max(2, int(3 * s))
        draw.ellipse([marker_x - r, marker_y - r, marker_x + r, marker_y + r], fill=color)
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()

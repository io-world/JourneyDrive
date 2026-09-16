from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from mcp.server.mcpserver import Image, MCPServer
from PIL import Image as PILImage
from PIL import ImageDraw

from journeycapture_mcp.client import JourneyCaptureClient
from journeycapture_mcp.config import Settings

logger = logging.getLogger(__name__)


def build_server(client: JourneyCaptureClient, settings: Settings) -> MCPServer:
    server = MCPServer(
        name="journeycapture",
        instructions="Remote-control Windows desktops through a broker that can reach multiple machines. Call "
        "list_machines first to see what's available, then pass that machine id to every other tool. Call "
        "health_check if unsure a specific machine is reachable. Before click_mouse on a small or ambiguous "
        "target, call preview_click first to visually confirm the resolved coordinate actually lands on it.",
    )

    def _prune_old_screenshots(out_dir: Path) -> None:
        if settings.max_saved_screenshots <= 0:
            return
        # Filenames are UTC timestamps (%Y%m%dT%H%M%S_%f), so name order is
        # chronological order — no need to stat each file's mtime.
        files = sorted(out_dir.iterdir(), key=lambda p: p.name)
        excess = len(files) - settings.max_saved_screenshots
        if excess <= 0:
            return
        for old_file in files[:excess]:
            old_file.unlink()
        logger.info("pruned %d old screenshot(s) from %s (cap: %d)", excess, out_dir, settings.max_saved_screenshots)

    def _save_screenshot(data: bytes, image_format: str) -> None:
        try:
            out_dir = Path(settings.screenshot_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
            out_path = out_dir / f"{timestamp}.{image_format}"
            # datetime.now()'s resolution can be coarser than 1 microsecond on
            # Windows, so back-to-back calls can collide on the same timestamp;
            # disambiguate rather than silently overwriting the earlier file.
            collision_counter = 1
            while out_path.exists():
                out_path = out_dir / f"{timestamp}_{collision_counter}.{image_format}"
                collision_counter += 1
            out_path.write_bytes(data)
            logger.info("saved screenshot to %s", out_path)
            _prune_old_screenshots(out_dir)
        except OSError as e:
            # Saving/pruning is a debugging convenience, not core functionality —
            # don't let a failure here (e.g. disk full, permissions) break the
            # actual take_screenshot call.
            logger.warning("failed to save/prune screenshot copies: %s", e)

    def _resolve_monitor(monitors: list[dict], index: int | None) -> dict:
        resolved_index = index if index is not None else (1 if len(monitors) > 1 else 0)
        try:
            return monitors[resolved_index]
        except IndexError:
            raise ValueError(f"monitor index {resolved_index} out of range (0..{len(monitors) - 1})") from None

    async def _resolve_xy(
        machine: str,
        x: int | None,
        y: int | None,
        fx: float | None,
        fy: float | None,
        monitor: int | None,
        *,
        allow_neither: bool = False,
    ) -> tuple[int | None, int | None]:
        if (x is None) != (y is None):
            raise ValueError("x and y must be given together.")
        if (fx is None) != (fy is None):
            raise ValueError("fx and fy must be given together.")
        have_xy = x is not None
        have_fxy = fx is not None
        if have_xy and have_fxy:
            raise ValueError("Provide either x/y or fx/fy, not both.")
        if not have_xy and not have_fxy:
            if allow_neither:
                return None, None
            raise ValueError("Provide either x/y (pixel coordinates) or fx/fy (fraction of the screen, 0.0-1.0).")
        if have_xy:
            return x, y
        monitors = await client.list_monitors(machine)
        target = _resolve_monitor(monitors, monitor)
        return target["left"] + round(fx * target["width"]), target["top"] + round(fy * target["height"])

    def _draw_crosshair(data: bytes, marker_x: int, marker_y: int) -> bytes:
        with PILImage.open(io.BytesIO(data)) as img:
            img = img.convert("RGB")
            marker_x = max(0, min(img.width - 1, marker_x))
            marker_y = max(0, min(img.height - 1, marker_y))
            draw = ImageDraw.Draw(img)
            color = (255, 0, 255)  # magenta — visible against most desktop backgrounds
            outline = (0, 0, 0)
            size, half_width, gap = 14, 5, 4
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
            draw.ellipse([marker_x - 2, marker_y - 2, marker_x + 2, marker_y + 2], fill=color)
            out = io.BytesIO()
            img.save(out, format="PNG")
            return out.getvalue()

    @server.tool()
    async def list_machines() -> list[dict]:
        """List machines currently connected to the broker, each with its monitor
        layout (machine_id, monitors) — resolution reported once when that machine
        connected to the broker, not re-queried here. Call this first — every other
        tool needs a machine id from here."""
        logger.info("list_machines")
        return await client.list_machines()

    @server.tool()
    async def health_check(machine: str) -> dict:
        """Check whether the given machine is reachable through the broker and report its version."""
        return await client.health(machine)

    @server.tool()
    async def list_monitors(machine: str) -> list[dict]:
        """List the given machine's monitors with pixel bounds in the same coordinate space move_mouse uses. Index 0 is the bounding box of all monitors combined; physical monitors start at index 1. Always read the actual width/height from here (or from a screenshot's real pixel dimensions) before computing click/move coordinates — never assume a resolution like 1366x768. Guessing wrong is why a click can silently land on empty desktop instead of the intended icon."""
        logger.info("list_monitors machine=%s", machine)
        return await client.list_monitors(machine)

    @server.tool()
    async def take_screenshot(
        machine: str,
        format: Literal["png", "jpeg"] | None = None,
        quality: int | None = None,
        monitor: int | None = None,
        fx1: float | None = None,
        fy1: float | None = None,
        fx2: float | None = None,
        fy2: float | None = None,
    ) -> Image:
        """Capture a screenshot of one monitor on the given machine. format/quality default to that machine's own config when omitted; monitor index comes from list_monitors. Measure coordinates against this image's actual pixel dimensions (or list_monitors) — don't assume a resolution. Optionally crop to a region: fx1/fy1 is the top-left corner and fx2/fy2 is the bottom-right corner, each as a fraction 0.0-1.0 of the monitor's width/height — useful for zooming in on a small target to read it more clearly. fx1 must be less than fx2, and fy1 less than fy2. A cropped result is always PNG, regardless of the requested format."""
        logger.info("take_screenshot machine=%s format=%s quality=%s monitor=%s fx1=%s fy1=%s fx2=%s fy2=%s", machine, format, quality, monitor, fx1, fy1, fx2, fy2)
        data, content_type = await client.screenshot(machine, format=format, quality=quality, monitor=monitor)
        image_format = "png" if "png" in content_type else "jpeg"
        if fx1 is not None or fy1 is not None or fx2 is not None or fy2 is not None:
            if None in (fx1, fy1, fx2, fy2):
                raise ValueError("fx1, fy1, fx2, and fy2 must all be provided together for region crop.")
            monitors = await client.list_monitors(machine)
            target = _resolve_monitor(monitors, monitor)
            w, h = target["width"], target["height"]
            x1, y1 = round(fx1 * w), round(fy1 * h)
            x2, y2 = round(fx2 * w), round(fy2 * h)
            if x2 <= x1 or y2 <= y1:
                raise ValueError(
                    f"Invalid crop region: fx1/fy1 ({fx1}, {fy1}) must be strictly less than fx2/fy2 "
                    f"({fx2}, {fy2}) — resolved to pixel box ({x1}, {y1})-({x2}, {y2}), which is empty or inverted."
                )
            with PILImage.open(io.BytesIO(data)) as img:
                cropped = img.crop((x1, y1, x2, y2))
                out = io.BytesIO()
                cropped.save(out, format="PNG")
                data = out.getvalue()
            image_format = "png"
        # Saved *after* any crop, so the debug copy on disk always matches what the
        # model actually received — not the full pre-crop capture.
        if settings.save_screenshots:
            _save_screenshot(data, image_format)
        return Image(data=data, format=image_format)

    @server.tool()
    async def preview_click(
        machine: str,
        x: int | None = None,
        y: int | None = None,
        fx: float | None = None,
        fy: float | None = None,
        monitor: int | None = None,
    ) -> Image:
        """Preview where click_mouse would land, WITHOUT clicking: takes a fresh screenshot and draws a magenta crosshair at the resolved x/y (pixels) or fx/fy (fraction 0.0-1.0) position. Use this before click_mouse on small or ambiguous targets (tabs, sidebar thumbnails, anything near another clickable element) to visually confirm the marker actually lands on the intended target — a fraction estimated by eye from a screenshot has no other way to be verified before the click commits. If the marker is off-target, adjust fx/fy and call this again rather than guessing a correction; only call click_mouse once the marker looks right. monitor selects which monitor fx/fy is relative to (default: the primary physical monitor, same as list_monitors index 1) — the same monitor is used both to resolve the coordinate and to capture the screenshot, so the marker is always positioned consistently with the image."""
        monitors = await client.list_monitors(machine)
        resolved_monitor = monitor if monitor is not None else (1 if len(monitors) > 1 else 0)
        resolved_x, resolved_y = await _resolve_xy(machine, x, y, fx, fy, resolved_monitor)
        logger.info(
            "preview_click machine=%s x=%s y=%s fx=%s fy=%s monitor=%s -> resolved (%s, %s)",
            machine, x, y, fx, fy, resolved_monitor, resolved_x, resolved_y,
        )
        data, _content_type = await client.screenshot(machine, monitor=resolved_monitor)
        try:
            target = monitors[resolved_monitor]
        except IndexError:
            raise ValueError(f"monitor index {resolved_monitor} out of range (0..{len(monitors) - 1})") from None
        annotated = _draw_crosshair(data, resolved_x - target["left"], resolved_y - target["top"])
        return Image(data=annotated, format="png")

    @server.tool()
    async def move_mouse(
        machine: str,
        x: int | None = None,
        y: int | None = None,
        fx: float | None = None,
        fy: float | None = None,
        monitor: int | None = None,
        relative: bool = False,
    ) -> dict:
        """Move the cursor on the given machine to an absolute screen position (x/y in pixels, or fx/fy as a fraction 0.0-1.0 of the target monitor's width/height), or by a relative offset (x/y only). (0, 0) is the primary monitor's top-left corner; monitors above/left of it have negative coordinates. Prefer fx/fy over x/y when targeting something visually identified from a screenshot — it's correct regardless of what resolution the image was actually perceived at, unlike guessing a pixel number against an assumed resolution. monitor selects which monitor fx/fy is relative to (default: the primary physical monitor, same as list_monitors index 1); fx/fy can't be combined with relative=True. Returns the resulting position — check it against what was requested."""
        if relative:
            if fx is not None or fy is not None:
                raise ValueError(
                    "fx/fy can't be combined with relative=True — a fraction of the screen isn't meaningful as a relative offset."
                )
            if x is None or y is None:
                raise ValueError("x and y are required when relative=True.")
            resolved_x, resolved_y = x, y
        else:
            resolved_x, resolved_y = await _resolve_xy(machine, x, y, fx, fy, monitor)
        logger.info(
            "move_mouse machine=%s x=%s y=%s fx=%s fy=%s monitor=%s relative=%s -> resolved (%s, %s)",
            machine, x, y, fx, fy, monitor, relative, resolved_x, resolved_y,
        )
        return await client.move_mouse(machine, resolved_x, resolved_y, relative=relative)

    @server.tool()
    async def click_mouse(
        machine: str,
        button: Literal["left", "right", "middle"] = "left",
        action: Literal["click", "down", "up"] = "click",
        clicks: int = 1,
        x: int | None = None,
        y: int | None = None,
        fx: float | None = None,
        fy: float | None = None,
        monitor: int | None = None,
    ) -> dict:
        """Click a mouse button on the given machine, optionally moving to x/y (pixels) or fx/fy (fraction 0.0-1.0 of the target monitor's width/height) first — omit all four to click at the current cursor position. Prefer fx/fy over x/y when targeting something visually identified from a screenshot — it's correct regardless of what resolution the image was actually perceived at. monitor selects which monitor fx/fy is relative to (default: the primary physical monitor, same as list_monitors index 1). Use action=down/up (instead of the default click) to hold a button across separate calls, e.g. for a drag — an unmatched down auto-releases after ~10s on the target machine."""
        resolved_x, resolved_y = await _resolve_xy(machine, x, y, fx, fy, monitor, allow_neither=True)
        logger.info(
            "click_mouse machine=%s button=%s action=%s clicks=%d x=%s y=%s fx=%s fy=%s monitor=%s -> resolved x=%s y=%s",
            machine, button, action, clicks, x, y, fx, fy, monitor, resolved_x, resolved_y,
        )
        return await client.click_mouse(
            machine, button=button, action=action, clicks=clicks, x=resolved_x, y=resolved_y
        )

    @server.tool()
    async def scroll_mouse(machine: str, dx: int = 0, dy: int = 0) -> dict:
        """Scroll the mouse wheel on the given machine at the current cursor position, in wheel notches (not pixels). Positive dy scrolls up, negative scrolls down; positive dx scrolls right, negative scrolls left."""
        logger.info("scroll_mouse machine=%s dx=%d dy=%d", machine, dx, dy)
        return await client.scroll_mouse(machine, dx=dx, dy=dy)

    @server.tool()
    async def type_text(machine: str, text: str) -> dict:
        """Type printable text into whatever window has focus on the given machine. For non-printable keys (Enter, Tab, Ctrl+C, etc.) use send_keys instead — this only sends character keystrokes."""
        # Log the length only, never the text itself — it could be a password or
        # anything else sensitive typed into a remote window.
        logger.info("type_text machine=%s %d character(s)", machine, len(text))
        return await client.type_text(machine, text)

    @server.tool()
    async def send_keys(machine: str, keys: list[str], action: Literal["press", "release", "tap"] = "tap") -> dict:
        """Send one or more named keys to the given machine, e.g. special keys or chords like ["ctrl", "c"] that type_text can't express. Each key is either a single printable character or a pynput.keyboard.Key name (enter, tab, esc, backspace, space, shift, ctrl, alt, cmd, up, down, left, right, f1-f20, etc). action="tap" (default) presses then releases; "press"/"release" hold or let go without the other half — an unmatched press auto-releases after ~10s on the target machine."""
        logger.info("send_keys machine=%s keys=%s action=%s", machine, keys, action)
        return await client.send_keys(machine, keys, action=action)

    @server.tool()
    async def get_clipboard(machine: str) -> dict:
        """Read the current text content of the clipboard on the given machine. Useful for inspecting what the user last copied without needing a screenshot."""
        logger.info("get_clipboard machine=%s", machine)
        text = await client.get_clipboard(machine)
        logger.info("get_clipboard machine=%s -> %d character(s)", machine, len(text))
        return {"text": text}

    @server.tool()
    async def set_clipboard(machine: str, text: str) -> dict:
        """Write text to the clipboard on the given machine. Combined with send_keys(["ctrl", "v"]), this is faster and more reliable than type_text for long strings (URLs, code blocks, paragraphs) — the text is pasted as a single operation rather than sent one character at a time."""
        # Log length only — clipboard content could be a password or other sensitive text.
        logger.info("set_clipboard machine=%s %d character(s)", machine, len(text))
        return await client.set_clipboard(machine, text)

    return server

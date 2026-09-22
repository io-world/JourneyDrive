from __future__ import annotations

import io
from typing import Literal

import mss
from PIL import Image

from journeydrive_windows_thinclient.schemas import MonitorInfo


def list_monitors() -> list[MonitorInfo]:
    with mss.mss() as sct:
        return [
            MonitorInfo(
                index=index,
                left=mon["left"],
                top=mon["top"],
                width=mon["width"],
                height=mon["height"],
            )
            for index, mon in enumerate(sct.monitors)
        ]


def take_screenshot(
    monitor: int = 0,
    format: Literal["png", "jpeg"] = "png",
    quality: int = 75,
) -> tuple[bytes, str]:
    with mss.mss() as sct:
        monitors = sct.monitors
        if monitor < 0 or monitor >= len(monitors):
            raise ValueError(f"monitor index {monitor} out of range (0..{len(monitors) - 1})")
        shot = sct.grab(monitors[monitor])

    if format == "png":
        return mss.tools.to_png(shot.rgb, shot.size), "image/png"

    image = Image.frombytes("RGB", shot.size, shot.rgb)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue(), "image/jpeg"

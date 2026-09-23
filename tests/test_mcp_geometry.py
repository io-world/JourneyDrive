from __future__ import annotations

import io

import pytest
from PIL import Image as PILImage

pytest.importorskip("mcp")  # controller-side-only extra; not installed for the thin-client builds

from journeydrive_mcp.geometry import default_monitor_index, draw_crosshair, fraction_to_pixel, resolve_monitor

ONE = [{"index": 0, "left": 0, "top": 0, "width": 1920, "height": 1080}]
TWO = ONE + [{"index": 1, "left": -1280, "top": 0, "width": 1280, "height": 1024}]


def test_default_monitor_is_the_primary_physical_one_when_there_is_one() -> None:
    assert default_monitor_index(ONE) == 0
    assert default_monitor_index(TWO) == 1
    assert resolve_monitor(TWO, None)["index"] == 1
    assert resolve_monitor(TWO, 0)["index"] == 0


def test_out_of_range_monitor_is_a_clear_error() -> None:
    with pytest.raises(ValueError, match=r"monitor index 5 out of range \(0..1\)"):
        resolve_monitor(TWO, 5)


def test_fraction_to_pixel_includes_the_monitor_offset() -> None:
    assert fraction_to_pixel(TWO[1], 0.5, 0.25) == (-640, 256)
    assert fraction_to_pixel(ONE[0], 1.0, 1.0) == (1920, 1080)


def test_draw_crosshair_returns_a_same_size_png_with_the_marker_on_target() -> None:
    buf = io.BytesIO()
    PILImage.new("RGB", (400, 200), color=(0, 0, 0)).save(buf, format="JPEG")
    out = draw_crosshair(buf.getvalue(), 100, 50)
    with PILImage.open(io.BytesIO(out)) as img:
        assert img.format == "PNG"
        assert img.size == (400, 200)
        assert img.getpixel((100, 50)) == (255, 0, 255)

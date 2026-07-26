"""Render the app icons as PNGs, using nothing but the standard library.

Shapes are drawn from signed distance fields, so a single sample per pixel is
enough for clean anti-aliased edges — no supersampling, no image library, and
the icons can be regenerated from source at any time:

    python tools/make_icons.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"

BACKGROUND = (0x67, 0x50, 0xA4)
GLYPH = (0xEA, 0xDD, 0xFF)


# ----------------------------------------------------------------- distances


def _sd_round_box(px: float, py: float, half: float, radius: float) -> float:
    qx = abs(px) - half + radius
    qy = abs(py) - half + radius
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    return outside + min(max(qx, qy), 0.0) - radius


def _sd_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    pax, pay = px - ax, py - ay
    bax, bay = bx - ax, by - ay
    denominator = bax * bax + bay * bay
    t = 0.0 if denominator == 0 else max(0.0, min(1.0, (pax * bax + pay * bay) / denominator))
    return math.hypot(pax - bax * t, pay - bay * t)


def _coverage(distance: float) -> float:
    """Antialias by turning a distance in pixels into an alpha ramp."""
    return max(0.0, min(1.0, 0.5 - distance))


def render(size: int, *, maskable: bool) -> bytes:
    """Draw the mark: three rounded bars with a ring over the last one."""
    # A maskable icon may be cropped to the central 80%, so the mark shrinks and
    # the background has to bleed all the way to the edges.
    glyph_scale = 0.62 if maskable else 0.80
    corner = size * 0.5 if maskable else size * 0.235

    centre = size / 2
    unit = size * glyph_scale / 48  # the mark is authored on a 48-unit grid

    def to_px(value: float) -> float:
        return centre + (value - 24) * unit

    stroke = 4 * unit / 2
    bars = [((10, 15), (38, 15)), ((10, 24), (27, 24)), ((10, 33), (32, 33))]
    ring_centre = (36, 30)
    ring_radius = 7 * unit

    rows = bytearray()
    for y in range(size):
        rows.append(0)  # PNG filter type 0
        py = y + 0.5
        for x in range(size):
            px = x + 0.5

            background = _coverage(_sd_round_box(px - centre, py - centre, centre, corner))
            if background <= 0:
                rows.extend((0, 0, 0, 0))
                continue

            distance = min(
                _sd_segment(px, py, to_px(ax), to_px(ay), to_px(bx), to_px(by)) - stroke
                for (ax, ay), (bx, by) in bars
            )
            to_ring = math.hypot(px - to_px(ring_centre[0]), py - to_px(ring_centre[1]))
            ring = abs(to_ring - ring_radius)
            distance = min(distance, ring - stroke)
            glyph = _coverage(distance)

            red = round(BACKGROUND[0] + (GLYPH[0] - BACKGROUND[0]) * glyph)
            green = round(BACKGROUND[1] + (GLYPH[1] - BACKGROUND[1]) * glyph)
            blue = round(BACKGROUND[2] + (GLYPH[2] - BACKGROUND[2]) * glyph)
            rows.extend((red, green, blue, round(background * 255)))

    return _png(size, size, bytes(rows))


# --------------------------------------------------------------------- png


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _png(width: int, height: int, raw: bytes) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def main() -> None:
    targets = [
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        ("icon-maskable-512.png", 512, True),
        ("apple-touch-icon.png", 180, True),
    ]
    for name, size, maskable in targets:
        path = STATIC / name
        path.write_bytes(render(size, maskable=maskable))
        print(f"{name}: {size}×{size}, {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()

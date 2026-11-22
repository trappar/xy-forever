"""The device's screen for host tools (xyf-host screenshot): the framebuffer as diag.screen reads it, as a PNG."""

import io
import struct

from PIL import Image

SCREEN_BYTES, SCREEN_W, SCREEN_H, SCREEN_STRIDE = 0x3C000, 480, 216, 256  # ui_gfx.hpp


def screen_png(raw):
    """Framebuffer (480 columns stored right-to-left, 256 RGB565 pixels each) -> PNG bytes."""
    rgb = bytearray()
    for y in range(SCREEN_H):
        for x in range(SCREEN_W):
            v = struct.unpack_from('<H', raw, ((SCREEN_W - 1 - x) * SCREEN_STRIDE + y) * 2)[0]
            rgb += bytes(
                ((v >> 11) << 3 | (v >> 13), ((v >> 5) & 63) << 2 | ((v >> 9) & 3), (v & 31) << 3 | ((v >> 2) & 7))
            )
    out = io.BytesIO()
    Image.frombytes('RGB', (SCREEN_W, SCREEN_H), bytes(rgb)).save(out, 'PNG')
    return out.getvalue()

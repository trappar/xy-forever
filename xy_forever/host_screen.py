"""The device's screen for host tools (xyf_host.py screenshot): the framebuffer as diag.screen reads it, as a PNG."""
import struct
import zlib

SCREEN_BYTES, SCREEN_W, SCREEN_H, SCREEN_STRIDE = 0x3c000, 480, 216, 256   # ui_gfx.hpp


def screen_png(raw):
    """Framebuffer (480 columns stored right-to-left, 256 RGB565 pixels each) -> PNG bytes."""
    rows = []
    for y in range(SCREEN_H):
        row = bytearray(b'\0')                                  # PNG filter: none
        for x in range(SCREEN_W):
            v = struct.unpack_from('<H', raw, ((SCREEN_W - 1 - x) * SCREEN_STRIDE + y) * 2)[0]
            row += bytes(((v >> 11) << 3 | (v >> 13), ((v >> 5) & 63) << 2 | ((v >> 9) & 3), (v & 31) << 3 | ((v >> 2) & 7)))
        rows.append(bytes(row))

    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', SCREEN_W, SCREEN_H, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(b''.join(rows), 9)) + chunk(b'IEND', b''))

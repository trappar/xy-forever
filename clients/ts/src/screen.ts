// The display framebuffer that screen.read returns, and its decoding (the public entry re-exports both).

/** The display framebuffer (screen.read): 480 columns stored right to left, 256 RGB565 pixels each. */
export const SCREEN = { bytes: 0x3c000, width: 480, height: 216, stride: 256 } as const;

/** Framebuffer bytes to RGBA pixels, row by row (as ImageData takes them). */
export function decodeScreen(raw: Uint8Array): { width: number; height: number; rgba: Uint8ClampedArray } {
  if (raw.length < SCREEN.bytes) throw new RangeError(`a screenshot is ${SCREEN.bytes} bytes`);
  const { width, height, stride } = SCREEN;
  const rgba = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const at = ((width - 1 - x) * stride + y) * 2;
      const v = raw[at]! | raw[at + 1]! << 8;
      const r = v >> 11, g = (v >> 5) & 63, b = v & 31, o = (y * width + x) * 4;
      rgba[o] = r << 3 | r >> 2; rgba[o + 1] = g << 2 | g >> 4; rgba[o + 2] = b << 3 | b >> 2; rgba[o + 3] = 255;
    }
  }
  return { width, height, rgba };
}

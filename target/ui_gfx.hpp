#ifndef XYF_UI_GFX_HPP
#define XYF_UI_GFX_HPP
#include <stdint.h>
// XY Forever drawing layer: integer-only, freestanding, no stock calls.
// Framebuffer layout follows the stock 1.2.1 plotter 889ee89a: 480 columns
// stored right-to-left, 256 16-bit pixels per column (216 visible rows), so
// pixel (x, y) lives at fb[(479 - x) * 256 + y]. Total 0x3c000 bytes.
namespace xyf { namespace gfx {
typedef uint16_t Color;
static const int width = 480, height = 216, column_stride = 256;
static const uint32_t framebuffer_halfwords = 480u * 256u;
// RGB565 (channel order on the panel unconfirmed; greys are order-independent).
static const Color black = 0x0000, white = 0xffff, grey = 0x8410, dark = 0x2104, accent = 0xfd20;
inline Color rgb(uint32_t r, uint32_t g, uint32_t b) { return Color((r >> 3) << 11 | (g >> 2) << 5 | (b >> 3)); }

void clear(uint16_t* fb, Color color);
void fill(uint16_t* fb, int x, int y, int w, int h, Color color);
// Built-in 5x7 font (ASCII 32..126), cell 6x8 per scale unit. Returns end x.
int text(uint16_t* fb, int x, int y, const char* s, Color color, int scale);
int text_width(const char* s, int scale);
// XY Forever mark: two interlocked rings (an infinity sign) with "XY" and
// "FOREVER" beside it, logo_height px tall from y, left edge at x. Returns the logo width in pixels.
static const int logo_height = 35, logo_gap = 10;
int logo(uint16_t* fb, int x, int y, Color mark, Color word);
}}
#endif

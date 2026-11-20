#ifndef XYF_UI_SCREENS_HPP
#define XYF_UI_SCREENS_HPP
#include <stdint.h>
#include "ui_pages.hpp"
// XY Forever full-screen layouts drawn with ui_gfx (no stock calls). Each
// draw function repaints the whole framebuffer. The menu has the logo header; a message (About, the
// service and boot screens) the compact title bar (ui_pages compact_header).
namespace xyf { namespace ui {
static const uint32_t max_items = 7, max_lines = 8;   // up to seven home rows (five since #21)
struct Menu {
    const char* items[max_items];
    uint32_t count, selected;
    const char* status;     // one line under the list (may be empty; the activity replaces it)
    const char* hint;       // footer
    const Chrome* chrome;   // the USB mark, the activity, the build (may be null)
    uint32_t dimmed;        // 193: bit i: row i is drawn grey (it needs storage that is not ready)
};
struct Message {
    const char* heading;
    const char* lines[max_lines];
    uint32_t count;
    const char* hint;
    const Chrome* chrome;
};
void draw_menu(uint16_t* fb, const Menu& menu);
void draw_message(uint16_t* fb, const Message& message);
} }
#endif

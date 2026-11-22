#include "ui_screens.hpp"
#include "ui_gfx.hpp"
#include "ui_pages.hpp"
namespace xyf { namespace ui {
namespace {
using namespace gfx;
const Color item_text = white, selected_bar = white, selected_text = black;
const int margin = 16, header_height = home_bar;

void footer(uint16_t* fb, const char* hint, const Chrome* chrome) { footer_line(fb, height - 14, hint, chrome); }
}

void draw_menu(uint16_t* fb, const Menu& menu) {
    home_header(fb, menu.chrome);
    // Six rows clear the status line at 20 px; seven (Settings) at 18 px, the bar 2 px above the text.
    const bool seven = menu.count > 6;
    const int top = header_height + (seven ? 4 : 8), row = seven ? 18 : menu.count > 5 ? 20 : 22;
    const int above = seven ? 2 : 4, bar = seven ? row - 1 : row - 2;
    for (uint32_t i = 0; i < menu.count && i < max_items; ++i) {
        const int y = top + int(i) * row;
        const bool on = i == menu.selected, dim = menu.dimmed >> i & 1u;
        if (on) fill(fb, margin - 6, y - above, width - 2 * margin + 12, bar, dim ? grey : selected_bar);
        text(fb, margin + 4, y, menu.items[i], on ? selected_text : dim ? grey : item_text, 2);
    }
    home_footer(fb, menu.status, menu.hint, menu.chrome);
}

void draw_message(uint16_t* fb, const Message& message) {
    compact_header(fb, message.heading, "", message.chrome);   // the logo only on the home menu
    int y = compact_top + 6;
    for (uint32_t i = 0; i < message.count && i < max_lines; ++i, y += 12)
        text(fb, margin, y, message.lines[i], item_text, 1);
    footer(fb, message.hint, message.chrome);
}
}}

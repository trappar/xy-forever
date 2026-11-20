#include "ui_pages.hpp"
#include "ui_gfx.hpp"
#include "ui_screens.hpp"
namespace xyf { namespace ui {
namespace {
using namespace gfx;
const int margin = 16, row_height = 22;

void hint_line(uint16_t* fb, const char* hint, const Chrome* chrome) { footer_line(fb, height - 14, hint, chrome); }

void button(uint16_t* fb, int x, int y, const char* label, bool on) {
    const int w = text_width(label, 2) + 20;
    if (on) fill(fb, x, y, w, 24, white);
    else { fill(fb, x, y, w, 1, grey); fill(fb, x, y + 23, w, 1, grey); fill(fb, x, y, 1, 24, grey); fill(fb, x + w - 1, y, 1, 24, grey); }
    text(fb, x + 10, y + 4, label, on ? black : white, 2);
}
}

void usb_mark(uint16_t* fb, int right, int y, bool usb, bool mtp) {
    if (mtp) {
        fill(fb, right - marks_w, y, mark_w, mark_h, accent);
        text(fb, right - marks_w + 3, y + 2, "MTP", black, 1);
    }
    if (usb) {
        fill(fb, right - mark_w, y, mark_w, mark_h, accent);
        text(fb, right - mark_w + 3, y + 2, "USB", black, 1);
    }
}

void footer_line(uint16_t* fb, int y, const char* hint, const Chrome* chrome) {
    if (chrome && chrome->activity && *chrome->activity) text(fb, margin, y, chrome->activity, accent, 1);
    else if (hint && *hint) text(fb, margin, y, hint, grey, 1);
}

namespace {
const int title_gap = 12;                             // after the title, and before the USB mark's place
int subtitle_x(const char* title) { return margin + text_width(title, 2) + title_gap; }
int subtitle_end() { return width - margin - marks_w - title_gap; }  // the marks' place stays free when they are off
}

uint32_t subtitle_room(const char* title) {
    const int room = subtitle_end() - subtitle_x(title) + 1;           // n characters are 6 n - 1 px wide
    return room < 5 ? 0 : scale(uint32_t(room + 1), 1, 6);           // no divide helper in the cold build
}

void compact_header(uint16_t* fb, const char* title, const char* subtitle, const Chrome* chrome) {
    clear(fb, black);
    usb_mark(fb, width - margin, compact_mark_y, chrome && chrome->usb, chrome && chrome->mtp);
    text(fb, margin, 4, title, white, 2);
    if (subtitle && *subtitle) {                      // as many characters as fit before the mark's place
        char cut[64];
        const uint32_t room = subtitle_room(title);
        uint32_t n = 0;
        while (subtitle[n] && n + 1 < sizeof(cut) && n < room) { cut[n] = subtitle[n]; ++n; }
        cut[n] = 0;
        text(fb, subtitle_x(title), 9, cut, grey, 1);
    }
    fill(fb, margin, compact_bar, width - 2 * margin, 1, dark);
}

void home_header(uint16_t* fb, const Chrome* chrome) {
    clear(fb, black);
    logo(fb, home_logo_x, home_logo_y, accent, white);
    usb_mark(fb, width - margin, home_logo_y, chrome && chrome->usb, chrome && chrome->mtp);
    if (chrome && chrome->activity && *chrome->activity)   // 188: right-aligned under the marks
        text(fb, width - margin - text_width(chrome->activity, 1), home_activity_y, chrome->activity, accent, 1);
    fill(fb, margin, home_bar, width - 2 * margin, 1, dark);
}

void home_footer(uint16_t* fb, const char* status, const char* hint, const Chrome* chrome) {
    if (status && *status) text(fb, margin, height - 30, status, accent, 1);   // 188: the activity is in the header
    if (hint && *hint) text(fb, margin, height - 14, hint, grey, 1);
    if (chrome && chrome->version && *chrome->version)
        text(fb, width - margin - text_width(chrome->version, 1), height - 14, chrome->version, grey, 1);
}

uint32_t scale(uint32_t a, uint32_t b, uint32_t c) {
    // Exact floor(a * b / c): 64-bit product from 16-bit halves, then 64/32
    // shift-subtract division; saturates at 0xffffffff. No 64-bit types, so
    // no libgcc helpers in the cold build.
    if (!c) return 0;
    const uint32_t al = a & 0xffffu, ah = a >> 16, bl = b & 0xffffu, bh = b >> 16;
    const uint32_t ll = al * bl, lh = al * bh, hl = ah * bl, hh = ah * bh;
    uint32_t lo = ll, hi = hh, carry = 0;
    const uint32_t mid = lh + hl;
    if (mid < lh) hi += 0x10000u;                      // carry out of the middle sum
    lo += mid << 16;
    if (lo < (mid << 16)) ++carry;
    hi += (mid >> 16) + carry;
    uint32_t q = 0, r = 0;
    for (int bit = 63; bit >= 0; --bit) {
        const uint32_t top = r >> 31;
        r = (r << 1) | ((bit >= 32 ? hi >> (bit - 32) : lo >> bit) & 1u);
        if (top || r >= c) {
            r -= c;
            if (bit >= 32) return 0xffffffffu;           // quotient exceeds 32 bits
            q |= 1u << bit;
        }
    }
    return q;
}

uint32_t list_first(uint32_t count, uint32_t selected) {
    if (count <= list_visible || selected < list_visible / 2) return 0;
    const uint32_t first = selected - list_visible / 2;
    return first + list_visible > count ? count - list_visible : first;
}

void draw_list(uint16_t* fb, const List& list) {
    compact_header(fb, list.heading, "", list.chrome);
    const int top = compact_top + 4;
    const uint32_t first = list_first(list.count, list.selected);
    for (uint32_t i = 0; i < list_visible && first + i < list.count; ++i) {
        const ListRow& row = list.rows[first + i];
        const int y = top + int(i) * row_height;
        const bool on = first + i == list.selected;
        if (on) fill(fb, margin - 6, y - 3, width - 2 * margin + 12, row_height - 2, white);
        const Color fg = on ? black : white;
        int x = text(fb, margin + 4, y, row.title, fg, 2);
        if (row.marked) text(fb, x + 8, y + 4, "(default)", on ? dark : accent, 1);
        if (row.detail && *row.detail)
            text(fb, width - margin - 4 - text_width(row.detail, 1), y + 4, row.detail, on ? dark : grey, 1);
    }
    if (list.count > list_visible) {          // scroll bar on the right edge
        const int track = int(list_visible) * row_height - 4;
        const int knob = int(scale(uint32_t(track), list_visible, list.count));
        const int at = int(scale(uint32_t(track - knob), first, list.count - list_visible));
        fill(fb, width - 6, top - 2, 2, track, dark);
        fill(fb, width - 6, top - 2 + at, 2, knob, accent);
    }
    if (!list.count) text(fb, margin, top, "Nothing here yet.", grey, 2);
    hint_line(fb, list.hint, list.chrome);
}

void draw_confirm(uint16_t* fb, const Confirm& c) {
    compact_header(fb, c.heading, "", c.chrome);
    int y = compact_top + 6;
    for (uint32_t i = 0; i < c.count && i < 4; ++i, y += 12) text(fb, margin, y, c.lines[i], grey, 1);
    const int by = height - 44;
    button(fb, margin, by, c.cancel, !c.accept_selected);
    const int aw = text_width(c.accept, 2) + 20;
    button(fb, width - margin - aw, by, c.accept, c.accept_selected);
    hint_line(fb, "- / +  choose      PLAY  confirm", c.chrome);
}

void draw_choice(uint16_t* fb, const Choice& c) {
    compact_header(fb, c.heading, "", c.chrome);
    int y = compact_top + 6;
    for (uint32_t i = 0; i < c.count && i < 4; ++i, y += 12) text(fb, margin, y, c.lines[i], grey, 1);
    int x = margin;
    for (uint32_t i = 0; i != 3; ++i) {
        if (!c.buttons[i]) continue;
        button(fb, x, height - 44, c.buttons[i], i == c.selected);
        x += text_width(c.buttons[i], 2) + 36;
    }
    hint_line(fb, "- / +  choose      PLAY  confirm      STOP  back", c.chrome);
}

void draw_progress(uint16_t* fb, const Progress& p) {
    compact_header(fb, p.heading, "", p.chrome);
    const int bar_y = compact_top + 10, bar_w = width - 2 * margin, bar_h = 16;
    if (p.step && *p.step) text(fb, margin, bar_y + bar_h + 8, p.step, white, 2);
    fill(fb, margin, bar_y, bar_w, bar_h, dark);
    const uint32_t done = p.done > p.total ? p.total : p.done;
    const int filled = int(scale(uint32_t(bar_w), done, p.total));
    fill(fb, margin, bar_y, filled, bar_h, accent);
    // percentage text: 0..100
    const uint32_t percent = scale(100, done, p.total);
    char digits[5] = {0, 0, 0, '%', 0};
    uint32_t v = percent, n = 0;
    if (v >= 100) { digits[n++] = '1'; v -= 100; digits[n++] = '0'; digits[n++] = '0'; }
    else { if (v >= 10) { char t = '0'; while (v >= 10) { v -= 10; ++t; } digits[n++] = t; } digits[n++] = char('0' + v); }
    digits[n++] = '%'; digits[n] = 0;
    text(fb, margin, bar_y + bar_h + 30, digits, grey, 1);
    if (p.warning && *p.warning) text(fb, margin, height - 30, p.warning, accent, 1);
    hint_line(fb, "", p.chrome);                      // the activity line only
}

void draw_home_empty(uint16_t* fb, const Chrome* chrome) {
    home_header(fb, chrome);                          // a home screen: the logo header (as ui_screens)
    int y = home_bar + 8;
    text(fb, margin, y, "No firmware found", white, 2);
    y += 24;
    text(fb, margin, y, "Connect USB (MTP), then copy an", grey, 1);
    text(fb, margin, y + 12, "official .tfw or an open-xy build into", grey, 1);
    text(fb, margin, y + 24, "xy-forever/inbox/ on the OP-XY.", grey, 1);
    // 188: no MTP mode row (always on); #21: one row, About, always selected
    fill(fb, margin - 6, y + 46 - 3, width - 2 * margin + 12, 22, white);
    text(fb, margin + 4, y + 46, "About", black, 2);
    home_footer(fb, "", "PLAY  select", chrome);
}
} }

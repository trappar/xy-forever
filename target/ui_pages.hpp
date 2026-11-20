#ifndef XYF_UI_PAGES_HPP
#define XYF_UI_PAGES_HPP
#include <stdint.h>
// XY Forever pages beyond the main menu: firmware library list (scrolling),
// confirmation dialog, progress screen and the "no firmware" home screen.
// Same rules as ui_screens: full repaint, no stock calls, no division helpers.
namespace xyf { namespace ui {
static const uint32_t list_visible = 7;          // the compact title bar leaves room for 7 rows (was 5)
// What every page shows besides its own content. usb: a host is talking serial frames (the USB mark,
// top right); mtp (188): an MTP command within 5 s or a transfer running (the MTP mark, left of the USB
// mark's place); activity: what a host command, a job started over USB or MTP is doing ("USB: writing
// files", "MTP: transfer"; null: nothing), on the home page right-aligned under the marks, on the other pages
// in place of the hint line; version: the release version ("v0.1.0"), on the home page only; image: the
// running image ("image f458e3cc"), on the Info page. A null Chrome: none.
struct Chrome { const char* version; bool usb; const char* activity; bool mtp; const char* image; };
// The marks (188): "MTP" and "USB" in black on accent boxes, mark_w x mark_h each, mark_gap apart, the USB
// box's right edge at right, each at a fixed place; a mark that is off is not drawn (both are always
// available; lit only while active). marks_w: the width both take.
static const int mark_w = 23, mark_h = 11, mark_gap = 3, marks_w = 2 * mark_w + mark_gap;
void usb_mark(uint16_t* fb, int right, int y, bool usb, bool mtp);
// Every page but the home menu has a one-line title bar instead of the logo header: the title (2x),
// an optional subtitle after it (1x, grey, cut to fit) and the place of the USB mark on the right; it
// clears the screen. Content starts at compact_top.
static const int compact_bar = 24, compact_top = 30, compact_mark_y = 7;
void compact_header(uint16_t* fb, const char* title, const char* subtitle, const Chrome* chrome);
// How many subtitle characters fit after title in the title bar.
uint32_t subtitle_room(const char* title);
// The home pages' logo header (the logo, the marks' place, 188: the activity right-aligned under the
// marks at home_activity_y, the rule at home_bar) and footer: status as the line above the hint; the
// version right-aligned on the hint line.
static const int home_bar = 56, home_logo_x = 20, home_logo_y = 11, home_activity_y = home_logo_y + mark_h + 5;   // the logo's left edge = the list text's
void home_header(uint16_t* fb, const Chrome* chrome);
void home_footer(uint16_t* fb, const char* status, const char* hint, const Chrome* chrome);
// The bottom line (y): the activity in accent while there is one, else hint (grey; may be null).
void footer_line(uint16_t* fb, int y, const char* hint, const Chrome* chrome);
struct ListRow { const char* title; const char* detail; bool marked; };   // marked = default (large: the detail in accent)
// large (#21: the Settings page, an option list): the detail at the title's size (2x, as the home menu's rows),
// and a marked row's detail in accent instead of "(default)".
struct List {
    const char* heading;
    const ListRow* rows;
    uint32_t count, selected;
    const char* hint;
    const Chrome* chrome;
    bool large;
};
struct Confirm {
    const char* heading;      // e.g. "Uninstall XY Forever?"
    const char* lines[4];
    uint32_t count;
    const char* cancel;       // left choice
    const char* accept;       // right choice
    bool accept_selected;
    const Chrome* chrome;
};
struct Progress {
    const char* heading;      // e.g. "Installing OP-XY 1.2.5"
    const char* step;         // e.g. "Keyboard firmware"
    uint32_t done, total;     // bar = done / total (total 0 -> empty bar)
    const char* warning;      // e.g. "Do not power off"
    const Chrome* chrome;
};
// A dialog of up to three buttons in a row (the Save dialog); buttons[i] 0: not shown.
struct Choice {
    const char* heading;
    const char* lines[4];
    uint32_t count;
    const char* buttons[3];
    uint32_t selected;
    const Chrome* chrome;
};
void draw_list(uint16_t* fb, const List& list);
void draw_choice(uint16_t* fb, const Choice& choice);
void draw_confirm(uint16_t* fb, const Confirm& confirm);
void draw_progress(uint16_t* fb, const Progress& progress);
// No bootable firmware anywhere: explain, and the one row (About, selected).
void draw_home_empty(uint16_t* fb, const Chrome* chrome);
// First visible row for a scrolling list (keeps the selection on screen).
uint32_t list_first(uint32_t count, uint32_t selected);
// floor(a * b / c) without a hardware/library divide; c == 0 -> 0.
uint32_t scale(uint32_t a, uint32_t b, uint32_t c);
} }
#endif

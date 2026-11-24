#ifndef XYF_SAVER_HPP
#define XYF_SAVER_HPP
#include <stdint.h>
#include "ui_gfx.hpp"
#include "xyf_menu.hpp"
// #315: the menu's screen saver (processor b: key_ui; the simulator drives the same). Header only, pure: times are
// the caller's millisecond clock (wrap-safe differences), no stock calls, no division.
// After timeout_ms (settings.txt saver=, xyf_settings.hpp saver_ms; 0: off) with no key, knob or USB activity, the
// screen gives way to the infinity mark on black, moving diagonally step px every frame_ms (about 30 frames a second)
// and bouncing off the edges, its colour changing at each bounce. Any key press or knob turn ends it and does nothing
// else: that press, and its key's release, go nowhere (PLAY never boots from it); the menu then shows its current
// state. A question or an error on screen (holds) never times out into it and ends it. USB work (activity: key_ui's
// host_activity, a job or a request that changes something, MTP traffic) counts as activity; it and a build (still)
// hold the mark still: no frame takes time from them (#288: a frame's stock transfer blocks the menu loop ~15 ms).
// Under the saver the menu's other redraws wait for the wake (key_ui: one full redraw then). A frame after the first
// redraws only the mark's old and new boxes (draw: the columns it touched, for the cache flush).
namespace xyf { namespace saver {
static const uint32_t frame_ms = 33, none = 0xffffffffu;
static const int outer = 26, inner = 20, offset = 2 * outer - 9, step = 1;   // the logo's mark, 1.5x
static const int mark_w = 2 * outer + offset + 1, mark_h = 2 * outer + 1;  // gfx::mark_width
static const int max_x = gfx::width - mark_w, max_y = gfx::height - mark_h;
static const uint32_t color_count = 5;
static const gfx::Color colors[color_count] = {gfx::accent, gfx::white, 0x867d, 0x9fd3, 0xfb56};
struct State {
    uint32_t on;      // the mark has the screen
    uint32_t since;   // the last activity
    uint32_t moved;   // the last frame
    uint32_t woke;    // the key whose press ended it: its release goes nowhere too (none: no such key)
    int32_t x, y, dx, dy;
    uint32_t color;   // colors[color]
    // What the framebuffer holds of it (draw): drawn 0: nothing yet (the next frame is full); the mark's box there.
    uint32_t drawn;
    int32_t drawn_x, drawn_y;
    const uint16_t* drawn_fb;
};
// The columns a frame touched, from..to (inclusive).
struct Span {
    int from, to;
};
// The menu opened: off, idle from now, the mark in the middle (it continues from where it was at each start).
inline void start(State& s, uint32_t now) {
    s.on = 0;
    s.since = s.moved = now;
    s.woke = none;
    s.x = max_x / 2;
    s.y = max_y / 2;
    s.dx = s.dy = step;
    s.color = 0;
    s.drawn = 0;
    s.drawn_x = s.drawn_y = 0;
    s.drawn_fb = 0;
}
// A key event, or a knob turn (key none, pressed). True: it goes nowhere else (it ended the saver, or it is the
// release of the key that did).
inline bool input(State& s, uint32_t key, bool pressed, uint32_t now) {
    s.since = now;
    if (s.on && pressed) {
        s.on = 0;
        s.woke = key;
        return true;
    }
    if (pressed || key == none || key != s.woke) return false;
    s.woke = none;
    return true;
}
// One frame's move: a bounce turns at the edge and changes the colour.
inline void move(State& s) {
    bool bounced = false;
    if (s.x + s.dx < 0 || s.x + s.dx > max_x) {
        s.dx = -s.dx;
        bounced = true;
    }
    if (s.y + s.dy < 0 || s.y + s.dy > max_y) {
        s.dy = -s.dy;
        bounced = true;
    }
    s.x += s.dx;
    s.y += s.dy;
    if (bounced) s.color = s.color + 1 == color_count ? 0 : s.color + 1;
}
// Each menu pass. activity: USB work (idle starts again); still: no frame now (USB work, a build). True: the screen
// changes (it started, the mark moved, or a hold or off ended it). Frames keep their average rate (a late one does not
// delay the next) unless more than one is missed.
inline bool pass(State& s, uint32_t now, uint32_t timeout_ms, bool hold, bool activity, bool still) {
    if (hold || !timeout_ms || activity) s.since = now;
    if (hold || !timeout_ms) {
        if (!s.on) return false;
        s.on = 0;
        return true;
    }
    if (!s.on) {
        if (now - s.since < timeout_ms) return false;
        s.on = 1;
        s.moved = now;
        s.drawn = 0; // the menu was on screen: the first frame is full
        return true;
    }
    if (still || now - s.moved < frame_ms) return false;
    s.moved = now - s.moved < 2 * frame_ms ? s.moved + frame_ms : now;
    move(s);
    return true;
}
// The screens that stay (an error or a question): the #65 question (asked), a content pack's confirm dialog, storage
// unavailable (the main menu says how out).
inline bool holds(const menu::State& m, bool asked) {
#if defined(XYF_SETTING_OPTIONS)
    if (m.screen == menu::Screen::packs && m.pack_confirm) return true;
#endif
    return asked || m.storage == menu::storage_unavailable;
}
// The frame: black but the mark. full (or the first frame, or another framebuffer): the whole screen; else only the
// mark's old box is cleared before it is drawn at its new place. Returns the columns touched.
inline Span draw(uint16_t* fb, State& s, bool full) {
    full = full || !s.drawn || s.drawn_fb != fb;
    Span span = {0, gfx::width - 1};
    if (full) gfx::clear(fb, gfx::black);
    else {
        gfx::fill(fb, s.drawn_x, s.drawn_y, mark_w, mark_h, gfx::black);
        span.from = s.drawn_x < s.x ? s.drawn_x : s.x;
        span.to = (s.drawn_x > s.x ? s.drawn_x : s.x) + mark_w - 1;
    }
    gfx::mark(fb, s.x, s.y, outer, inner, offset, colors[s.color < color_count ? s.color : 0]);
    s.drawn = 1;
    s.drawn_x = s.x;
    s.drawn_y = s.y;
    s.drawn_fb = fb;
    return span;
}
}}
#endif

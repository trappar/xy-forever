#ifndef XYF_WHERE_HPP
#define XYF_WHERE_HPP
#include <stdint.h>
#include "ui_gfx.hpp"
// B's where codes (#114; the list: docs/hardware/devices.md, "Where codes"). Each blocking wait on B names
// itself in one word while it runs (key_ui::Where); a wait past paint_ms while the menu has the screen
// paints its label ("W3") in the screen's top-right corner from the wait's own loop (key_ui_where.ipp),
// and the crash screen names the last one. Freestanding and host-tested (tests/test_crash_screen.py).
namespace xyf { namespace where {
enum Code : uint32_t {
    file_answer = 1,    // file_client await_answer: A's answer to a file request
    link_settle = 2,    // file_boot b_settle: the outstanding echo, before B's next request
    command = 3,        // file_boot b_ask: A's answer to a command item
    check_blob = 4,     // file_boot b_check: A's check status blob
    catalog_blob = 5,   // file_boot: the catalog blob (b_fetch_catalog, b_fetch_step, the menu without checks)
    stage2_blob = 6,    // file_boot b_load_stage2 (the stage-2 test modes, built without where codes: reserved)
    handshake = 7,      // file_boot role1_menu_boot: the link handshake
    catalog_first = 8,  // xyf_boot fetch_wait: a link user waits for the catalog request in flight
    link_close = 9,     // xyf_boot link_close: the open request, before B resets
    boot_image = 10,    // file_boot role1_menu_boot: the boot image transfer
    usb_reply = 11,     // usb_service send: a reply into the CDC
    usb_log = 12,       // usb_service raw_send: an install log line (never paints: 200 ms bound, in the stock writer)
};
static const uint32_t paint_ms = 500;
// The word: bits 0-15 the wait B is in now (0: none); bits 16-31 the last wait entered (kept after it ended).
inline uint32_t entered(uint32_t code) { return code | code << 16; }
inline uint32_t current(uint32_t word) { return word & 0xffffu; }
inline uint32_t last(uint32_t word) { return word >> 16; }
// Inlined (as crash_screen.hpp's put): "W" and the code in decimal (1..99; larger codes show 99) at out[at..];
// cap bytes from at on, NUL included (4 suffice). No division (no libgcc call). Returns the new end.
inline __attribute__((always_inline)) uint32_t label(char* out, uint32_t at, uint32_t cap, uint32_t code) {
    if (cap < 4) {
        if (cap) out[at] = 0;
        return at;
    }
    uint32_t tens = 0;
    if (code > 99) code = 99;
    while (code >= 10) {
        code -= 10;
        ++tens;
    }
    out[at++] = 'W';
    if (tens) out[at++] = char('0' + tens);
    out[at++] = char('0' + code);
    out[at] = 0;
    return at;
}
// The corner label over a drawn screen (key_ui present; ui_preview): black on the accent colour, top right.
inline void draw(uint16_t* fb, uint32_t code) {
    char text[4];
    label(text, 0, sizeof(text), code);
    const int w = gfx::text_width(text, 1) + 4;
    gfx::fill(fb, gfx::width - w, 0, w, 11, gfx::accent);
    gfx::text(fb, gfx::width - w + 2, 2, text, gfx::black, 1);
}
}}
#endif

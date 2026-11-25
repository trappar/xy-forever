#ifndef XYF_KEY_UI_HPP
#define XYF_KEY_UI_HPP
#include <stdint.h>
#include "where.hpp"
// Hold-key UI (processor b only; Test018 onwards). Reads the stock bootloader's
// boot-time key snapshot (PSoC request 0x41 reply, frame type 0x42, 10-byte key
// bitmap; bit i = key enum i, KPlay = 51). When the menu opens (PLAY held, or
// asked for by the host or a failed check): draws the XY Forever screens,
// re-opens the stock PSoC link, feeds key events to the menu, and closes the
// link again with the stock boot teardown before the boot continues.
// Otherwise (or on processor a) it makes no stock calls.
namespace xy_forever { namespace key_ui {
static const uint32_t magic = 0x3149554bu;     // "KUI1"
static const uint32_t max_events = 20;           // 24 in versions 1-2
enum Exit : uint32_t {
    exit_none = 0,
    exit_play = 1,
    exit_timeout = 2,
    exit_link = 3,
    exit_identity = 4,
    exit_role = 5,
    exit_no_play = 6,
    exit_snapshot = 7
};
// Stage bits, set in order reached.
enum Stage : uint32_t {
    stage_snapshot = 1,
    stage_play = 2,
    stage_identity = 4,
    stage_display = 8,
    stage_displayed = 16,
    stage_link = 32,
    stage_greeted = 64,
    stage_closed = 128
};
struct Result {
    uint32_t magic, version, role, stages;
    uint32_t frame_header;          // bytes 0x8807ce7e..81 (expect type 0x42, length 0x0a)
    uint32_t bitmap[3];             // bytes 0x8807ce82..8d (10-byte key bitmap + 2)
    uint32_t play_snapshot, identity_mismatch, framebuffer, start_cycles;
    uint32_t display_ms, link_ms, link_state, polls;
    uint32_t events, exit_reason, end_ms, heap_brk_before;
    uint32_t heap_brk_after, heap_results, last_event, stack_used;
    // Version 3: synchronous display present (no vsync interrupt).
    uint32_t dma_stat_entry;        // display DMA status (0x200a3030) before the first present
    uint32_t display_entry;         // smc_dma_active | frame mailbox flag << 8, at entry
    uint32_t presents;              // transfers started
    uint32_t present_skips;         // DMA not idle in time | stale busy cleared << 16
    uint32_t event[max_events];     // key | pressed << 8 | ms << 16
};
static_assert(sizeof(Result) == 0xc0, "Result layout");
extern Result result;
// #288: how long the menu's frames take, for the host (diag.status block FRM1). Times are in units of 1024 CPU
// cycles (2.56 us at 400 MHz); a min is 0 until its first sample. present() is synchronous end to end: it
// waits for any DMA still running, repaints all 480x216 RGB565 pixels, then the stock transfer (889efa6a with 1)
// sends the frame and returns only once the display DMA is idle again (its loop at 889efc12).
struct Frames {
    uint32_t magic, version, presents;                     // presents: frames measured (skipped ones are not)
    uint32_t draw_last, draw_min, draw_max;                // the repaint: draw_screen, where code, cache flush
    uint32_t transfer_last, transfer_min, transfer_max;   // the stock transfer: the frame's DMA, start to idle
    uint32_t present_last, present_min, present_max;      // the whole present(): wait + draw + transfer
    uint32_t wait_last, wait_max;                          // of that: the wait for a DMA still running at entry
};
static_assert(sizeof(Frames) == 14 * 4, "Frames layout");
static const uint32_t frames_magic = 0x314d5246u;          // "FRM1" (version 2)
extern Frames frames;
// The exception vector the stock code left, back in place if the menu had put the crash screen's there
// (key_ui_crash.ipp); a no-op otherwise (and on A). usb_service::reset_self calls it before B resets.
void crash_disarm();
// #314: the dead man's timer (key_ui_crash.ipp) stands still while on: a long stock call that runs interrupts of its
// own (usb_service: the stock boot writer).
void hang_hold(bool on);
// #314: the stop screen's log (diag.status block STL1): each time the dead man's timer found that B's menu loop had
// not moved for crash_screen.hpp stop_ms, while the menu loop runs. pc: the interrupted instruction (RETI);
// rets, fp: the registers there; state: IPEND (low 16 bits) | the ms since the loop last moved << 16 (0xffff: more);
// ms: the menu's clock. Entry i of count ever logged is in slot i % stall_entries. timer: stall_armed since the menu
// has the screen; stall_proven once the timer's proof interrupt came (without it nothing is ever logged);
// stall_off when it interrupted a loop that moved and was turned off.
static const uint32_t stall_magic = 0x314c5453u;          // "STL1" (version 2)
static const uint32_t stall_entries = 16;                 // a power of two
struct StallEntry {
    uint32_t ms, pc, rets, fp, state;
};
static const uint32_t stall_armed = 1, stall_proven = 2, stall_off = 4;
struct StallLog {
    uint32_t magic, version, count, timer;
    StallEntry entry[stall_entries];
};
static_assert(sizeof(StallLog) == 16 + 20 * stall_entries, "StallLog layout");
extern StallLog stall_log;
// #114: B's where codes (where.hpp). A blocking wait on B holds a Where for its whole length (any return
// clears it) and calls wait() in its loop: past where::paint_ms while the menu has the screen, that paints the
// code in the top-right corner once (the menu's present, never from an interrupt); the end of a painted wait
// redraws without it. A nested wait puts the outer code back; the outer one counts again from there.
// Built with the USB service (the product builds); the narrow offline modes have no room (a Where does nothing).
#if defined(XYF_USB_SERVICE)
uint32_t where_enter(uint32_t code);     // returns the word before (for where_leave)
void where_wait();
void where_leave(uint32_t before);
// Inlined: an out-of-line destructor was called through a register (unresolved in the call audit).
class Where {
public:
    __attribute__((always_inline)) explicit Where(uint32_t code) : before_(where_enter(code)) {}
    __attribute__((always_inline)) ~Where() { where_leave(before_); }
    __attribute__((always_inline)) void wait() const { where_wait(); }

private:
    Where(const Where&);
    Where& operator=(const Where&);
    uint32_t before_;
};
#else
class Where {
public:
    explicit Where(uint32_t) {}
    void wait() const {}
};
#endif
#if defined(XYF_CRASH_TRIGGER)
// Test build xyf-crash-trigger only (#102): whether the crash vector is armed (the menu has the screen), and
// one chosen fault (1..4, key_ui_crash.ipp) when it is; returns otherwise. The simulator halts B instead.
bool crash_armed_now();
void crash_fault(uint32_t kind);
#endif
#if defined(XYF_SERVICE_SCREEN)
// Test 054: draw the USB service screen (b); false when the display code check failed.
bool service_screen(const char* a, const char* b, const char* c, const char* d);
void inject_key(uint32_t key, uint32_t pressed);   // Test 058
#if defined(XYF_ONE_SESSION)
void refresh_catalog();                 // return to the main menu and redraw the current default
// A USB frame arrived (usb_service home_pump): while the menu loop has the screen, redraw it at once when
// the USB mark or the activity line changed (before a long request such as INSTALL runs).
void host_view();
#endif
#endif
}}
// Pre-copy hook; always returns (never stops the boot).
extern "C" void xy_forever_key_ui();
#endif

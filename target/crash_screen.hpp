#ifndef XYF_CRASH_SCREEN_HPP
#define XYF_CRASH_SCREEN_HPP
#include <stdint.h>
#include "ui_screens.hpp"
#include "where.hpp"
// B's crash screen (key_ui_crash.ipp): what B's exception vector recorded, as the lines the screen shows.
// Freestanding and host-tested (tests/test_crash_screen.py); no stock calls.
namespace xyf { namespace crash {
// Written by the vector: taken = 1 once it ran; SEQSTAT (EXCAUSE in bits 0-5); RETX, the instruction
// that faulted; RETS, where the function that faulted was called from.
struct Record {
    uint32_t taken, seqstat, retx, rets;
};
static const uint32_t line_bytes = 64;      // 63 characters at most: the 1x font fits 74 across
struct Text {
    char cause[line_bytes], at[line_bytes], seqstat[line_bytes], where[line_bytes];
};

struct Cause {
    uint32_t code;
    const char* name;
};
// The Blackfin EXCAUSE values (a table, not a switch: no jump table in the call audit).
static const Cause causes[] = {
    {0x10, "single step"},
    {0x11, "trace buffer full"},
    {0x21, "undefined instruction"},
    {0x22, "illegal instruction combination"},
    {0x23, "data access protection violation"},
    {0x24, "misaligned data access"},
    {0x25, "unrecoverable event"},
    {0x26, "data access to unmapped memory"},
    {0x27, "data access matches two CPLBs"},
    {0x28, "emulation watchpoint"},
    {0x2a, "misaligned instruction fetch"},
    {0x2b, "instruction fetch protection violation"},
    {0x2c, "instruction fetch from unmapped memory"},
    {0x2d, "instruction fetch matches two CPLBs"},
    {0x2e, "supervisor resource in user mode"},
};
inline __attribute__((always_inline)) const char* cause_name(uint32_t excause) {
    for (uint32_t i = 0; i != sizeof(causes) / sizeof(causes[0]); ++i)
        if (causes[i].code == excause) return causes[i].name;
    return excause < 0x10 ? "software exception (EXCPT)" : "unknown exception";
}
// Inlined: a function address GCC keeps in a register for repeated calls is unresolved in the call audit.
// Appends to line from at (always NUL-terminated, cut at line_bytes - 1); returns the new end.
inline __attribute__((always_inline)) uint32_t put(char* line, uint32_t at, const char* text) {
    for (; *text && at + 1 < line_bytes; ++text) line[at++] = *text;
    line[at] = 0;
    return at;
}
inline __attribute__((always_inline)) uint32_t put_hex(char* line, uint32_t at, uint32_t value, uint32_t digits) {
    at = put(line, at, "0x");
    for (uint32_t i = digits; i-- && at + 1 < line_bytes;) line[at++] = "0123456789abcdef"[value >> (i * 4) & 15u];
    line[at] = 0;
    return at;
}
// where: B's where word at the fault (where.hpp): the wait it was in, else the last one it left (#114).
inline void describe(const Record& r, uint32_t where, Text& t) {
    const uint32_t cause = r.seqstat & 0x3fu;
    uint32_t n = put_hex(t.cause, put(t.cause, 0, "Cause "), cause, 2);
    put(t.cause, put(t.cause, n, ": "), cause_name(cause));
    n = put_hex(t.at, put(t.at, 0, "At "), r.retx, 8);
    put_hex(t.at, put(t.at, n, ", called from "), r.rets, 8);
    put_hex(t.seqstat, put(t.seqstat, 0, "SEQSTAT "), r.seqstat, 8);
    const uint32_t in = xyf::where::current(where), code = in ? in : xyf::where::last(where);
    n = put(t.where, 0, in ? "Waiting in " : code ? "Not waiting; last wait " : "No wait yet");
    if (code) xyf::where::label(t.where, n, line_bytes - n, code);
}
inline ui::Message message(const Text& t, const ui::Chrome* chrome) {
    const char* image = chrome && chrome->image ? chrome->image : "";   // whose build the addresses belong to
    const ui::Message m = {"Crashed",
                           {"XY Forever stopped on an exception (processor B).", t.cause, t.at, t.seqstat, t.where,
                            image, "", "Turn the OP-XY off and on again."},
                           8,
                           "The addresses map to this image's build (module.asm).",
                           chrome};
    return m;
}
// #314: B's loop stopped. While the menu loop runs, the core timer is a dead man's timer (key_ui_crash.ipp):
// every pass of the loop and every poll of a bounded wait winds it back, so it interrupts only once nothing has
// moved for stop_ms. Its handler records this and draws the stop screen. ipend: the levels active then (bit 15
// alone: the loop itself; 7..14: inside an interrupt handler); pc: the instruction it interrupted (RETI); rets, fp:
// the registers there; caller, caller2: [fp + 4] and [[fp] + 4], where that frame's function and the one above it
// were called from; text: the string [fp - 4] points to (a stock assert keeps its message there: 11a01f4c, LINK
// 0x14, R0 at [SP + 0x10]; it ends in the ROM's IDLE loop, 0803dfcc), "" when it is no string; crumb: the loop's
// last step (usb_service::crumb).
static const uint32_t stop_ms = 3000;
static const uint32_t text_bytes = 128;            // a stock assert's message, whole (its path cut on screen)
struct Hang {
    uint32_t ipend, pc, rets, fp, caller, caller2;
    char text[text_bytes], crumb[line_bytes];
};
struct HangText {
    char at[line_bytes], frame[line_bytes], text[line_bytes], where[line_bytes];
};
// A string at address p (printable ASCII up to its NUL, at most cap - 1 bytes kept) copied into out, else false.
// read: a byte at an address that may be read (the caller's map check).
template<class Read> inline bool copy_text(char* out, uint32_t cap, uint32_t p, Read read) {
    uint32_t n = 0, k = 0;
    out[0] = 0;
    for (; k != cap; ++k) {
        int c = -1;
        if (!read(p + k, c)) return false;
        if (!c) break;
        if (c < 32 || c > 126) return false;
        if (n + 1 < cap) out[n++] = char(c);
    }
    out[n] = 0;
    return k > 3;
}
inline void hang_describe(const Hang& h, uint32_t where, HangText& t) {
    uint32_t n = put_hex(t.at, put(t.at, 0, "IPEND "), h.ipend, 4);
    n = put_hex(t.at, put(t.at, n, ", at "), h.pc, 8);
    put_hex(t.at, put(t.at, n, ", RETS "), h.rets, 8);
    n = put_hex(t.frame, put(t.frame, 0, "Frame "), h.fp, 8);
    n = put_hex(t.frame, put(t.frame, n, ", callers "), h.caller, 8);
    put_hex(t.frame, put(t.frame, n, ", "), h.caller2, 8);
    const char* said = h.text;                       // a stock source path: its "../../../src/" left out (room)
    while (said[0] == '.' && said[1] == '.' && said[2] == '/') said += 3;
    if (said[0] == 's' && said[1] == 'r' && said[2] == 'c' && said[3] == '/') said += 4;
    put(t.text, put(t.text, 0, h.text[0] ? "Says: " : "No message at the frame"), said);
    const uint32_t in = xyf::where::current(where), code = in ? in : xyf::where::last(where);
    n = put(t.where, 0, in ? "Waiting in " : code ? "Not waiting; last wait " : "No wait yet");
    if (code) xyf::where::label(t.where, n, line_bytes - n, code);
}
inline ui::Message hang_message(const Hang& h, const HangText& t, const ui::Chrome* chrome) {
    const char* image = chrome && chrome->image ? chrome->image : "";
    const ui::Message m = {"Stopped",
                           {"XY Forever's loop stopped for 3 s (processor B).", t.at, t.frame, t.text, t.where, h.crumb,
                            image, "If it stays: turn the OP-XY off and on again."},
                           8,
                           "Please photograph this screen (#314).",
                           chrome};
    return m;
}
}}
#endif

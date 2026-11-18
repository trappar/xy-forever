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
struct Record { uint32_t taken, seqstat, retx, rets; };
static const uint32_t line_bytes = 64;      // 63 characters at most: the 1x font fits 74 across
struct Text { char cause[line_bytes], at[line_bytes], seqstat[line_bytes], where[line_bytes]; };

struct Cause { uint32_t code; const char* name; };
// The Blackfin EXCAUSE values (a table, not a switch: no jump table in the call audit).
static const Cause causes[] = {
    {0x10, "single step"}, {0x11, "trace buffer full"}, {0x21, "undefined instruction"},
    {0x22, "illegal instruction combination"}, {0x23, "data access protection violation"},
    {0x24, "misaligned data access"}, {0x25, "unrecoverable event"},
    {0x26, "data access to unmapped memory"}, {0x27, "data access matches two CPLBs"},
    {0x28, "emulation watchpoint"}, {0x2a, "misaligned instruction fetch"},
    {0x2b, "instruction fetch protection violation"}, {0x2c, "instruction fetch from unmapped memory"},
    {0x2d, "instruction fetch matches two CPLBs"}, {0x2e, "supervisor resource in user mode"},
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
    const ui::Message m = {"Crashed", {"XY Forever stopped on an exception (processor B).", t.cause, t.at, t.seqstat,
                                       t.where, image, "", "Turn the OP-XY off and on again."}, 8,
                           "The addresses map to this image's build (module.asm).", chrome};
    return m;
}
} }
#endif

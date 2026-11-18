#ifndef XYF_KNOB_INPUT_HPP
#define XYF_KNOB_INPUT_HPP
#include <stdint.h>
// Knob turns in B's menu. The PSoC reports turns as frames of
// type 0x45, which the stock poll receives into its receive queue and then drops. Every frame stays in its
// slot, so after each stock poll this reads the slots written since the last pass (plain memory reads; no
// stock call, nothing sent, nothing written to the queue) and turns the knobs' counts into list moves: one
// row per counts_per_row counts of all four knobs together, clockwise = down (as +). Measured (2026-11-14):
// idle indexes 25/25; slot = 41 <seq> 45 03 {id, int16 big-endian} (id 1..4 = knob 1..4, +1 clockwise).
// Anything that does not look like that is dropped and counted (result); nothing turns the reader off but
// the stock identity: start(false) keeps it off. Whether the indexes count on or wrap at 128 is unknown:
// both are read (slot = index % 128).
namespace xy_forever { namespace knob_input {
static const uint32_t magic = 0x31424e4bu;               // "KNB1" (diag.status block)
static const uint32_t queue = 0x88a522fcu;               // stock PSoC receive queue (ctx 0x88a522c8 + 0x34)
static const uint32_t slots = 128, slot_bytes = 0xfd;
static const uint32_t read_index = queue + 0x7e80u, write_index = queue + 0x7e84u;   // 32-bit words
static const uint32_t route = 0x41, frame_type = 0x45;   // bytes 0 and 2 of a knob frame
static const uint32_t max_length = 39;                   // byte 3: 3-byte records, at most 13
static const int32_t max_change = 32;                    // a record's change, either sign
static const uint32_t max_frames = 32;                   // slots read per pass (the rest next pass)
static const uint32_t window = slots - max_frames - 8;   // further behind the writer: resynchronise
static const uint32_t wrap_window = 16;                  // a step from 127 to 0..: the indexes wrap
static const int32_t counts_per_row = 2;                 // N: counts per list row: one click (2 counts; build 178)
static const int32_t max_moves = 4;                      // rows per pass; a spin's excess is dropped
static const uint32_t leftover_ms = 600;                 // counts below N are dropped after this long (a slow click)
enum State : uint32_t { state_off = 0, state_on = 1, state_identity = 2 };
struct Result {
    uint32_t magic, version, state;
    uint32_t frames;            // knob frames (type 0x45) accepted
    uint32_t dropped;           // knob frames refused (byte 0, length, a change out of range)
    uint32_t resyncs;           // index steps ignored (backwards, too far, read and write too far apart)
    uint32_t skipped;           // slots those steps passed over unread (at most 0xffff per step)
    uint32_t moves;             // list rows moved (either way)
    uint32_t idle;              // counts dropped: no selection on screen, leftovers timed out, a spin's excess
    uint32_t read_index, write_index;   // as last read
    uint32_t last[2];           // bytes 0..7 of the last knob frame (accepted or refused), little endian
    int32_t knob[4];            // counts per knob (ids 1..4) of the accepted frames
    uint32_t pass_read;         // slots read in the last pass that read any
    uint32_t max_read, max_moves;       // most slots read / rows moved in one pass
};
static_assert(sizeof(Result) == 0x50, "knob_input Result layout");
extern Result result;
// The menu opened. identity_ok: the stock identity the key path checked (key_ui's CRC spans) matched.
void start(bool identity_ok);
// One menu-loop pass, after the stock poll. selection: the page on screen moves a selection with - / +
// (not the Uninstall dialog, not a job's screen); otherwise the counts are dropped. Returns the rows to
// move, -max_moves..max_moves (positive: down, as +).
int32_t pass(uint32_t now_ms, bool selection);
} }
#endif

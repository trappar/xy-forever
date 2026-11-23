#include "knob_input.hpp"
// Knob turns (knob_input.hpp). Reads only: the two index words and the slots between them.
namespace xy_forever { namespace knob_input {
// Initialized data, not BSS: the cold link has no zero-initialization.
Result result __attribute__((section(".data"))) = {magic};
InputLog input_log __attribute__((section(".data"))) = {log_magic, 1};

void log_key(uint32_t key, bool pressed, bool injected, uint32_t ms) {
    InputLog& l = input_log;
    Event& e = l.event[l.count++ & (log_events - 1)];
    e.ms = ms;
    e.what = (pressed ? kind_press : kind_release) | (injected ? what_injected : 0u) | (key & 0xffu) << 8;
}
}}

namespace {
using namespace xy_forever::knob_input;
uint32_t cursor __attribute__((section(".data"))) = 0;           // the next slot index to read
int32_t pending __attribute__((section(".data"))) = 0;           // counts not yet a row
uint32_t pending_ms __attribute__((section(".data"))) = 0;       // when counts last arrived

uint32_t byte(uint32_t address) { return *reinterpret_cast<const volatile uint8_t*>(address); }
uint32_t word(uint32_t address) { return *reinterpret_cast<const volatile uint32_t*>(address); }
uint32_t magnitude(int32_t v) { return v < 0 ? uint32_t(-v) : uint32_t(v); }

// Slots from index `from` up to `to`, if that is a plausible step: counting on (at most a queue's worth),
// or wrapped at 128 (both below 128 and a short step). Otherwise ~0.
uint32_t distance(uint32_t from, uint32_t to) {
    const uint32_t d = to - from;
    if (d <= slots) return d;
    if (from < slots && to < from && to + slots - from <= wrap_window) return to + slots - from;
    return 0xffffffffu;
}

// An implausible step: the stock poll just ran, so read and write are never this far apart; the cursor is
// behind by more than the slots still unwritten (they may be overwritten), ahead of the writer, or the
// indexes went back (a restart). The slots in between are not read; the next pass starts at the writer.
void resync(uint32_t to, uint32_t step) {
    Result& r = result;
    ++r.resyncs;
    r.skipped += step > 0xffffu ? 0xffffu : step;
    cursor = to;
}

void log_turn(uint32_t id, int32_t change, uint32_t ms) {
    InputLog& l = input_log;
    Event& e = l.event[l.count++ & (log_events - 1)];
    e.ms = ms;
    e.what = kind_turn | (id & 0xffu) << 8 | uint32_t(uint16_t(change)) << 16;
}

// One slot: the change it adds (0 for any other frame type, or a refused knob frame).
int32_t frame(uint32_t a, uint32_t now_ms) {
    Result& r = result;
    const uint32_t first = byte(a), type = byte(a + 2), length = byte(a + 3);
    if (type != frame_type) return 0;                    // a key frame, a reply, a log: not ours
    r.last[0] = first | byte(a + 1) << 8 | type << 16 | length << 24;
    r.last[1] = byte(a + 4) | byte(a + 5) << 8 | byte(a + 6) << 16 | byte(a + 7) << 24;
    uint32_t rest = length;
    while (rest >= 3) rest -= 3;                         // length % 3 (no division)
    bool ok = first == route && length && length <= max_length && !rest;
    for (uint32_t at = a + 4; ok && at != a + 4 + length; at += 3) {
        const uint32_t id = byte(at);
        const int32_t change = int16_t(uint16_t(byte(at + 1) << 8 | byte(at + 2)));
        if (id - 1u < 4u && (change > max_change || change < -max_change)) ok = false;
    }
    if (!ok) {
        ++r.dropped;
        return 0;
    }
    int32_t total = 0;
    for (uint32_t at = a + 4; at != a + 4 + length; at += 3) {
        const uint32_t id = byte(at);
        const int32_t change = int16_t(uint16_t(byte(at + 1) << 8 | byte(at + 2)));
        log_turn(id, change, now_ms);                    // every id: the log names what moved
        if (id - 1u >= 4u) continue;                     // other ids (5..13: other controls): ignored
        r.knob[id - 1] += change;
        total += change;
    }
    ++r.frames;
    return total;
}
}

namespace xy_forever { namespace knob_input {
void start(bool identity_ok) {
    Result& r = result;
    for (uint32_t* p = &r.state; p != reinterpret_cast<uint32_t*>(&r + 1); ++p) *p = 0;
    r.version = 1;
    pending = 0;
    if (!identity_ok) {
        r.state = state_identity;
        return;
    }
    cursor = r.write_index = word(write_index);          // frames from before the menu are not turns
    r.read_index = word(read_index);
    r.state = state_on;
}

int32_t pass(uint32_t now_ms, bool selection) {
    Result& r = result;
    if (r.state != state_on) return 0;
    const uint32_t read = word(read_index), write = word(write_index);
    r.read_index = read;
    r.write_index = write;
    int32_t total = 0;
    uint32_t ahead = distance(cursor, write);
    if (distance(read, write) > slots || ahead > window) resync(write, write - cursor);   // see resync()
    else if (ahead) {
        const uint32_t n = ahead < max_frames ? ahead : max_frames;
        for (uint32_t i = 0; i != n; ++i) {
            total += frame(queue + (cursor & (slots - 1)) * slot_bytes, now_ms);
            ++cursor;
            if (write < slots && cursor >= slots) cursor -= slots;  // the writer wraps at 128
        }
        r.pass_read = n;
        if (n > r.max_read) r.max_read = n;
    }
    if (total) {
        pending += total;
        pending_ms = now_ms;
    }    // opposite counts cancel: a leftover never adds to a
                                                             // turn the other way (|pending| < N before this)
    if (pending && (!selection || now_ms - pending_ms > leftover_ms)) {
        r.idle += magnitude(pending);
        pending = 0;
    }
    int32_t moves = 0;
    while (pending >= counts_per_row && moves != max_moves) {
        pending -= counts_per_row;
        ++moves;
    }
    while (pending <= -counts_per_row && moves != -max_moves) {
        pending += counts_per_row;
        --moves;
    }
    if (pending >= counts_per_row || pending <= -counts_per_row) {
        r.idle += magnitude(pending);
        pending = 0;
    }
    const uint32_t rows = magnitude(moves);
    r.moves += rows;
    if (rows > r.max_moves) r.max_moves = rows;
    return moves;
}
}}

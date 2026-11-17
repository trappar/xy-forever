// Host test: set-default planning, slot alternation, id hash lookup, collector.
#include "xyf_state_write.hpp"
#include "xyf_crc32.hpp"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
using namespace xyf;
static unsigned checks;
static void require(bool ok, int line) { ++checks; if (!ok) { fprintf(stderr, "check %u (line %d) failed\n", checks, line); abort(); } }
#define REQUIRE(x) require((x), __LINE__)
static library::Library lib;
static state_write::Plan plan;
int main() {
    lib.count = 3;
    strcpy(lib.entries[0].id, "granular"); lib.entries[0].usable = true;
    strcpy(lib.entries[1].id, "noimage");   lib.entries[1].usable = false;
    strcpy(lib.entries[2].id, "opxy-121");  lib.entries[2].usable = true;
    // No state yet: first write goes to slot a with seq 1.
    REQUIRE(state_write::plan_default(lib, 0, plan));
    REQUIRE(!strcmp(plan.path, state_write::slot_a_path) && plan.seq == 1 && plan.length == strlen(plan.text));
    state::Slot slot;
    REQUIRE(state::parse_slot(plan.text, plan.length, slot) && slot.seq == 1 && !strcmp(slot.state.default_id, "granular"));
    // Current state from slot a (seq 1, with once and stage2 fields): next is slot b, seq 2, keeps stage2, clears once.
    lib.has_state = true; lib.state_source = state::source_a; lib.state_seq = 1;
    strcpy(lib.state.default_id, "granular"); strcpy(lib.state.once, "granular"); strcpy(lib.state.stage2, "xyf-1.ldr");
    REQUIRE(state_write::plan_default(lib, 2, plan));
    REQUIRE(!strcmp(plan.path, state_write::slot_b_path) && plan.seq == 2);
    REQUIRE(state::parse_slot(plan.text, plan.length, slot) && !strcmp(slot.state.default_id, "opxy-121") &&
            !slot.state.once[0] && !strcmp(slot.state.stage2, "xyf-1.ldr"));
    // Both slots compete correctly afterwards (b seq 2 beats a seq 1).
    state::Choice c = {};
    char a_text[128]; store::State a_state = {}; strcpy(a_state.default_id, "granular");
    const uint32_t na = state::write_slot(a_state, 1, a_text, sizeof(a_text));
    state::consider(c, state::source_a, a_text, na); state::consider(c, state::source_b, plan.text, plan.length);
    REQUIRE(c.source == state::source_b && !strcmp(c.state.default_id, "opxy-121"));
    // Legacy state.txt: next write is slot a, seq 1, keeps legacy fields.
    lib.state_source = state::source_legacy; lib.state_seq = 0;
    REQUIRE(state_write::plan_default(lib, 0, plan) && !strcmp(plan.path, state_write::slot_a_path) && plan.seq == 1);
    // Refusals: unusable entry, out of range, invalid id.
    REQUIRE(!state_write::plan_default(lib, 1, plan) && !plan.path);
    REQUIRE(!state_write::plan_default(lib, 3, plan));
    strcpy(lib.entries[0].id, "Bad");
    REQUIRE(!state_write::plan_default(lib, 0, plan));
    strcpy(lib.entries[0].id, "granular");
    // Hash lookup: unique usable match only.
    const uint32_t h = state_write::id_hash("opxy-121");
    REQUIRE(h == crc::update(0, reinterpret_cast<const uint8_t*>("opxy-121"), 8));
    REQUIRE(state_write::find_by_hash(lib, h) == 2);
    REQUIRE(state_write::find_by_hash(lib, state_write::id_hash("noimage")) == -1);   // not usable
    REQUIRE(state_write::find_by_hash(lib, 0x12345678u) == -1);
    lib.count = 4; strcpy(lib.entries[3].id, "opxy-121"); lib.entries[3].usable = true;  // duplicate (never from a scan)
    REQUIRE(state_write::find_by_hash(lib, h) == -2);
    lib.count = 3;
    // Collector: all eight nibbles, any order, then commit; incomplete commit refused and resets.
    // Every command value keeps its low three bits 0 (a's bootloader's select: the user area).
    state_write::Collector col = {0, 0};
    const uint32_t order[8] = {2u, 0u, 7u, 3u, 5u, 1u, 6u, 4u};
    for (uint32_t k = 0; k != 8; ++k) REQUIRE((state_write::id_nibble_command(0xffffffffu, k) & 7u) == 0);
    for (uint32_t k : order) REQUIRE(!state_write::collect(col, state_write::id_nibble_command(h, k)));
    const uint32_t commit = 0x434du << 16 | state_write::item_set_default_by_id << 8 | 7;
    REQUIRE(state_write::collect(col, commit) && col.hash == h);
    REQUIRE(!state_write::collect(col, commit));                                   // mask reset
    REQUIRE(!state_write::collect(col, state_write::id_nibble_command(h, 0)) && !state_write::collect(col, commit));
    for (uint32_t k = 0; k != 7; ++k) REQUIRE(!state_write::collect(col, state_write::id_nibble_command(h, k)));
    REQUIRE(!state_write::collect(col, 0x434du << 16 | (state_write::item_id_nibble + 7) << 8 | 1u));   // low bits set: ignored
    REQUIRE(!state_write::collect(col, commit));
    REQUIRE(!state_write::collect(col, 0x4b41u << 16 | 0x60u << 8));               // keepalive tag ignored
    printf("%u checks passed\n", checks);
    return 0;
}

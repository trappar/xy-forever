// Host test: A/B state slots (power-safe state.txt without rename).
#include "xyf_state.hpp"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
using namespace xyf;
static unsigned checks;
static void require(bool ok, int line) { ++checks; if (!ok) { fprintf(stderr, "check %u (line %d) failed\n", checks, line); abort(); } }
#define REQUIRE(x) require((x), __LINE__)
int main() {
    store::State s = {}; strcpy(s.default_id, "opxy-121"); strcpy(s.once, "granular");
    char a[512], b[512];
    const uint32_t na = state::write_slot(s, 7, a, sizeof(a));
    REQUIRE(na && !strncmp(a, "default=opxy-121\nonce=granular\nseq=7\ncrc32=", 43) && a[na - 1] == '\n');
    state::Slot slot;
    REQUIRE(state::parse_slot(a, na, slot) && slot.seq == 7 && !strcmp(slot.state.default_id, "opxy-121"));
    for (uint32_t i = 0; i != na; ++i) { a[i] ^= 1; REQUIRE(!state::parse_slot(a, na, slot)); a[i] ^= 1; }  // any flip breaks it
    REQUIRE(!state::parse_slot(a, na - 3, slot));                                           // truncated (power loss)
    strcpy(s.default_id, "opxy-125"); s.once[0] = 0;
    const uint32_t nb = state::write_slot(s, 8, b, sizeof(b));
    state::Choice c = {};
    state::consider(c, state::source_a, a, na); state::consider(c, state::source_b, b, nb);
    state::consider(c, state::source_legacy, "default=legacy\n", 15);
    REQUIRE(c.source == state::source_b && c.seq == 8 && !strcmp(c.state.default_id, "opxy-125"));
    state::Source target; uint32_t seq;
    state::next_write(c, target, seq); REQUIRE(target == state::source_a && seq == 9);
    // b torn mid-write: a still wins; next write replaces b.
    state::Choice d = {};
    state::consider(d, state::source_a, a, na); state::consider(d, state::source_b, b, nb - 5);
    REQUIRE(d.source == state::source_a && !strcmp(d.state.default_id, "opxy-121"));
    state::next_write(d, target, seq); REQUIRE(target == state::source_b && seq == 8);
    // No slots: legacy state.txt; next write goes to a with seq 1.
    state::Choice e = {};
    state::consider(e, state::source_legacy, "default=legacy\n", 15);
    REQUIRE(e.source == state::source_legacy && !strcmp(e.state.default_id, "legacy"));
    state::next_write(e, target, seq); REQUIRE(target == state::source_a && seq == 1);
    store::State empty = {};
    const uint32_t ne = state::write_slot(empty, 3, b, sizeof(b));
    REQUIRE(ne && state::parse_slot(b, ne, slot) && slot.seq == 3 && !slot.state.default_id[0]);
    REQUIRE(state::write_slot(s, 1, b, 20) == 0);
    printf("%u checks passed\n", checks);
    return 0;
}

// Host test of xyf_settings.hpp (tests/test_settings.py).
#include "xyf_settings.hpp"
#include <cstdio>
#include <cstring>
static int checks = 0, failures = 0;
#define CHECK(c) do { ++checks; if (!(c)) { ++failures; std::printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); } } while (0)
using namespace xyf::settings;
static Record parsed(const char* t) { Record r; parse(t, uint32_t(std::strlen(t)), r); return r; }
int main() {
    Record r = parsed("");                                         // no record: the product defaults
    CHECK(r.confirm == 1);
    r = parsed("format=1\nconfirm=0\nknob=reversed\n");             // a record with the old knob= loads
    CHECK(r.confirm == 0);
    r = parsed("confirm=00\n");                                     // only exact values count
    CHECK(r.confirm == 1);
    r = parsed("other=1\nconfirm=0");                              // unknown keys, no final LF
    CHECK(r.confirm == 0);
    char out[64];
    Record w; w.confirm = 0;
    const uint32_t n = format(out, sizeof(out), w);
    CHECK(n && std::memcmp(out, "format=1\nconfirm=0\n", n) == 0);
    Record back; parse(out, n, back);
    CHECK(back.confirm == 0);                                        // round trip (a restart reads it back)
    CHECK(format(out, 10, w) == 0);                                  // does not fit
    CHECK(rows == 1 && std::strcmp(label(row_memory), "Mod memory") == 0);   // only settings that work
    CHECK(std::strcmp(memory_text(memory_standard, false), "Standard") == 0);
    CHECK(std::strcmp(memory_text(memory_expanded, false), "4 MiB (experimental)") == 0);
    CHECK(std::strcmp(memory_text(memory_expanded, true), "4 MiB (experimental): applied when you boot") == 0);
    CHECK(std::strcmp(memory_text(memory_standard, true), "Standard: applied when you boot") == 0);
    CHECK(std::strcmp(memory_text(memory_unavailable, false), "Not available with these mod packages") == 0);
    Gate g; gate_clear(g); CHECK(gate_poll(g, 0) == idle);
    gate_open(g, "boot", 1000);
    gate_key(g, key_play, 1, true);                                  // injected: ignored
    CHECK(gate_poll(g, 2000) == pending);
    gate_key(g, key_play, 0, false);                                 // a release: ignored
    CHECK(gate_poll(g, 2000) == pending);
    gate_key(g, key_play, 1, false); CHECK(gate_poll(g, 2000) == yes);
    gate_open(g, "boot", 1000); gate_key(g, key_stop, 1, true); gate_key(g, key_stop, 1, false); CHECK(gate_poll(g, 1001) == no);
    gate_open(g, "boot", 0xfffff000u); CHECK(gate_poll(g, 0x1000u) == pending);   // wrap-safe
    CHECK(gate_poll(g, 0xfffff000u + timeout_ms) == timed_out);
    gate_key(g, key_play, 1, false); CHECK(gate_poll(g, 0) == timed_out);          // too late
    std::printf("%d checks passed\n", checks - failures);
    return failures ? 1 : 0;
}

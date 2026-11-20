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
    CHECK(r.confirm == 1 && r.power_on == power_play && r.usb == usb_mtp_serial && !r.newer);
    r = parsed("format=1\nconfirm=0\n");                           // #21: a record before power_on / usb: defaults
    CHECK(r.confirm == 0 && r.power_on == power_play && r.usb == usb_mtp_serial && !r.newer);
    r = parsed("format=1\npower_on=always\nusb=serial\n");
    CHECK(r.confirm == 1 && r.power_on == power_always && r.usb == usb_serial);
    r = parsed("power_on=wait\nusb=serial2\n");                    // only exact values count (no "wait": #21)
    CHECK(r.power_on == power_play && r.usb == usb_mtp_serial);
    CHECK(parsed("usb=mtp_only\n").usb == usb_mtp_only && parsed("usb=mtp_serial\n").usb == usb_mtp_serial);
    CHECK(parsed("format=1\nusb=mtp\n").usb == usb_mtp_serial);     // written before MTP alone: the composite
    CHECK(parsed("usb=MTP\n").usb == usb_mtp_serial && parsed("usb=mtp_only2\n").usb == usb_mtp_serial);
    r = parsed("format=2\nconfirm=0\npower_on=always\n");          // a newer record: the defaults, newer set
    CHECK(r.newer && r.confirm == 1 && r.power_on == power_play);
    r = parsed("format=12\n"); CHECK(r.newer);
    r = parsed("format=0\npower_on=always\n");                     // not a version this reads: the defaults
    CHECK(!r.newer && r.power_on == power_play);
    r = parsed("format=x\npower_on=always\n"); CHECK(!r.newer && r.power_on == power_play);
    r = parsed("format=9999999999\n"); CHECK(!r.newer);             // too long for a version: broken
    r = parsed("format=1\npower_on=always\n\xff\xfe=\x01garbage"); CHECK(r.power_on == power_always);
    r = parsed("format=1\nconfirm=0\nknob=reversed\n");             // a record with the old knob= loads
    CHECK(r.confirm == 0);
    r = parsed("confirm=00\n");                                     // only exact values count
    CHECK(r.confirm == 1);
    r = parsed("other=1\nconfirm=0");                              // unknown keys, no final LF
    CHECK(r.confirm == 0);
    char out[96];
    Record w; defaults(w); w.confirm = 0;
    uint32_t n = format(out, sizeof(out), w);
    CHECK(n == 48 && std::memcmp(out, "format=1\nconfirm=0\npower_on=play\nusb=mtp_serial\n", n) == 0);
    Record back; parse(out, n, back);
    CHECK(back.confirm == 0 && back.power_on == power_play && back.usb == usb_mtp_serial);   // round trip
    for (uint32_t p = 0; p != 2; ++p)
        for (uint32_t u = 0; u != usb_modes; ++u) {
            w.power_on = p; w.usb = u;
            n = format(out, sizeof(out), w);
            parse(out, n, back);
            CHECK(n && n <= max_text && back.power_on == p && back.usb == u && !back.newer);
        }
    CHECK(format(out, 10, w) == 0);                                  // does not fit
    // #21: the option lists, in value order (an option's index is the value it picks).
    CHECK(std::strcmp(power_on_options[power_play], "PLAY held") == 0 && std::strcmp(power_on_options[power_always], "Always") == 0);
    CHECK(usb_option_count == 3 && std::strcmp(usb_options[usb_mtp_serial], "MTP + Serial") == 0 &&
          std::strcmp(usb_options[usb_mtp_only], "MTP") == 0 && std::strcmp(usb_options[usb_serial], "Serial") == 0);
    CHECK(std::strcmp(memory_options[memory_standard - memory_standard], "2 MiB") == 0 &&
          std::strcmp(memory_options[memory_expanded - memory_standard], "4 MiB (experimental)") == 0);
    CHECK(std::strcmp(power_on_text(power_play), "PLAY held") == 0 && std::strcmp(power_on_text(power_always), "Always") == 0);
    CHECK(std::strcmp(usb_text(usb_mtp_serial), "MTP + Serial") == 0 && std::strcmp(usb_text(usb_serial), "Serial") == 0);
    CHECK(std::strcmp(usb_text(usb_mtp_only), "MTP (no web app)") == 0);
    CHECK(rows == 5 && std::strcmp(label(row_memory), "Mod memory") == 0 && std::strcmp(label(row_about), "About") == 0);
    CHECK(std::strcmp(label(row_confirm), "Confirm on device") == 0 && confirm_option_count == 2);   // #65
    CHECK(std::strcmp(confirm_options[0], "Off") == 0 && std::strcmp(confirm_options[1], "On") == 0);
    CHECK(std::strcmp(confirm_text(0), "Off") == 0 && std::strcmp(confirm_text(1), "On") == 0);
    CHECK(std::strcmp(memory_text(memory_standard, false), "2 MiB") == 0);
    CHECK(std::strcmp(memory_text(memory_expanded, false), "4 MiB (experimental)") == 0);
    CHECK(std::strcmp(memory_text(memory_expanded, true), "4 MiB (experimental): applied when you boot") == 0);
    CHECK(std::strcmp(memory_text(memory_standard, true), "2 MiB: applied when you boot") == 0);
    CHECK(std::strcmp(memory_text(memory_unavailable, false), "Not available") == 0);   // short: the page's 2x text
    CHECK(std::strcmp(memory_text(memory_reading, false), "Checking...") == 0);
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

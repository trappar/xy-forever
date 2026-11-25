// target/crash_screen.hpp's screens for tests/test_crash_screen.py, sanitized (hosttest/CMakeLists.txt).
#include "crash_screen.hpp"
#include <stdio.h>
int main() {
    const xyf::crash::Record records[] = {{1, 0x26, 0x8ab03d14, 0x8ab05a20},
                                          {1, 0x2021, 0x889eed60, 0x8ab02f00},
                                          {1, 0x03, 0x8ab00010, 0},
                                          {1, 0x3f, 0xffffffff, 0xffffffff}};
    // B's where word (#114): in W1; none now, the last W12; never; in W3 after W12 (the last half stays).
    const uint32_t wheres[] = {0x00010001, 0x000c0000, 0, 0x000c0003};
    for (unsigned i = 0; i != 4; ++i) {
        const xyf::crash::Record& r = records[i];
        xyf::crash::Text t;
        xyf::crash::describe(r, wheres[i], t);
        const xyf::ui::Chrome chrome = {"v0.1.0", false, nullptr, false, "image f458e3cc"};
        const xyf::ui::Message m = xyf::crash::message(t, &chrome);
        for (unsigned i = 0; i != m.count; ++i) printf("%s\n", m.lines[i]);
        printf("%s\n--\n", m.hint);
    }
    // #314: the hang screen: a stock assert's frame (its message readable) and one with no message.
    static const char assert_text[] = "../../../src/bf/spi_bridge_bfm.cpp:288 _this->currentRequest!=NULL";
    const xyf::crash::Hang hangs[2] = {{0x8810, 0x0803dfcc, 0x8bffef00, 0x11a002e0, {0}, {0}},
                                       {0x8810, 0x11a00500, 0x8bffef00, 0x11a002e0, {0}, {0}}};
    for (unsigned i = 0; i != 2; ++i) {
        xyf::crash::Hang h = hangs[i];
        const uint32_t base = 0x11906fc8u;
        if (!i && !xyf::crash::copy_text(h.text, sizeof(h.text), base, [&](uint32_t a, int& c) {
                if (a < base || a >= base + sizeof(assert_text)) return false;
                c = assert_text[a - base];
                return true;
            }))
            return 1;
        if (xyf::crash::copy_text(h.crumb, sizeof(h.crumb), 0, [](uint32_t, int&) { return false; }))
            return 2;   // unreadable
        xyf::crash::put(h.crumb, 0, "USB 0x02 #124349, job 1.1 93/121 MB, file 0x2a o0 3 ms ago");
        xyf::crash::HangText t;
        xyf::crash::hang_describe(h, i ? 0x00010001 : 0x00010000, t);
        const xyf::ui::Chrome chrome = {"v0.1.0", false, nullptr, false, "image f458e3cc"};
        const xyf::ui::Message m = xyf::crash::hang_message(h, t, &chrome);
        printf("%s\n", m.heading);
        for (unsigned k = 0; k != m.count; ++k) printf("%s\n", m.lines[k]);
        printf("%s\n--\n", m.hint);
    }
    return 0;
}

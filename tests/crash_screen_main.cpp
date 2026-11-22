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
    return 0;
}

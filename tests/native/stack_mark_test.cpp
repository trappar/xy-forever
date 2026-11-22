// target/stack_mark.hpp: B's stack high-water mark, as STATUS reports it.
#include "doctest.h"
#include "stack_mark.hpp"
using xy_forever::stack_mark::paint;
using xy_forever::stack_mark::used;

TEST_CASE("used counts from the deepest overwritten word") {
    volatile uint32_t s[64];
    for (int i = 0; i != 64; ++i) s[i] = paint;
    CHECK_MESSAGE(used(s, s + 64) == 0, "all painted");
    s[63] = 1; CHECK_MESSAGE(used(s, s + 64) == 4, "top word used");
    s[40] = 7; CHECK_MESSAGE(used(s, s + 64) == 96, "deepest touch counts, not the words above");
    s[0] = 0; CHECK_MESSAGE(used(s, s + 64) == 256, "touched at the floor: all used");
    CHECK_MESSAGE(used(s, s) == 0, "empty range");
}

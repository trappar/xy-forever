#include "stack_mark.hpp"
#include <stdio.h>
using xy_forever::stack_mark::paint;
using xy_forever::stack_mark::used;
static int failed = 0, checks = 0;
static void expect(uint32_t got, uint32_t want, const char* what) {
    ++checks; if (got != want) { ++failed; printf("FAIL %s: %u != %u\n", what, unsigned(got), unsigned(want)); }
}
int main() {
    volatile uint32_t s[64];
    for (int i = 0; i != 64; ++i) s[i] = paint;
    expect(used(s, s + 64), 0, "all painted");
    s[63] = 1; expect(used(s, s + 64), 4, "top word used");
    s[40] = 7; expect(used(s, s + 64), 96, "deepest touch counts, not the words above");
    s[0] = 0; expect(used(s, s + 64), 256, "touched at the floor: all used");
    expect(used(s, s), 0, "empty range");
    if (failed) return 1;
    printf("%d stack mark checks passed\n", checks);
    return 0;
}

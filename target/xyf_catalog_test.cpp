// Host test: catalog round trip, corruption rejection and menu row text.
#include "xyf_catalog.hpp"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
using namespace xyf;
static unsigned checks;
static void require(bool ok, int line) { ++checks; if (!ok) { fprintf(stderr, "check %u (line %d) failed\n", checks, line); abort(); } }
#define REQUIRE(x) require((x), __LINE__)
static library::Library lib;
static uint8_t buffer[catalog::max_bytes];
static catalog::Catalog cat;
int main() {
    lib.count = 3;
    strcpy(lib.entries[0].id, "granular"); strcpy(lib.entries[0].info.name, "open-xy: granular");
    lib.entries[0].info.kind = store::kind_open_xy; lib.entries[0].usable = true;
    lib.entries[0].has_layout = lib.entries[0].layout_ok = true; lib.entries[0].machines = 6;
    strcpy(lib.entries[1].id, "noimage"); strcpy(lib.entries[1].info.name, "noimage");
    strcpy(lib.entries[2].id, "opxy-121"); strcpy(lib.entries[2].info.name, "OP-XY 1.2.1");
    strcpy(lib.entries[2].info.version, "1.2.1+0"); lib.entries[2].info.kind = store::kind_official; lib.entries[2].usable = true;
    strcpy(lib.state.default_id, "opxy-121");

    REQUIRE(catalog::encode(lib, buffer, 10) == 0);
    const uint32_t n = catalog::encode(lib, buffer, sizeof(buffer));
    REQUIRE(n == catalog::header_bytes + 3 * sizeof(catalog::Item) + 4);
    REQUIRE(catalog::decode(buffer, n, cat) && cat.count == 3 && cat.default_index == 2);
    REQUIRE(!strcmp(cat.items[0].name, "open-xy: granular") && cat.items[0].machines == 6 && cat.items[0].flags == 7);
    REQUIRE(!strcmp(cat.items[2].version, "1.2.1+0") && cat.items[2].kind == store::kind_official);
    char text[24];
    catalog::describe(cat.items[0], text, sizeof(text)); REQUIRE(!strcmp(text, "open-xy, 6 machines"));
    catalog::describe(cat.items[1], text, sizeof(text)); REQUIRE(!strcmp(text, "missing os.ldr"));
    catalog::describe(cat.items[2], text, sizeof(text)); REQUIRE(!strcmp(text, "official"));
    cat.items[0].machines = 1; catalog::describe(cat.items[0], text, sizeof(text)); REQUIRE(!strcmp(text, "open-xy, 1 machine"));
    catalog::describe(cat.items[0], text, 5); REQUIRE(!strcmp(text, "open"));
    // The current build may select none or all 256; SDK number history is explicitly unknown.
    lib.entries[0].machines = 0;
    REQUIRE(catalog::decode(buffer, catalog::encode(lib, buffer, sizeof(buffer)), cat));
    catalog::describe(cat.items[0], text, sizeof(text)); REQUIRE(!strcmp(text, "open-xy, 0 machines"));
    lib.entries[0].numbers_only = true;
    REQUIRE(catalog::decode(buffer, catalog::encode(lib, buffer, sizeof(buffer)), cat));
    REQUIRE(cat.items[0].flags == (7 | catalog::flag_numbers_only));
    catalog::describe(cat.items[0], text, sizeof(text)); REQUIRE(!strcmp(text, "open-xy, numbers only"));
    lib.entries[0].numbers_only = false; lib.entries[0].machines = 256;
    REQUIRE(catalog::decode(buffer, catalog::encode(lib, buffer, sizeof(buffer)), cat) && cat.items[0].machines == 256);
    catalog::describe(cat.items[0], text, sizeof(text)); REQUIRE(!strcmp(text, "open-xy, 256 machines"));
    // An entry whose content is not installed (os.ldr there): flag 8, not usable, its own row text;
    // without an os.ldr it stays "missing os.ldr".
    lib.entries[0].usable = false; lib.entries[0].content_missing = lib.entries[0].image_present = true;
    lib.entries[1].content_missing = true;
    const uint32_t m = catalog::encode(lib, buffer, sizeof(buffer));
    REQUIRE(catalog::decode(buffer, m, cat) && cat.items[0].flags == (2 | 4 | catalog::flag_content_missing) &&
            cat.items[1].flags == 0);
    catalog::describe(cat.items[0], text, sizeof(text)); REQUIRE(!strcmp(text, "content not installed"));
    catalog::describe(cat.items[1], text, sizeof(text)); REQUIRE(!strcmp(text, "missing os.ldr"));
    lib.entries[0].usable = true; lib.entries[0].content_missing = false; lib.entries[1].content_missing = false;
    REQUIRE(catalog::encode(lib, buffer, sizeof(buffer)) == n);

    // Every single-byte corruption is rejected (CRC or structure).
    for (uint32_t i = 0; i != n; ++i) {
        buffer[i] ^= 0x41;
        REQUIRE(!catalog::decode(buffer, n, cat) && cat.count == 0);
        buffer[i] ^= 0x41;
    }
    REQUIRE(!catalog::decode(buffer, n - 1, cat) && !catalog::decode(buffer, n + 1, cat) && !catalog::decode(0, n, cat));
    // No default, empty library.
    lib.state.default_id[0] = 0; lib.count = 0;
    const uint32_t empty = catalog::encode(lib, buffer, sizeof(buffer));
    REQUIRE(empty == catalog::header_bytes + 4 && catalog::decode(buffer, empty, cat) && cat.count == 0 &&
            cat.default_index == 0xffffffffu);
    printf("%u checks passed\n", checks);
    return 0;
}

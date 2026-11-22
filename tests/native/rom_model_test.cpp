// target/boot_rom.hpp: the ROM reader context layout and the bounded source.
#include "doctest.h"
#include "boot_rom.hpp"
#include <string.h>

using namespace xy_forever::boot_rom;

namespace {
struct Memory { uint8_t bytes[8]; unsigned calls; };
static bool copy(void* user, uint32_t destination, const uint8_t* bytes, uint32_t count) {
    Memory& memory = *static_cast<Memory*>(user);
    if (destination > sizeof(memory.bytes) || count > sizeof(memory.bytes) - destination)
        return false;
    memcpy(memory.bytes + destination, bytes, count);
    ++memory.calls;
    return true;
}
}  // namespace

TEST_CASE("context layout and bounded source") {
    Context context;
    Auxiliary auxiliary;
    memset(&context, 0xa5, sizeof(context));
    memset(&auxiliary, 0xa5, sizeof(auxiliary));
    REQUIRE(prepare(context, auxiliary, 0x88000000, 0x88001000));
    for (size_t i = 0; i != context_size; ++i) {
        uint8_t expected = 0;
        if (i == reader_offset + 3) expected = 0x88;
        if (i == auxiliary_pointer_offset + 1) expected = 0x10;
        if (i == auxiliary_pointer_offset + 3) expected = 0x88;
        REQUIRE(context.bytes[i] == expected);
    }
    for (size_t i = 0; i != auxiliary_size; ++i) REQUIRE(auxiliary.bytes[i] == 0);
    REQUIRE(!prepare(context, auxiliary, 0, 0x88001000));
    REQUIRE(!prepare(context, auxiliary, 0x88000001, 0x88001000));
    REQUIRE(!prepare(context, auxiliary, 0x88000000, 0x88001001));
    const uint8_t source[] = {1, 2, 3, 4};
    Memory memory = {};
    ReadRequest request = {1, 2, 3};
    REQUIRE(read(request, source, sizeof(source), copy, &memory) == ReadResult::ok);
    REQUIRE((request.offset == 4 && memory.calls == 1));
    REQUIRE((memory.bytes[2] == 2 && memory.bytes[3] == 3 && memory.bytes[4] == 4));
    request = {4, 0, 1};
    REQUIRE(read(request, source, sizeof(source), copy, &memory) == ReadResult::source_bounds);
    REQUIRE((request.offset == 4 && memory.calls == 1));
    request = {0, 7, 2};
    REQUIRE(read(request, source, sizeof(source), copy, &memory) == ReadResult::copy_failed);
    REQUIRE((request.offset == 0 && memory.calls == 1));
    request = {0, UINT32_MAX, 2};
    REQUIRE(read(request, source, sizeof(source), copy, &memory) == ReadResult::destination_bounds);
    request = {4, 0, 0};
    REQUIRE(read(request, 0, sizeof(source), 0, 0) == ReadResult::ok);
}

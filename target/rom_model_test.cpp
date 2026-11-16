#include "boot_rom.hpp"
#include <stdlib.h>
#include <string.h>

using namespace xy_forever::boot_rom;
static void require(bool value) { if (!value) abort(); }
struct Memory { uint8_t bytes[8]; unsigned calls; };
static bool copy(void* user, uint32_t destination, const uint8_t* bytes, uint32_t count) {
    Memory& memory = *static_cast<Memory*>(user);
    if (destination > sizeof(memory.bytes) || count > sizeof(memory.bytes) - destination)
        return false;
    memcpy(memory.bytes + destination, bytes, count);
    ++memory.calls;
    return true;
}
int main() {
    Context context;
    Auxiliary auxiliary;
    memset(&context, 0xa5, sizeof(context));
    memset(&auxiliary, 0xa5, sizeof(auxiliary));
    require(prepare(context, auxiliary, 0x88000000, 0x88001000));
    for (size_t i = 0; i != context_size; ++i) {
        uint8_t expected = 0;
        if (i == reader_offset + 3) expected = 0x88;
        if (i == auxiliary_pointer_offset + 1) expected = 0x10;
        if (i == auxiliary_pointer_offset + 3) expected = 0x88;
        require(context.bytes[i] == expected);
    }
    for (size_t i = 0; i != auxiliary_size; ++i) require(auxiliary.bytes[i] == 0);
    require(!prepare(context, auxiliary, 0, 0x88001000));
    require(!prepare(context, auxiliary, 0x88000001, 0x88001000));
    require(!prepare(context, auxiliary, 0x88000000, 0x88001001));
    const uint8_t source[] = {1, 2, 3, 4};
    Memory memory = {};
    ReadRequest request = {1, 2, 3};
    require(read(request, source, sizeof(source), copy, &memory) == ReadResult::ok);
    require(request.offset == 4 && memory.calls == 1);
    require(memory.bytes[2] == 2 && memory.bytes[3] == 3 && memory.bytes[4] == 4);
    request = {4, 0, 1};
    require(read(request, source, sizeof(source), copy, &memory) == ReadResult::source_bounds);
    require(request.offset == 4 && memory.calls == 1);
    request = {0, 7, 2};
    require(read(request, source, sizeof(source), copy, &memory) == ReadResult::copy_failed);
    require(request.offset == 0 && memory.calls == 1);
    request = {0, UINT32_MAX, 2};
    require(read(request, source, sizeof(source), copy, &memory) == ReadResult::destination_bounds);
    request = {4, 0, 0};
    require(read(request, 0, sizeof(source), 0, 0) == ReadResult::ok);
    return 0;
}

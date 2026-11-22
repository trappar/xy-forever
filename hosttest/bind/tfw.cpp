// native/tfw.hpp: random-access reads of a .tfw container's payload (tests/test_tfw.py).
#include <cstring>
#include <string>
#include <tuple>
#include <nanobind/nanobind.h>
#include <nanobind/stl/tuple.h>
#include "guarded.hpp"
#include "native/tfw.hpp"

namespace nb = nanobind;
using namespace xyf_core;

namespace {

struct Memory {
    const Byte* data;
    U32 size;
};
bool read_memory(void* context, U32 offset, Byte* out, U32 count) {
    Memory& memory = *static_cast<Memory*>(context);
    if (offset > memory.size || count > memory.size - offset) return false;
    std::memcpy(out, memory.data + offset, count);
    return true;
}

// The key provider: key (32 bytes, or none) for indexes 0, 1 and 255; counts its calls and the last index.
struct Keys {
    const Byte* key;
    U32 calls, index;
};
bool read_key(void* context, U32 index, Byte* key) {
    Keys& provider = *static_cast<Keys*>(context);
    ++provider.calls;
    provider.index = index;
    if (!provider.key || (index != 0 && index != 1 && index != 255)) return false;
    std::memcpy(key, provider.key, 32);
    return true;
}

}  // namespace

void bind_tfw(nb::module_ m) {
    // read(source, key or None, offset, count) -> (status, output (count bytes, 'X' where unwritten), the last key
    // index asked for, key calls). Status 100: the read failed; 101: the reader survived close.
    m.def(
        "read",
        [](nb::bytes source, nb::object key, U32 offset, U32 count) {
            nb::bytes hold;
            const Byte* k = xyf_glue::optional_bytes(key, hold);
            if (k && hold.size() != 32) throw nb::value_error("key: 32 bytes");
            Memory memory = {xyf_glue::bytes_of(source), static_cast<U32>(source.size())};
            Keys keys = {k, 0, 0};
            std::string output(count, 'X');
            tfw::Archive archive;
            int status = static_cast<int>(archive.open({&memory, memory.size, read_memory}, {&keys, read_key}));
            if (status == 0) {
                const input::Reader reader = archive.reader();
                if (!input::read(reader, offset, reinterpret_cast<Byte*>(&output[0]), count)) status = 100;
                else {
                    archive.close();
                    status = archive.reader().read ? 101 : 0;
                }
            }
            return std::make_tuple(status, nb::bytes(output.data(), output.size()), keys.index, keys.calls);
        },
        nb::arg("source"), nb::arg("key").none(), nb::arg("offset"), nb::arg("count"));
}

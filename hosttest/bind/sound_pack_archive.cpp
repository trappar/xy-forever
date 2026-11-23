// target/sound_pack_archive.hpp: a signed pack's archive layout and manifest admission, over a ZIP in memory.
#include <cstring>
#include <memory>
#include <string>
#include <tuple>
#include <vector>
#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/tuple.h>
#include "target/sound_pack_archive.hpp"

namespace nb = nanobind;
using namespace xyf_core;
namespace pack = xy_forever::sound_pack_archive;
namespace z = xyf_core::zip;

namespace {

struct Input {
    const Byte* data;
    U32 size;
};
bool reader(void* context, U32 offset, Byte* output, U32 count) {
    const Input& input = *static_cast<Input*>(context);
    if (offset > input.size || count > input.size - offset) return false;
    std::memcpy(output, input.data + offset, count);
    return true;
}

}  // namespace

void bind_sound_pack_archive(nb::module_ m) {
    // admit(zip bytes) -> (status name, pack folder, files, bytes, the pack's total output limit).
    m.def("admit", [](nb::bytes data) {
        Input input = {reinterpret_cast<const Byte*>(data.c_str()), static_cast<U32>(data.size())};
        const z::Reader source = {&input, input.size, reader};
        const z::Limits limits = pack::limits();
        std::vector<z::Entry> entries(limits.entries);
        z::Archive archive;
        z::Status status = z::open(source, limits, entries.data(), limits.entries, archive);
        pack::Layout layout = {};
        if (status == z::Status::ok) {
            std::unique_ptr<z::InflateWorkspace> inflate(new z::InflateWorkspace);
            std::unique_ptr<pack::ManifestBuffer> manifest(new pack::ManifestBuffer);
            status = pack::admit(archive, *inflate, *manifest, layout);
        }
        return std::make_tuple(std::string(z::status_name(status)), std::string(layout.folder), layout.files,
                               layout.bytes, limits.total_bytes);
    });
}

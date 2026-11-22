// native/tfw.hpp -> native/zip.hpp -> native/package.hpp: the production readers composed, rather than
// decrypting to an intermediate ZIP (tests/test_tfw_package_pipeline.py). The only key is the public synthetic
// vector from tests/test_tfw.py.
#include <memory>
#include <vector>
#include <nanobind/nanobind.h>
#include <nanobind/stl/tuple.h>
#include "native/package.hpp"
#include "native/tfw.hpp"

namespace nb = nanobind;
using namespace xyf_core;

namespace {

struct Source {
    const Byte* bytes;
    U32 size;
};
bool read_bytes(void* context, U32 offset, Byte* out, U32 count) {
    const Source& source = *static_cast<Source*>(context);
    if (offset > source.size || count > source.size - offset) return false;
    for (U32 i = 0; i < count; ++i) out[i] = source.bytes[offset + i];
    return true;
}
bool synthetic_key(void*, U32 index, Byte out[32]) {
    if (index > 1) return false;
    for (U32 i = 0; i < 32; ++i) out[i] = Byte(i);
    return true;
}

}  // namespace

void bind_tfw_package(nb::module_ m) {
    // run(container) -> (0 when admitted else 1, report): report is {phase: tfw, status: tfw::Status} or
    // {phase: zip, status: name} or {phase: admission, status: name, items, excluded (bootloader items)}.
    m.def("run", [](nb::bytes data) {
        Source source = {reinterpret_cast<const Byte*>(data.data()), static_cast<U32>(data.size())};
        input::Reader reader = {&source, source.size, read_bytes};
        tfw::KeyProvider keys = {0, synthetic_key};
        tfw::Archive decrypted;
        nb::dict report;
        const tfw::Status ts = decrypted.open(reader, keys);
        if (ts != tfw::Status::ok) {
            report["phase"] = "tfw";
            report["status"] = static_cast<U32>(ts);
            return std::make_tuple(1, report);
        }
        std::vector<zip::Entry> entries(4096);
        zip::Archive archive;
        input::Reader clear = decrypted.reader();
        zip::Reader zip_reader = {clear.context, clear.size, clear.read};
        const zip::Status zs = zip::open(zip_reader, zip::Limits(), entries.data(), 4096, archive);
        if (zs != zip::Status::ok) {
            report["phase"] = "zip";
            report["status"] = zip::status_name(zs);
            return std::make_tuple(1, report);
        }
        std::vector<Byte> scratch(8 * 1024 * 1024);
        package::Workspace workspace;
        workspace.scratch = scratch.data();
        workspace.capacity = static_cast<U32>(scratch.size());
        std::vector<package::Item> items(4096);
        package::Manifest manifest = {};
        const Range ranges[] = {{0x1000, 0x10000}};
        Policy policy = {ranges, 1, 0, 0};
        const package::AdmissionStatus ps = package::admit(archive, policy, workspace, items.data(), 4096, manifest);
        U32 excluded = 0;
        if (ps == package::AdmissionStatus::ok)
            for (U32 i = 0; i < manifest.count; ++i)
                if (manifest.items[i].destination.role == package::Role::excluded_bootloader) ++excluded;
        report["phase"] = "admission";
        report["status"] = package::admission_status_name(ps);
        report["items"] = manifest.count;
        report["excluded"] = excluded;
        return std::make_tuple(ps == package::AdmissionStatus::ok ? 0 : 1, report);
    });
}

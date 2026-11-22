// native/content.hpp: plan and apply a content change through a Python disk object.
#include <cstring>
#include <string>
#include <vector>
#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/vector.h>
#include "native/content.hpp"

namespace nb = nanobind;
using namespace xyf_core;

namespace {

// (path, size, sha256 digest) -> a content item.
std::vector<package::Item> items(const std::vector<std::tuple<std::string, unsigned, nb::bytes>>& rows) {
    std::vector<package::Item> out(rows.size());
    for (size_t i = 0; i < rows.size(); ++i) {
        package::Item& item = out[i];
        const std::string& path = std::get<0>(rows[i]);
        const nb::bytes& digest = std::get<2>(rows[i]);
        if (path.size() > 256 || digest.size() != 32) throw nb::value_error("path over 256 bytes, or digest not 32");
        item = {};
        item.entry_index = static_cast<U32>(i);
        item.size = std::get<1>(rows[i]);
        item.destination.role = package::Role::content;
        std::memcpy(item.destination.relative, path.c_str(), path.size() + 1);
        std::memcpy(item.digest.bytes, digest.data(), 32);
    }
    return out;
}

nb::object& disk(void* context) { return *static_cast<nb::object*>(context); }
nb::bytes digest_bytes(const hash::Digest& d) { return nb::bytes(reinterpret_cast<const char*>(d.bytes), 32); }

bool read(void* context, const char* method, const char* path, U32 offset, Byte* out, U32 count) {
    nb::object data = disk(context).attr(method)(path, offset, count);
    if (data.is_none()) return false;
    nb::bytes bytes = nb::cast<nb::bytes>(data);
    if (bytes.size() != count) return false;
    std::memcpy(out, bytes.data(), count);
    return true;
}

const content::IO io_for(nb::object& d) {
    content::IO io = {};
    io.context = &d;
    io.target_size = [](void* c, const char* path, bool& exists, U32& size) {
        nb::object got = disk(c).attr("size")(path);       // None: absent; else its size
        exists = !got.is_none();
        size = exists ? nb::cast<U32>(got) : 0;
        return true;
    };
    io.read_target = [](void* c, const char* path, U32 offset, Byte* out, U32 count) {
        return read(c, "read_target", path, offset, out, count);
    };
    io.read_staged = [](void* c, const char* path, U32 offset, Byte* out, U32 count) {
        return read(c, "read_staged", path, offset, out, count);
    };
    io.replace = [](void* c, const char* path, U32 size, const hash::Digest& digest) {
        return nb::cast<bool>(disk(c).attr("replace")(path, size, digest_bytes(digest)));
    };
    io.remove_matching = [](void* c, const char* path, U32 size, const hash::Digest& digest) {
        return nb::cast<bool>(disk(c).attr("remove")(path, size, digest_bytes(digest)));
    };
    return io;
}

}  // namespace

void bind_content(nb::module_ m) {
    // run(desired, previous, remove_old, disk) -> (status, kept, installed, removed); items are (path, size,
    // digest) and disk has size, read_target, read_staged, replace and remove (tests/test_content.py: Disk).
    m.def("run", [](const std::vector<std::tuple<std::string, unsigned, nb::bytes>>& desired,
                    const std::vector<std::tuple<std::string, unsigned, nb::bytes>>& previous, bool remove_old,
                    nb::object d) {
        std::vector<package::Item> want = items(desired), had = items(previous);
        package::Manifest manifest = {};
        manifest.items = want.data();
        manifest.count = static_cast<U32>(want.size());
        content::Operation operations[64];
        content::Plan plan;
        content::Report report = {0, 0, 0};
        const content::IO io = io_for(d);
        content::Status status =
            content::plan(manifest, had.data(), static_cast<U32>(had.size()), remove_old, io, operations, 64, plan);
        if (status == content::Status::ok) status = content::apply(plan, io, report);
        return std::make_tuple(static_cast<int>(status), report.kept, report.installed, report.removed);
    });
}

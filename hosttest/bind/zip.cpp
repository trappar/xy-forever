// native/zip.hpp and native/inflate: the differential driver of tests/test_native_zip.py, in memory.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>
#include <tuple>
#include <vector>
#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/vector.h>
#include "native/zip.hpp"

namespace nb = nanobind;
using namespace xyf_core;
namespace z = xyf_core::zip;

namespace {

struct Input {
    const Byte* data;
    U32 size, fail_at, last, back; // back: reads before the one before them (a jump back)
};
bool reader(void* context, U32 offset, Byte* output, U32 count) {
    Input& input = *static_cast<Input*>(context);
    input.back += offset < input.last;
    input.last = offset;
    if (offset > input.fail_at || count > input.fail_at - offset) return false;
    if (offset > input.size || count > input.size - offset) return false;
    std::memcpy(output, input.data + offset, count);
    return true;
}
bool writer(void* context, const Byte* data, U32 count) {
    static_cast<std::string*>(context)->append(reinterpret_cast<const char*>(data), count);
    return true;
}
bool refused(void*, const Byte*, U32) { return false; }
U32 number(const std::string& text) { return static_cast<U32>(std::strtoul(text.c_str(), 0, 10)); }

struct Result {
    int code;
    std::string out, err;
};
Result failed(Result& r, z::Status status) {
    r.code = 1;
    r.err = std::string(z::status_name(status)) + "\n";
    return r;
}

// The old zip_bridge's commands, over data in memory (args: after the input path):
//   raw SIZE                                     inflate a raw stream of SIZE bytes out
//   list [CAPACITY [MEMBER [TOTAL [RATIO]]]]     "name\tsize\tcrc32\tmethod\n" per entry (args[0]: unused)
//   back                                         the reads of open() before the one before them, "N\n"
//   extract NAME | all | fail-read NAME | fail-sink NAME
// Exit codes: 0 ok, 1 refused (stderr: the status name), 2 usage or no member selected.
Result run(const std::string& command, const Byte* data, U32 size, const std::vector<std::string>& args) {
    Result r = {0, std::string(), std::string()};
    Input context = {data, size, 0xffffffffu, 0, 0};
    z::Reader input = {&context, size, reader};
    z::Sink sink = {&r.out, writer};
    std::unique_ptr<z::InflateWorkspace> workspace(new z::InflateWorkspace);
    z::Extraction extracted;
    if (command == "raw") {
        if (args.size() != 1) return r.code = 2, r;
        const z::Status status = z::inflate_raw(input, 0, input.size, number(args[0]), *workspace, sink, extracted);
        return status == z::Status::ok ? r : failed(r, status);
    }
    const bool list = command == "list";
    z::Limits limits;
    const U32 capacity = list && args.size() > 1 ? number(args[1]) : 4096;
    if (list && args.size() > 2) limits.member_bytes = number(args[2]);
    if (list && args.size() > 3) limits.total_bytes = number(args[3]);
    if (list && args.size() > 4) limits.expansion_ratio = number(args[4]);
    std::vector<z::Entry> entries(capacity ? capacity : 1);
    z::Archive archive;
    z::Status status = z::open(input, limits, entries.data(), capacity, archive);
    if (status != z::Status::ok) return failed(r, status);
    if (command == "back") {
        r.out = std::to_string(context.back) + "\n";
        return r;
    }
    const bool all = command == "all";
    if (list) {
        for (U32 i = 0; i != archive.count; ++i) {
            const z::Entry& entry = entries[i];
            char line[400];
            std::snprintf(line, sizeof line, "%s\t%u\t%u\t%u\n", entry.name, entry.size, entry.crc32, entry.method);
            r.out += line;
        }
        return r;
    }
    if (command == "fail-read") context.fail_at = 0;
    if (command == "fail-sink") sink.write = refused;
    if (!all && args.size() != 1) return r.code = 2, r;
    bool selected = false;
    for (U32 i = 0; i != archive.count; ++i) {
        const z::Entry& entry = entries[i];
        if (entry.directory || (!all && args[0] != entry.name)) continue;
        selected = true;
        if (all) {
            char line[400];
            std::snprintf(line, sizeof line, "%s\t%u\t%u\n", entry.name, entry.size, entry.crc32);
            r.out += line;
        }
        status = z::extract(archive, i, *workspace, sink, extracted);
        if (status != z::Status::ok) return failed(r, status);
    }
    if (!selected) r.code = 2;
    return r;
}

}  // namespace

void bind_zip(nb::module_ m) {
    // run(command, data, args) -> (exit code, stdout bytes, stderr bytes), as the old command-line driver.
    m.def("run", [](const std::string& command, nb::bytes data, const std::vector<std::string>& args) {
        const Result r = run(command, reinterpret_cast<const Byte*>(data.c_str()), static_cast<U32>(data.size()), args);
        return std::make_tuple(r.code, nb::bytes(r.out.data(), r.out.size()), nb::bytes(r.err.data(), r.err.size()));
    });
}

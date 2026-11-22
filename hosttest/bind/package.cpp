// native/package.hpp and native/images.hpp: the admission/staging differential and fault-injection driver of
// tests/test_native_package.py, in memory (tests/package_bridge.cpp is the same driver as a program, for the
// sanitized run).
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <tuple>
#include <vector>
#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/tuple.h>
#include "native/package.hpp"

namespace nb = nanobind;
using namespace xyf_core;
namespace p = xyf_core::package;
namespace z = xyf_core::zip;

namespace {

void print(std::string& out, const char* format, ...) {
    char line[512];
    va_list args;
    va_start(args, format);
    std::vsnprintf(line, sizeof line, format, args);
    va_end(args);
    out += line;
}
struct Source { std::vector<Byte> data; };
bool reader(void* context, U32 offset, Byte* output, U32 count) {
    Source& source = *static_cast<Source*>(context);
    if (offset > source.data.size() || count > source.data.size() - offset) return false;
    for (U32 i = 0; i != count; ++i) output[i] = source.data[offset + i];
    return true;
}
void quoted(std::string& out, const char* text) {
    out += '"';
    for (U32 i = 0; text[i]; ++i) {
        if (text[i] == '"' || text[i] == '\\') out += '\\';
        out += text[i];
    }
    out += '"';
}
void digest(std::string& out, const hash::Digest& value) {
    out += '"';
    for (U32 i = 0; i != 32; ++i) print(out, "%02x", value.bytes[i]);
    out += '"';
}
struct Backend {
    Source* source;
    const z::Archive* archive;
    std::string fault;
    U32 begun, ended, bytes, aborted, attempted_publish;
    bool published, mutated;
    std::vector<std::string> paths;
    Backend() : source(0), archive(0), begun(0), ended(0), bytes(0), aborted(0),
        attempted_publish(0), published(false), mutated(false) {}
    void mutate() {
        if (mutated) return;
        if (fault == "mutate-comment") {
            source->data[source->data.size() - 1] ^= 1;
            mutated = true; return;
        }
        for (U32 i = archive->count; i; --i) {
            const z::Entry& entry = archive->entries[i - 1];
            if (!entry.directory && entry.compressed_size) {
                source->data[entry.data_offset] ^= 1;
                mutated = true; break;
            }
        }
    }
};
bool begin(void* context, const char* path, U32, const hash::Digest&) {
    Backend& backend = *static_cast<Backend*>(context);
    ++backend.begun; backend.paths.push_back(path);
    if (backend.fault == "mutate-during") backend.mutate();
    return backend.fault != "begin-fail";
}
bool write(void* context, const Byte*, U32 count) {
    Backend& backend = *static_cast<Backend*>(context);
    backend.bytes += count;
    return backend.fault != "write-fail";
}
bool end(void* context) {
    Backend& backend = *static_cast<Backend*>(context);
    ++backend.ended;
    if (backend.fault == "mutate-after-end" || backend.fault == "mutate-comment") backend.mutate();
    return backend.fault != "end-fail";
}
void aborted(void* context) { ++static_cast<Backend*>(context)->aborted; }
bool publish(void* context, const p::Manifest&) {
    Backend& backend = *static_cast<Backend*>(context);
    ++backend.attempted_publish;
    backend.published = backend.fault != "publish-fail";
    return backend.published;
}
int run(const std::string& mode, Source& source, const std::string& fault, std::string& out) {
    if (mode == "image-ble") {
        images::BleSummary summary;
        images::Status status = images::inspect_ble(source.data.data(), static_cast<U32>(source.data.size()), summary);
        print(out, "{\"status\":%u,\"stack\":%u,\"reset\":%u,\"crc16\":%u}\n", static_cast<U32>(status),summary.stack,summary.reset,summary.crc16);
        return status == images::Status::ok ? 0 : 1;
    }
    if (mode == "image-cyacd") {
        images::CyacdSummary summary;
        images::Status status = images::inspect_cyacd(source.data.data(), static_cast<U32>(source.data.size()), summary);
        print(out, "{\"status\":%u,\"silicon\":%u,\"rows\":%u", static_cast<U32>(status),summary.silicon_id,summary.rows);
        if (status == images::Status::ok && !fault.empty()) {
            Byte output[288]; for (U32 i = 0; i != 288; ++i) output[i] = 0xa5;
            companion::CypressRow row;
            status = images::cyacd_row(source.data.data(), static_cast<U32>(source.data.size()), static_cast<U32>(std::strtoul(fault.c_str(),0,10)),output+16,256,row);
            bool guards = true;
            for (U32 i = 0; i != 16; ++i) if (output[i] != 0xa5 || output[i+272] != 0xa5) guards = false;
            print(out, ",\"row_status\":%u,\"row_number\":%u,\"row_size\":%u,\"guards\":%s",static_cast<U32>(status),row.number,row.size,guards?"true":"false");
            if (status == images::Status::ok) {
                hash::Sha256 hash; hash::Digest result; hash.update(row.data,row.size); hash.finish(result);
                print(out, ",\"row_sha256\":"); digest(out, result);
            }
        }
        print(out, "}\n");
        return status == images::Status::ok ? 0 : 1;
    }
    z::Reader source_reader = {&source, static_cast<U32>(source.data.size()), reader};
    std::vector<z::Entry> entries(4096);
    z::Archive archive;
    z::Status opened = z::open(source_reader, z::Limits(), entries.data(), static_cast<U32>(entries.size()), archive);
    if (opened != z::Status::ok) {
        print(out, "{\"status\":\"zip-%s\",\"begun\":0,\"published\":false}\n", z::status_name(opened));
        return 1;
    }
    std::vector<Byte> scratch(8u * 1024u * 1024u);
    p::Workspace workspace;
    workspace.scratch = scratch.data(); workspace.capacity = static_cast<U32>(scratch.size());
    std::vector<p::Item> items(4096);
    p::Manifest manifest = {};
    const Range allowed[] = {{0x1000,0x10000}, {0x08000000,0x08040000}, {0x11800000,0x11c00000}, {0x80000000,0x90000000}};
    const Range protected_ranges[] = {{0x0803e0c0,0x0803e142},{0x11803000,0x11804000},
        {0x8803d000,0x8803d02a},{0x88a346a6,0x88a3481c},{0x88a34aec,0x88a34d6c},
        {0x8a000000,0x8a810000},{0x8b000000,0x8b800000}};
    Policy policy = {allowed,4,protected_ranges,7};
    if (fault == "small-scratch") workspace.capacity = 1024;
    const Range forbidden[] = {{0x1000,0x1002}};
    if (fault == "protect-os") { policy.protected_ranges = forbidden; policy.protected_count = 1; }
    p::AdmissionStatus status = p::admit(archive, policy, workspace, items.data(), static_cast<U32>(items.size()), manifest);
    Backend backend; backend.source = &source; backend.archive = &archive; backend.fault = fault;
    p::Writer writer = {&backend,begin,write,end,aborted,publish};
    if (status == p::AdmissionStatus::ok && mode == "stage") {
        if (fault == "mutate-before") backend.mutate();
        if (fault == "forged-path") std::strcpy(items[0].destination.relative, "../../os.ldr");
        status = p::stage(archive, manifest, workspace, writer);
    }
    print(out, "{\"status\":\"%s\",\"begun\":%u,\"ended\":%u,\"bytes\":%u,\"aborted\":%u,\"publish_attempts\":%u,\"published\":%s,\"paths\":[",
        p::admission_status_name(status),backend.begun,backend.ended,backend.bytes,backend.aborted,backend.attempted_publish,
        backend.published?"true":"false");
    for (U32 i = 0; i != backend.paths.size(); ++i) { if (i) out += ','; quoted(out, backend.paths[i].c_str()); }
    print(out, "],\"items\":[");
    for (U32 i = 0; i != manifest.count; ++i) {
        const p::Item& item = items[i];
        if (i) out += ',';
        print(out, "{\"entry\":%u,\"role\":%u,\"size\":%u,\"destination\":", item.entry_index,static_cast<U32>(item.destination.role),item.size);
        quoted(out, item.destination.relative);
        print(out, ",\"sha256\":"); digest(out, item.digest); out += '}';
    }
    out += ']';
    if (manifest.count) {
        print(out, ",\"archive_sha256\":"); digest(out, manifest.archive_digest);
        print(out, ",\"os_item\":%u,\"backplane_item\":%u,\"ble_item\":%u", manifest.os_item,manifest.backplane_item,manifest.ble_item);
        const p::Version* versions[] = {&manifest.os_version,&manifest.backplane_version,&manifest.ble_version,&manifest.firmware_version};
        const char* names[] = {"os_version","backplane_version","ble_version","firmware_version"};
        const bool present[] = {manifest.has_os_version,manifest.has_backplane_version,manifest.has_ble_version,manifest.has_firmware_version};
        for (U32 i = 0; i != 4; ++i) if (present[i]) {
            print(out, ",\"%s\":[%u,%u,%u,%u]",names[i],versions[i]->field[0],versions[i]->field[1],versions[i]->field[2],versions[i]->field[3]);
        }
    }
    print(out, "}\n");
    return status == p::AdmissionStatus::ok ? 0 : 1;
}

}  // namespace

void bind_package(nb::module_ m) {
    // run(mode, data, fault) -> (exit code, the JSON report): mode admit, stage, image-ble or image-cyacd (fault:
    // the row index); fault as the old driver's third argument.
    m.def("run", [](const std::string& mode, nb::bytes data, const std::string& fault) {
        Source source;
        source.data.assign(reinterpret_cast<const Byte*>(data.c_str()), reinterpret_cast<const Byte*>(data.c_str()) + data.size());
        std::string out;
        const int code = run(mode, source, fault, out);
        return std::make_tuple(code, out);
    }, nb::arg("mode"), nb::arg("data"), nb::arg("fault") = "");
}

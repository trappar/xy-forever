// Host-only driver for admission/staging differential and fault-injection tests.
#include "../native/package.hpp"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <string>
using namespace xyf_core;
namespace p = xyf_core::package;
namespace z = xyf_core::zip;
struct Source {
    std::vector<Byte> data;
};
bool reader(void* context, U32 offset, Byte* output, U32 count) {
    Source& source = *static_cast<Source*>(context);
    if (offset > source.data.size() || count > source.data.size() - offset) return false;
    for (U32 i = 0; i != count; ++i) output[i] = source.data[offset + i];
    return true;
}
void quoted(const char* text) {
    std::putchar('"');
    for (U32 i = 0; text[i]; ++i) {
        if (text[i] == '"' || text[i] == '\\') std::putchar('\\');
        std::putchar(text[i]);
    }
    std::putchar('"');
}
void digest(const hash::Digest& value) {
    std::putchar('"');
    for (U32 i = 0; i != 32; ++i) std::printf("%02x", value.bytes[i]);
    std::putchar('"');
}
struct Backend {
    Source* source;
    const z::Archive* archive;
    std::string fault;
    U32 begun, ended, bytes, aborted, attempted_publish;
    bool published, mutated;
    std::vector<std::string> paths;
    Backend()
        : source(0), archive(0), begun(0), ended(0), bytes(0), aborted(0), attempted_publish(0), published(false),
          mutated(false) {}
    void mutate() {
        if (mutated) return;
        if (fault == "mutate-comment") {
            source->data[source->data.size() - 1] ^= 1;
            mutated = true;
            return;
        }
        for (U32 i = archive->count; i; --i) {
            const z::Entry& entry = archive->entries[i - 1];
            if (!entry.directory && entry.compressed_size) {
                source->data[entry.data_offset] ^= 1;
                mutated = true;
                break;
            }
        }
    }
};
bool begin(void* context, const char* path, U32, const hash::Digest&) {
    Backend& backend = *static_cast<Backend*>(context);
    ++backend.begun;
    backend.paths.push_back(path);
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
void abort(void* context) { ++static_cast<Backend*>(context)->aborted; }
bool publish(void* context, const p::Manifest&) {
    Backend& backend = *static_cast<Backend*>(context);
    ++backend.attempted_publish;
    backend.published = backend.fault != "publish-fail";
    return backend.published;
}
int main(int argc, char** argv) {
    if (argc < 3) return 2;
    std::FILE* input = std::fopen(argv[2], "rb");
    if (!input || std::fseek(input, 0, SEEK_END)) return 2;
    long size = std::ftell(input);
    if (size < 0 || static_cast<unsigned long>(size) > 0xfffffffful || std::fseek(input, 0, SEEK_SET)) return 2;
    Source source;
    source.data.resize(static_cast<U32>(size));
    if (std::fread(source.data.data(), 1, source.data.size(), input) != source.data.size()) return 2;
    std::fclose(input);
    if (std::strcmp(argv[1], "image-ble") == 0) {
        images::BleSummary summary;
        images::Status status = images::inspect_ble(source.data.data(), static_cast<U32>(source.data.size()), summary);
        std::printf("{\"status\":%u,\"stack\":%u,\"reset\":%u,\"crc16\":%u}\n", static_cast<U32>(status), summary.stack,
                    summary.reset, summary.crc16);
        return status == images::Status::ok ? 0 : 1;
    }
    if (std::strcmp(argv[1], "image-cyacd") == 0) {
        images::CyacdSummary summary;
        images::Status status =
            images::inspect_cyacd(source.data.data(), static_cast<U32>(source.data.size()), summary);
        std::printf("{\"status\":%u,\"silicon\":%u,\"rows\":%u", static_cast<U32>(status), summary.silicon_id,
                    summary.rows);
        if (status == images::Status::ok && argc > 3) {
            Byte output[288];
            for (U32 i = 0; i != 288; ++i) output[i] = 0xa5;
            companion::CypressRow row;
            status = images::cyacd_row(source.data.data(), static_cast<U32>(source.data.size()),
                                       static_cast<U32>(std::strtoul(argv[3], 0, 10)), output + 16, 256, row);
            bool guards = true;
            for (U32 i = 0; i != 16; ++i)
                if (output[i] != 0xa5 || output[i + 272] != 0xa5) guards = false;
            std::printf(",\"row_status\":%u,\"row_number\":%u,\"row_size\":%u,\"guards\":%s", static_cast<U32>(status),
                        row.number, row.size, guards ? "true" : "false");
            if (status == images::Status::ok) {
                hash::Sha256 hash;
                hash::Digest result;
                hash.update(row.data, row.size);
                hash.finish(result);
                std::printf(",\"row_sha256\":");
                digest(result);
            }
        }
        std::printf("}\n");
        return status == images::Status::ok ? 0 : 1;
    }
    z::Reader source_reader = {&source, static_cast<U32>(source.data.size()), reader};
    std::vector<z::Entry> entries(4096);
    z::Archive archive;
    z::Status opened = z::open(source_reader, z::Limits(), entries.data(), static_cast<U32>(entries.size()), archive);
    if (opened != z::Status::ok) {
        std::printf("{\"status\":\"zip-%s\",\"begun\":0,\"published\":false}\n", z::status_name(opened));
        return 1;
    }
    std::vector<Byte> scratch(8u * 1024u * 1024u);
    p::Workspace workspace;
    workspace.scratch = scratch.data();
    workspace.capacity = static_cast<U32>(scratch.size());
    std::vector<p::Item> items(4096);
    p::Manifest manifest = {};
    const Range allowed[] = {
        {0x1000, 0x10000}, {0x08000000, 0x08040000}, {0x11800000, 0x11c00000}, {0x80000000, 0x90000000}};
    const Range protected_ranges[] = {{0x0803e0c0, 0x0803e142}, {0x11803000, 0x11804000}, {0x8803d000, 0x8803d02a},
                                      {0x88a346a6, 0x88a3481c}, {0x88a34aec, 0x88a34d6c}, {0x8a000000, 0x8a810000},
                                      {0x8b000000, 0x8b800000}};
    Policy policy = {allowed, 4, protected_ranges, 7};
    std::string fault = argc > 3 ? argv[3] : "";
    if (fault == "small-scratch") workspace.capacity = 1024;
    const Range forbidden[] = {{0x1000, 0x1002}};
    if (fault == "protect-os") {
        policy.protected_ranges = forbidden;
        policy.protected_count = 1;
    }
    p::AdmissionStatus status =
        p::admit(archive, policy, workspace, items.data(), static_cast<U32>(items.size()), manifest);
    Backend backend;
    backend.source = &source;
    backend.archive = &archive;
    backend.fault = fault;
    p::Writer writer = {&backend, begin, write, end, abort, publish};
    if (status == p::AdmissionStatus::ok && std::strcmp(argv[1], "stage") == 0) {
        if (fault == "mutate-before") backend.mutate();
        if (fault == "forged-path") std::strcpy(items[0].destination.relative, "../../os.ldr");
        status = p::stage(archive, manifest, workspace, writer);
    }
    std::printf(
        "{\"status\":\"%s\",\"begun\":%u,\"ended\":%u,\"bytes\":%u,\"aborted\":%u,\"publish_attempts\":%u,\"published\":%s,\"paths\":[",
        p::admission_status_name(status), backend.begun, backend.ended, backend.bytes, backend.aborted,
        backend.attempted_publish, backend.published ? "true" : "false");
    for (U32 i = 0; i != backend.paths.size(); ++i) {
        if (i) std::putchar(',');
        quoted(backend.paths[i].c_str());
    }
    std::printf("],\"items\":[");
    for (U32 i = 0; i != manifest.count; ++i) {
        const p::Item& item = items[i];
        if (i) std::putchar(',');
        std::printf("{\"entry\":%u,\"role\":%u,\"size\":%u,\"destination\":", item.entry_index,
                    static_cast<U32>(item.destination.role), item.size);
        quoted(item.destination.relative);
        std::printf(",\"sha256\":");
        digest(item.digest);
        std::putchar('}');
    }
    std::putchar(']');
    if (manifest.count) {
        std::printf(",\"archive_sha256\":");
        digest(manifest.archive_digest);
        std::printf(",\"os_item\":%u,\"backplane_item\":%u,\"ble_item\":%u", manifest.os_item, manifest.backplane_item,
                    manifest.ble_item);
        const p::Version* versions[] = {&manifest.os_version, &manifest.backplane_version, &manifest.ble_version,
                                        &manifest.firmware_version};
        const char* names[] = {"os_version", "backplane_version", "ble_version", "firmware_version"};
        const bool present[] = {manifest.has_os_version, manifest.has_backplane_version, manifest.has_ble_version,
                                manifest.has_firmware_version};
        for (U32 i = 0; i != 4; ++i)
            if (present[i]) {
                std::printf(",\"%s\":[%u,%u,%u,%u]", names[i], versions[i]->field[0], versions[i]->field[1],
                            versions[i]->field[2], versions[i]->field[3]);
            }
    }
    std::printf("}\n");
    return status == p::AdmissionStatus::ok ? 0 : 1;
}

// Host-only differential bridge. The native implementation has no stdlib use.
#include "../native/zip.hpp"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

using namespace xyf_core;
namespace z = xyf_core::zip;
struct File {
    std::FILE* file;
    U32 fail_at;
};
bool reader(void* context, U32 offset, Byte* output, U32 count) {
    File* file = static_cast<File*>(context);
    if (offset > file->fail_at || count > file->fail_at - offset) return false;
    return std::fseek(file->file, static_cast<long>(offset), SEEK_SET) == 0 &&
           std::fread(output, 1, count, file->file) == count;
}
bool writer(void*, const Byte* data, U32 count) { return std::fwrite(data, 1, count, stdout) == count; }
bool refused(void*, const Byte*, U32) { return false; }
int failed(z::Status status) {
    std::fprintf(stderr, "%s\n", z::status_name(status));
    return 1;
}
int main(int argc, char** argv) {
    if (argc < 3) return 2;
    std::FILE* file = std::fopen(argv[2], "rb");
    if (!file) return 2;
    if (std::fseek(file, 0, SEEK_END) != 0) return 2;
    long length = std::ftell(file);
    if (length < 0 || static_cast<unsigned long>(length) > 0xfffffffful) return 2;
    File context = {file, 0xffffffffu};
    z::Reader input = {&context, static_cast<U32>(length), reader};
    z::Sink sink = {0, writer};
    z::InflateWorkspace workspace;
    z::Extraction extracted;
    if (std::strcmp(argv[1], "raw") == 0) {
        if (argc != 4) return 2;
        z::Status status = z::inflate_raw(input, 0, input.size, static_cast<U32>(std::strtoul(argv[3], 0, 10)),
                                          workspace, sink, extracted);
        std::fclose(file);
        return status == z::Status::ok ? 0 : failed(status);
    }
    z::Limits limits;
    U32 capacity =
        argc > 4 && std::strcmp(argv[1], "list") == 0 ? static_cast<U32>(std::strtoul(argv[4], 0, 10)) : 4096;
    if (argc > 5 && std::strcmp(argv[1], "list") == 0)
        limits.member_bytes = static_cast<U32>(std::strtoul(argv[5], 0, 10));
    if (argc > 6 && std::strcmp(argv[1], "list") == 0)
        limits.total_bytes = static_cast<U32>(std::strtoul(argv[6], 0, 10));
    if (argc > 7 && std::strcmp(argv[1], "list") == 0)
        limits.expansion_ratio = static_cast<U32>(std::strtoul(argv[7], 0, 10));
    std::vector<z::Entry> entries(capacity ? capacity : 1);
    z::Archive archive;
    z::Status status = z::open(input, limits, entries.data(), capacity, archive);
    if (status != z::Status::ok) {
        std::fclose(file);
        return failed(status);
    }
    bool all = std::strcmp(argv[1], "all") == 0;
    if (std::strcmp(argv[1], "list") == 0) {
        for (U32 i = 0; i != archive.count; ++i) {
            const z::Entry& entry = entries[i];
            std::printf("%s\t%u\t%u\t%u\n", entry.name, entry.size, entry.crc32, entry.method);
        }
        std::fclose(file);
        return 0;
    }
    if (std::strcmp(argv[1], "fail-read") == 0) context.fail_at = 0;
    if (std::strcmp(argv[1], "fail-sink") == 0) sink.write = refused;
    if (!all && argc != 4) return 2;
    bool selected = false;
    for (U32 i = 0; i != archive.count; ++i) {
        const z::Entry& entry = entries[i];
        if (entry.directory || (!all && std::strcmp(argv[3], entry.name) != 0)) continue;
        selected = true;
        if (all) std::printf("%s\t%u\t%u\n", entry.name, entry.size, entry.crc32);
        status = z::extract(archive, i, workspace, sink, extracted);
        if (status != z::Status::ok) {
            std::fclose(file);
            return failed(status);
        }
    }
    std::fclose(file);
    return selected ? 0 : 2;
}

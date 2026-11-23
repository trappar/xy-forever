// Host adapter for native/tfw_os: runs the device's freestanding extractor
// and signature check against a .tfw file on the Mac. Built and driven by xy_forever/tfw_extract.py.
//
//   tfw_extract digest PACKAGE
//   tfw_extract extract PACKAGE OUT (--public-key-stdin | --unverified | --reject-signature)
//               [--digest HEX] [--allow LO:HI]... [--image-limit N] [--directory-limit N]
//               [--entries N] [--fail-write]
//   tfw_extract check FILE [--public-key-stdin] [--allow LO:HI]...    (native/tfw_check)
//
// check: the tfw.check verdict on a package or a cut of one. Without --public-key-stdin the device
// has no public key (reason no_otp); AES records follow as for extract (none: no_otp).
//
// The signature mode is required: --public-key-stdin verifies the header with the vendor
// public key (tfw::verify_header), --unverified accepts it unchecked and says so, and
// --reject-signature refuses it (tests). Keys arrive on stdin, never on the command line:
// with --public-key-stdin first the 56-byte public key (Qx || Qy), then 33-byte AES records
// (index, 32 key bytes); they are erased after use and never printed. OUT is created only
// when the extractor hands over its first byte. --allow makes every package read outside the
// listed byte ranges fail (the read-restriction proof). The policy is cold.cpp's
// (gen/cold_policy.inc). Prints JSON.
#include "../native/tfw_os.hpp"
#include "../native/tfw_signature.hpp"
#include "../native/tfw_check.hpp"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
using namespace xyf_core;

namespace {
#include "cold_policy.inc"

struct Range64 {
    unsigned long long lo, hi;
};
struct Source {
    std::FILE* file;
    U32 size;
    std::vector<Range64> allowed;
    unsigned long long reads, refused;
};
bool read_file(void* context, U32 offset, Byte* out, U32 count) {
    Source& s = *static_cast<Source*>(context);
    ++s.reads;
    if (!s.allowed.empty()) {
        bool inside = false;
        for (const Range64& r : s.allowed)
            if (r.lo <= offset && (unsigned long long)offset + count <= r.hi) inside = true;
        if (!inside) {
            ++s.refused;
            return false;
        }
    }
    return std::fseek(s.file, long(offset), SEEK_SET) == 0 && std::fread(out, 1, count, s.file) == count;
}

struct Keys {
    Byte records[8][33];
    U32 count, calls;
};
bool read_key(void* context, U32 index, Byte key[32]) {
    Keys& k = *static_cast<Keys*>(context);
    ++k.calls;
    for (U32 i = 0; i != k.count; ++i)
        if (k.records[i][0] == index) {
            std::memcpy(key, k.records[i] + 1, 32);
            return true;
        }
    return false;
}
void scrub(Keys& k) {
    volatile Byte* p = &k.records[0][0];
    for (U32 i = 0; i != sizeof(k.records); ++i) p[i] = 0;
}

// The header check: the device's own HeaderCheck (tfw::signature_check), with the reason of a
// refusal kept for the report.
enum class Mode { none, verify, unverified, reject };
struct Signature {
    Mode mode;
    Byte key[tfw::signature_public_key_bytes];
    bool called;
    tfw::Signature result;
};
bool check_header(void* context, const Byte* header, U32 size) {
    Signature& s = *static_cast<Signature*>(context);
    s.called = true;
    if (s.mode == Mode::unverified) return size == tfw::crypto_header_bytes;
    if (s.mode != Mode::verify) return false;
    const tfw::HeaderCheck device = tfw::signature_check(s.key);
    const bool accepted = device.verify(device.context, header, size);
    s.result = accepted ? tfw::Signature::valid : tfw::verify_header(header, size, s.key);
    return accepted;
}
const char* signature_text(const Signature& s) {
    if (!s.called) return "not reached";
    switch (s.mode) {
    case Mode::verify: return tfw::signature_name(s.result);
    case Mode::unverified: return "not checked";
    default: return "refused by test";
    }
}

struct Output {
    const char* path;
    std::FILE* file;
    bool fail;
};
bool write_out(void* context, const Byte* bytes, U32 count) {
    Output& o = *static_cast<Output*>(context);
    if (o.fail) return false;
    if (!o.file && !(o.file = std::fopen(o.path, "wb"))) return false;
    return std::fwrite(bytes, 1, count, o.file) == count;
}

void hex(const Byte* bytes, U32 count, char* out) {
    for (U32 i = 0; i != count; ++i) std::snprintf(out + 2 * i, 3, "%02x", bytes[i]);
}
bool parse_hex(const char* text, Byte* out, U32 count) {
    if (std::strlen(text) != 2 * count) return false;
    for (U32 i = 0; i != count; ++i) {
        unsigned value;
        if (std::sscanf(text + 2 * i, "%2x", &value) != 1) return false;
        out[i] = Byte(value);
    }
    return true;
}
long long ms_since(std::chrono::steady_clock::time_point t) {
    return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t).count();
}
int fail(const char* message) {
    std::printf("{\"status\":\"usage\",\"detail\":\"%s\"}\n", message);
    return 2;
}

// check: the file as the device's read path serves it (short at the end), and the device's keys.
int read_some(void* context, U32 offset, Byte* out, U32 count) {
    Source& s = *static_cast<Source*>(context);
    ++s.reads;
    bool inside = s.allowed.empty();
    for (const Range64& r : s.allowed)
        if (r.lo <= offset && (unsigned long long)offset + count <= r.hi) inside = true;
    if (!inside) {
        ++s.refused;
        return -1;
    }
    if (std::fseek(s.file, long(offset), SEEK_SET)) return -1;
    return int(std::fread(out, 1, count, s.file));
}
struct DeviceKeys {
    Byte public_key[tfw::signature_public_key_bytes];
    bool has_public;
    U32 public_calls;
};
bool read_public(void* context, Byte out[56]) {
    DeviceKeys& k = *static_cast<DeviceKeys*>(context);
    ++k.public_calls;
    if (!k.has_public) return false;
    std::memcpy(out, k.public_key, sizeof(k.public_key));
    return true;
}
int check_main(Source& source, int argc, char** argv) {
    static DeviceKeys device = {{}, false, 0};
    for (int i = 3; i < argc; ++i) {
        if (!std::strcmp(argv[i], "--public-key-stdin")) device.has_public = true;
        else if (!std::strcmp(argv[i], "--allow") && i + 1 < argc) {
            Range64 r;
            if (std::sscanf(argv[++i], "%llu:%llu", &r.lo, &r.hi) != 2 || r.lo > r.hi) return fail("allow");
            source.allowed.push_back(r);
        } else return fail("unknown option");
    }
    if (device.has_public &&
        std::fread(device.public_key, 1, sizeof(device.public_key), stdin) != sizeof(device.public_key))
        return fail("public key expected on stdin");
    Keys keys = {};
    while (keys.count < 8 && std::fread(keys.records[keys.count], 1, 33, stdin) == 33) ++keys.count;
    static tfw_check::Workspace workspace;
    tfw_check::Result result = {};
    const tfw_check::File file = {&source, read_some};
    const tfw_check::Keys device_keys = {&device, read_public, {&keys, read_key}};
    const auto started = std::chrono::steady_clock::now();
    const tfw_check::Reason reason = tfw_check::check(file, device_keys, workspace, result);
    const long long ms = ms_since(started);
    scrub(keys);
    volatile Byte* wipe = device.public_key;
    for (U32 i = 0; i != sizeof(device.public_key); ++i) wipe[i] = 0;
    std::fclose(source.file);
    char sha[65] = "";
    if (result.public_key_read) hex(result.public_key_sha256.bytes, 32, sha);
    std::printf("{\"status\":\"%s\",\"reason\":\"%s\",\"public_key_read\":%s,\"public_key_sha256\":\"%s\","
                "\"container\":\"%s\",\"entries\":%u,\"public_key_calls\":%u,\"key_calls\":%u,"
                "\"reads\":%llu,\"refused_reads\":%llu,\"workspace_bytes\":%u,\"ms\":%lld}\n",
                reason == tfw_check::Reason::pass ? "pass" : "fail", tfw_check::reason_name(reason),
                result.public_key_read ? "true" : "false", sha, tfw::status_name(result.container), result.entries,
                device.public_calls, keys.calls, source.reads, source.refused, U32(sizeof(workspace)), ms);
    return reason == tfw_check::Reason::pass ? 0 : 1;
}
}

int main(int argc, char** argv) {
    if (argc < 3) return fail("command and package required");
    const bool digest_only = !std::strcmp(argv[1], "digest"), check_only = !std::strcmp(argv[1], "check");
    if (!digest_only && !check_only && (std::strcmp(argv[1], "extract") || argc < 4))
        return fail("digest PACKAGE | extract PACKAGE OUT | check FILE");
    Source source = {std::fopen(argv[2], "rb"), 0, {}, 0, 0};
    if (!source.file || std::fseek(source.file, 0, SEEK_END)) return fail("cannot open package");
    const long size = std::ftell(source.file);
    if (size < 0 || size > 0x10000000L) return fail("package size");
    source.size = U32(size);
    if (check_only) return check_main(source, argc, argv);
    const input::Reader reader = {&source, source.size, read_file};
    static Byte buffer[1u << 16];
    hash::Digest digest;
    bool have_digest = false;
    tfw_os::Limits limits;
    Output output = {digest_only ? "" : argv[3], 0, false};
    static Signature signature = {Mode::none, {}, false, tfw::Signature::argument};
    const tfw::HeaderCheck check = {&signature, check_header};
    for (int i = digest_only ? 3 : 4; i < argc; ++i) {
        const bool more = i + 1 < argc;
        if (!std::strcmp(argv[i], "--digest") && more) {
            if (!parse_hex(argv[++i], digest.bytes, 32)) return fail("digest");
            have_digest = true;
        } else if (!std::strcmp(argv[i], "--allow") && more) {
            Range64 r;
            if (std::sscanf(argv[++i], "%llu:%llu", &r.lo, &r.hi) != 2 || r.lo > r.hi) return fail("allow");
            source.allowed.push_back(r);
        } else if (!std::strcmp(argv[i], "--image-limit") && more)
            limits.image_bytes = U32(std::strtoul(argv[++i], 0, 0));
        else if (!std::strcmp(argv[i], "--directory-limit") && more)
            limits.directory_bytes = U32(std::strtoul(argv[++i], 0, 0));
        else if (!std::strcmp(argv[i], "--entries") && more) limits.entries = U32(std::strtoul(argv[++i], 0, 0));
        else if (!std::strcmp(argv[i], "--fail-write")) output.fail = true;
        else if (!std::strcmp(argv[i], "--reject-signature")) signature.mode = Mode::reject;
        else if (!std::strcmp(argv[i], "--unverified")) signature.mode = Mode::unverified;
        else if (!std::strcmp(argv[i], "--public-key-stdin")) signature.mode = Mode::verify;
        else return fail("unknown option");
    }
    if (!digest_only && signature.mode == Mode::none)
        return fail("signature mode required: --public-key-stdin, --unverified or --reject-signature");
    const auto started = std::chrono::steady_clock::now();
    long long digest_ms = 0;
    tfw::Status digest_status = tfw::Status::ok;
    if (!have_digest) {
        // The keyless pass sees the whole file as opaque
        // ciphertext; it runs before any read restriction and decrypts nothing.
        std::vector<Range64> restriction;
        restriction.swap(source.allowed);
        digest_status = tfw::ciphertext_digest(reader, buffer, sizeof(buffer), digest);
        restriction.swap(source.allowed);
        digest_ms = ms_since(started);
    }
    char digest_hex[65] = "";
    if (digest_status == tfw::Status::ok) hex(digest.bytes, 32, digest_hex);
    if (digest_only) {
        std::printf("{\"status\":\"%s\",\"ciphertext_sha256\":\"%s\",\"ms\":%lld}\n",
                    digest_status == tfw::Status::ok ? "ok" : "container", digest_hex, digest_ms);
        return digest_status == tfw::Status::ok ? 0 : 1;
    }
    Keys keys = {};
    if (signature.mode == Mode::verify &&
        std::fread(signature.key, 1, sizeof(signature.key), stdin) != sizeof(signature.key))
        return fail("public key expected on stdin");
    while (keys.count < 8 && std::fread(keys.records[keys.count], 1, 33, stdin) == 33) ++keys.count;
    static Byte scratch[4u << 20];
    static tfw_os::Workspace workspace;
    workspace.scratch = scratch;
    workspace.capacity = sizeof(scratch);
    const Policy policy = {allowed, sizeof(allowed) / sizeof(allowed[0]), protected_ranges,
                           sizeof(protected_ranges) / sizeof(protected_ranges[0])};
    tfw_os::Result result = {};
    tfw_os::Status status = tfw_os::Status::container;
    result.container = digest_status;
    source.reads = 0;
    const auto extract_started = std::chrono::steady_clock::now();
    if (digest_status == tfw::Status::ok)
        status = tfw_os::extract(reader, {&keys, read_key}, digest, check, policy, 0x11a00000u, limits, workspace,
                                 {&output, write_out}, result);
    const long long extract_ms = ms_since(extract_started);
    scrub(keys);
    const bool closed = !output.file || std::fclose(output.file) == 0;
    std::fclose(source.file);
    char sha[65], version[32] = "", package_version[32] = "";
    hex(result.sha256.bytes, 32, sha);
    tfw_os::version_text(result.os_version, version, sizeof(version));
    tfw_os::version_text(result.package_version, package_version, sizeof(package_version));
    const bool ok = status == tfw_os::Status::ok && closed;
    std::printf("{\"status\":\"%s\",\"phase\":\"%s\",\"container\":\"%s\",\"inflate\":\"%s\",\"ldr\":\"%s\","
                "\"bytes\":%u,\"crc32\":\"%08x\",\"sha256\":\"%s\",\"entry\":%u,\"records\":%u,"
                "\"version\":\"%s\",\"package_version\":\"%s\",\"written\":%u,\"output_created\":%s,"
                "\"key_calls\":%u,\"reads\":%llu,\"refused_reads\":%llu,\"ciphertext_sha256\":\"%s\","
                "\"signature\":\"%s\",\"scratch_bytes\":%u,\"workspace_bytes\":%u,"
                "\"digest_ms\":%lld,\"extract_ms\":%lld}\n",
                closed ? tfw_os::status_name(status) : "write", digest_status == tfw::Status::ok ? "extract" : "digest",
                tfw::status_name(result.container), zip::status_name(result.inflate), error_name(result.ldr),
                result.bytes, result.crc32, sha, result.entry, result.records, version, package_version, result.written,
                output.file ? "true" : "false", keys.calls, source.reads, source.refused, digest_hex,
                signature_text(signature), tfw_os::scratch_bytes(limits), U32(sizeof(tfw_os::Workspace)), digest_ms,
                extract_ms);
    return ok ? 0 : 1;
}

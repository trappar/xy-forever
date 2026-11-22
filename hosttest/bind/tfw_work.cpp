// native/tfw_os.hpp, tfw.hpp, zip.hpp: cancellation through the signed TFW reader, ZIP members and OS writer,
// against a synthetic signed package without device I/O (tests/test_tfw_work.py).
#include <stdexcept>
#include <string>
#include <vector>
#include <nanobind/nanobind.h>
#include "native/tfw_os.hpp"
#include "native/tfw_signature.hpp"
#include "native/zip.hpp"

namespace nb = nanobind;
using namespace xyf_core;

// A failed check raises (the glue builds without assert's NDEBUG dependence).
#define CHECK(condition) \
    do { if (!(condition)) throw std::runtime_error("tfw_work line " + std::to_string(__LINE__) + ": " #condition); } while (0)

namespace {

struct Progress {
    U32 polls, stop_at, reads, read_bytes, signatures, keys, writes, written;
    bool stop_after_signature, stop_on_output, stopped;
};
struct Source {
    std::vector<Byte> data, public_key;
    Progress progress;
    std::string failure;     // the first check a callback failed (callbacks do not raise through native code)
};
void require(Source& s, bool condition, const char* text) {
    if (!condition && s.failure.empty()) s.failure = text;
}
#define REQUIRE_IN(s, condition) require(s, condition, #condition)

bool keep_going(void* context) {
    Source& s = *static_cast<Source*>(context);
    Progress& p = s.progress;
    REQUIRE_IN(s, !p.stopped);
    p.stopped = ++p.polls == p.stop_at || (p.stop_after_signature && p.signatures) ||
                (p.stop_on_output && p.written);
    return !p.stopped;
}
bool read(void* context, U32 offset, Byte* out, U32 count) {
    Source& s = *static_cast<Source*>(context);
    REQUIRE_IN(s, !s.progress.stopped && count <= 512);
    REQUIRE_IN(s, offset <= s.data.size() && count <= s.data.size() - offset);
    if (offset > s.data.size() || count > s.data.size() - offset) return false;
    ++s.progress.reads;
    s.progress.read_bytes += count;
    for (U32 i = 0; i != count; ++i) out[i] = s.data[offset + i];
    return true;
}
bool verify(void* context, const Byte* header, U32 size) {
    Source& s = *static_cast<Source*>(context);
    REQUIRE_IN(s, !s.progress.stopped);
    ++s.progress.signatures;
    return tfw::verify_header(header, size, s.public_key.data()) == tfw::Signature::valid;
}
bool key(void* context, U32 index, Byte* out) {
    Source& s = *static_cast<Source*>(context);
    REQUIRE_IN(s, !s.progress.stopped && !index);
    ++s.progress.keys;
    for (U32 i = 0; i != 32; ++i) out[i] = Byte(i);
    return true;
}
bool sink(void* context, const Byte*, U32 count) {
    Source& s = *static_cast<Source*>(context);
    REQUIRE_IN(s, !s.progress.stopped && count <= 32768);
    ++s.progress.writes;
    s.progress.written += count;
    return true;
}

void run(Source& s) {
    Progress& p = s.progress;
    const input::Reader source = {&s, U32(s.data.size()), read};
    const tfw::HeaderCheck signature = {&s, verify};
    const tfw::KeyProvider keys = {&s, key};
    Byte buffer[4096];
    const hash::Digest unchanged = {{42}};
    hash::Digest digest = unchanged;
    Work interrupted_hash(&s, keep_going);
    p.stop_at = 3;
    CHECK(tfw::ciphertext_digest(source, buffer, sizeof(buffer), digest, tfw::op_xy_sku, &interrupted_hash)
          == tfw::Status::cancelled);
    CHECK(p.reads == 2 && p.read_bytes == 320 + 512 && hash::equal(digest, unchanged));
    CHECK(!interrupted_hash.checkpoint() && p.polls == 3);

    p = {};
    Work hashing(&s, keep_going);
    CHECK(tfw::ciphertext_digest(source, buffer, sizeof(buffer), digest, tfw::op_xy_sku, &hashing)
          == tfw::Status::ok);
    CHECK(p.read_bytes == s.data.size() - tfw::header_bytes + 320 && !p.keys);

    tfw::Archive decrypted;
    p = {};
    Work wrong_digest(&s, keep_going);
    CHECK(decrypted.open_digested(source, keys, unchanged, signature, buffer, tfw::op_xy_sku, &wrong_digest)
          == tfw::Status::ciphertext_hash);
    CHECK(p.signatures == 1 && !p.keys && !decrypted.reader().read);

    p = {};
    Work before_key(&s, keep_going);
    p.stop_after_signature = true;
    CHECK(decrypted.open_digested(source, keys, digest, signature, buffer, tfw::op_xy_sku, &before_key)
          == tfw::Status::cancelled);
    CHECK(p.signatures == 1 && !p.keys && !decrypted.reader().read);

    p = {};
    Work during_decryption(&s, keep_going);
    CHECK(decrypted.open_digested(source, keys, digest, signature, buffer, tfw::op_xy_sku, &during_decryption)
          == tfw::Status::ok);
    p.stop_at = p.polls + 3;
    const U32 read_before = p.read_bytes;
    CHECK(!input::read(decrypted.reader(), 0, buffer, sizeof(buffer)));
    CHECK(during_decryption.cancelled() && p.read_bytes - read_before == 16 + 512);
    decrypted.close();

    p = {};
    Work opening_directory(&s, keep_going);
    CHECK(decrypted.open_digested(source, keys, digest, signature, buffer, tfw::op_xy_sku, &opening_directory)
          == tfw::Status::ok);
    input::Reader clear = decrypted.reader();
    std::vector<zip::Entry> entries(16);
    zip::Archive archive;
    p.stop_at = p.polls + 3;  // cancel in the underlying read of the ZIP end record
    CHECK(zip::open({clear.context, clear.size, clear.read}, zip::Limits(), entries.data(), U32(entries.size()),
                    archive, &opening_directory) == zip::Status::cancelled);
    CHECK(!archive.count && !archive.entries);
    decrypted.close();

    p = {};
    Work extraction(&s, keep_going);
    CHECK(decrypted.open_digested(source, keys, digest, signature, buffer, tfw::op_xy_sku, &extraction)
          == tfw::Status::ok);
    clear = decrypted.reader();
    CHECK(zip::open({clear.context, clear.size, clear.read}, zip::Limits(), entries.data(), U32(entries.size()),
                    archive, &extraction) == zip::Status::ok);
    zip::InflateWorkspace inflate;
    zip::Extraction extracted;
    CHECK(zip::extract(archive, 0, inflate, {&s, sink}, extracted, &extraction) == zip::Status::ok);
    CHECK(p.written == entries[0].size && p.signatures == 1 && p.keys == 1);
    CHECK(zip::extract(archive, 2, inflate, {nullptr, nullptr}, extracted, &extraction) == zip::Status::ok);
    CHECK(extracted.produced == entries[2].size && p.signatures == 1 && p.keys == 1);
    p.writes = p.written = 0;
    p.stop_on_output = true;
    CHECK(zip::extract(archive, 1, inflate, {&s, sink}, extracted, &extraction) == zip::Status::cancelled);
    CHECK(!extracted.produced && p.written == 32768 && p.writes == 1 && p.keys == 1);
    decrypted.close();

    p = {};
    Work writing_image(&s, keep_going);
    p.stop_on_output = true;
    const Range ranges[] = {{0x11a00000u, 0x11a01000u}, {0x80001000u, 0x80100000u}};
    const Policy policy = {ranges, 2, nullptr, 0};
    tfw_os::Limits limits;
    std::vector<U32> scratch(tfw_os::scratch_bytes(limits) / sizeof(U32));
    tfw_os::Workspace workspace;
    workspace.scratch = reinterpret_cast<Byte*>(scratch.data());
    workspace.capacity = U32(scratch.size() * sizeof(U32));
    tfw_os::Result result;
    CHECK(tfw_os::extract(source, keys, digest, signature, policy, 0x11a00000u, limits,
                          workspace, {&s, sink}, result, &writing_image) == tfw_os::Status::cancelled);
    CHECK(result.ldr == Error::ok && !result.bytes && result.written == 32768 && p.writes == 1);
    CHECK(!workspace.archive.reader().read);
}

}  // namespace

void bind_tfw_work(nb::module_ m) {
    // run(package, public key): raises RuntimeError naming the first failed check.
    m.def("run", [](nb::bytes data, nb::bytes public_key) {
        const Byte* d = reinterpret_cast<const Byte*>(data.c_str());
        const Byte* k = reinterpret_cast<const Byte*>(public_key.c_str());
        Source s = {std::vector<Byte>(d, d + data.size()), std::vector<Byte>(k, k + public_key.size()), {}, std::string()};
        CHECK(!s.data.empty() && s.public_key.size() == tfw::signature_public_key_bytes);
        run(s);
        if (!s.failure.empty()) throw std::runtime_error("tfw_work callback: " + s.failure);
    });
}

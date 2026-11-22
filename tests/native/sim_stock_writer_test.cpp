// sim/stock_boot.ipp: the simulator's real stock writer shim with observable local media/RPC stand-ins: both role
// routes, ABI, retries and commit/cut ordering. Synthetic media only: the XOR representation tests buffer ownership
// and ciphertext readback; it supplies no cryptographic evidence about the real bootloader.
#include "doctest.h"
#include <atomic>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#include "../../sim/boot_crypto.hpp"

namespace {
using xyf_sim::boot_crypt;
using xyf_sim::boot_crypto_mark;
struct State { uint32_t capacity = 0x40000; std::atomic<int32_t> partition{0}, fail_select{0}; } state, *shared = &state;
uint32_t selected = 0, role = 0;
const uint32_t erase_block = 0x10000, cls_partition = 1, cls_sector = 2, cls_write = 3;
std::vector<uint8_t> media[2];
struct Op { uint32_t partition, sector, count; };
std::vector<Op> writes;
int rpc = 0, failed_write = 0, fail_read = 0, corrupt_sector = -1, cut = 0;
bool stuck = false;
int stuck_attempts = 0, body_reads = 0;
uint32_t wanted_body_writes = 1;
struct Cut {};
struct Stuck {};
void log(const char*, ...) {}
void stock_log(const char*) {}
uint32_t crc32(const uint8_t* data, uint32_t bytes) {
    uint32_t crc = ~0u;
    for (uint32_t i = 0; i != bytes; ++i) {
        crc ^= data[i];
        for (int bit = 0; bit != 8; ++bit) crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}
bool sectors_io(bool write, uint32_t sector, uint32_t count, uint8_t* data) {
    REQUIRE((selected == 1 || selected == 2));
    REQUIRE((sector + count) * 512 <= shared->capacity);
    if (write && stuck) {
        if (++stuck_attempts == 1000) throw Stuck{};  // stop observing retries; no production timeout
        return false;
    }
    if (write && failed_write) { --failed_write; return false; }
    if (!write && fail_read) { --fail_read; return false; }
    auto* at = media[selected - 1].data() + sector * 512;
    if (write) {
        memcpy(at, data, count * 512);
        writes.push_back({selected, sector, count});
        if (cut && int(writes.size()) == cut) throw Cut{};
    } else {
        if (selected == 2 && sector && !writes.empty()) {
            ++body_reads;
            uint32_t body_writes = 0;
            for (const auto& w : writes) if (w.partition == 2 && w.sector % 128 == 1) ++body_writes;
            REQUIRE(body_writes == wanted_body_writes); // ALL chunks written before first verify read
        }
        memcpy(data, at, count * 512);
        if (int(sector) == corrupt_sector && (sector == 0 || !writes.empty())) data[0] ^= 1;
    }
    return true;
}
bool trim(uint32_t block) {
    std::vector<uint8_t> zero(erase_block);
    return sectors_io(true, block * 128, 128, zero.data());
}
uint32_t local_select(uint32_t);
uint32_t b_call(uint32_t cls, uint32_t op, uint32_t a0, uint32_t a1, uint32_t,
                const void* body, uint32_t bytes, void* out, uint32_t) {
    ++rpc;
    if (!cls) { REQUIRE(op == 5); return shared->capacity; }
    if (cls == cls_partition) return op == 0x18 ? local_select(a0) : trim(a0) ? 0 : uint32_t(-1);
    if (cls == cls_sector) return sectors_io(false, a0, a1, static_cast<uint8_t*>(out)) ? 0 : uint32_t(-1);
    REQUIRE((cls == cls_write && bytes == a1 * 512));
    return sectors_io(true, a0, a1, static_cast<uint8_t*>(const_cast<void*>(body))) ? 0 : uint32_t(-1);
}
#include "../../sim/stock_boot.ipp"

// The state a fresh process had (each case ran in its own process before).
void reset(uint32_t r) {
    state.capacity = 0x40000; state.partition = 0; state.fail_select = 0;
    selected = 0; role = r;
    for (auto& m : media) m.clear();
    writes.clear();
    rpc = failed_write = fail_read = 0; corrupt_sector = -1; cut = 0;
    stuck = false; stuck_attempts = body_reads = 0; wanted_body_writes = 1;
}

void run(uint32_t r, const std::string& test) {
    reset(r);
    for (auto& p : media) p.resize(shared->capacity);
    // An active old image. Its header version deliberately differs: writer tests active byte only.
    media[0][12] = 1; media[0][512] = 77;
    const uint32_t size = test == "multichunk" ? 100001 : test == "aligned" ? 10240 : 10001;
    wanted_body_writes = test == "multichunk" ? 2 : 1;
    std::vector<uint8_t> image(((size >> 9) + 1) * 512, 0x7b), plain;
    for (uint32_t i = 0; i != size; ++i) image[i] = uint8_t(i * 7);
    plain = image;
    if (test == "invalid") {
        REQUIRE(stock_writer(nullptr, size) == 0);
        REQUIRE(stock_writer(image.data(), 100) == 0);
        REQUIRE(stock_writer(image.data(), shared->capacity) == 0);
        REQUIRE(stock_writer(image.data() + 1, size) == 0);
        REQUIRE((image == plain && writes.empty()));
        return;
    }
    if (test == "initial") shared->fail_select = 1;
    if (test == "retry") { failed_write = 3; fail_read = 2; }
    if (test == "body") corrupt_sector = 1 + (size >> 9);
    if (test == "early" || test == "aligned") corrupt_sector = 1;
    if (test == "header") corrupt_sector = 0;
    if (test == "stuck") stuck = true;
    if (test == "cutbody") cut = 2;                  // target erase, body write; no header commit
    if (test == "cutcommit") cut = 3;                // committed new header, old still active
    if (test == "cutold") cut = 4;                   // old erased after commit
    uint32_t result = 0;
    try { result = stock_writer(image.data(), size); }
    catch (const Cut&) {
        REQUIRE((cut && int(writes.size()) == cut));
        REQUIRE(bool(media[0][12]) == (cut < 4));
        REQUIRE(bool(media[1][12]) == (cut >= 3));
        return;
    } catch (const Stuck&) {
        REQUIRE((test == "stuck" && stuck_attempts == 1000 && writes.empty()));
        REQUIRE((media[0][12] && !media[1][12]));
        return;
    }
    REQUIRE((!cut && test != "stuck"));
    REQUIRE((role == 0) == (rpc == 0));
    if (test == "initial") {
        REQUIRE((result == 1 && image == plain && writes.empty()));
        REQUIRE(selected == 1);
        return;
    }
    REQUIRE((selected == 0 && shared->partition == 0));
    REQUIRE(body_reads == int((size >> 9) + 1));
    REQUIRE((image != plain && image.back() != plain.back())); // padded staging mutated
    REQUIRE(!memcmp(media[1].data() + 512, image.data(), image.size()));
    xyf_sim::boot_crypt(image.data(), uint32_t(image.size()));
    REQUIRE(image == plain);                           // representation is reversible, not AES evidence
    REQUIRE(!media[0][12]);                            // even when new verify failed
    REQUIRE((writes.front().partition == 2 && writes.front().sector == 0));
    REQUIRE((writes.back().partition == 1 && writes.back().sector == 0));
    if (test == "body") {
        REQUIRE((result == 0 && !media[1][12]));
        REQUIRE(writes.size() == 3);                  // body verify failure skips only header commit
    } else {
        REQUIRE(result == (test == "header" ? 0 : size));
        REQUIRE((media[1][12] && xyf_sim::boot_crypto_model(media[1].data())));
        uint32_t header[3]; memcpy(header, media[1].data(), 12);
        REQUIRE((header[0] == 3 && header[1] == size && header[2] == crc32(plain.data(), size)));
        const unsigned commit = test == "multichunk" ? 4 : 2;
        REQUIRE((writes.size() == commit + 2 && writes[commit].partition == 2 && writes[commit].sector == 0));
    }
}
}  // namespace

TEST_CASE("both roles match the stock failure and commit paths") {
    for (uint32_t r : {0u, 1u})
        for (const char* test : {"success", "multichunk", "invalid", "initial", "retry", "body", "header", "early",
                                 "aligned", "stuck", "cutbody", "cutcommit", "cutold"}) {
            CAPTURE(r);
            CAPTURE(test);
            run(r, test);
        }
}

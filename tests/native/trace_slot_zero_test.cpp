// tier: gate
// native/trace_slot.hpp locate_zero: the large zero diagnostic slot lookup (census dump transport), sanitized.
#include <cstring>
#include <string>
#include <vector>
#include "doctest.h"
#include "trace_slot.hpp"

namespace {

typedef std::vector<unsigned char> Bytes;

void put_word(Bytes& out, unsigned value) {
    for (int i = 0; i != 4; ++i) out.push_back(static_cast<unsigned char>(value >> (8 * i)));
}

// One LDR block header (the XOR checksum in byte 2).
Bytes header(unsigned flags, unsigned address, unsigned count, unsigned argument = 0) {
    Bytes out;
    put_word(out, 0xad000000u | flags); put_word(out, address); put_word(out, count); put_word(out, argument);
    unsigned char checksum = 0;
    for (unsigned char byte : out) checksum ^= byte;
    out[2] = checksum;
    return out;
}

struct Record { unsigned address; Bytes data; };

// A first extent, a 4-byte boot record, the data records, the final record (tests/test_trace_slot.py image()).
Bytes image(const std::vector<Record>& records, unsigned entry = 0x1000) {
    Bytes body = header(4, 0x1000, 4);
    body.insert(body.end(), {'b', 'o', 'o', 't'});
    for (const Record& r : records) {
        const Bytes h = header(4, r.address, unsigned(r.data.size()));
        body.insert(body.end(), h.begin(), h.end());
        body.insert(body.end(), r.data.begin(), r.data.end());
    }
    const Bytes last = header(0x8004, entry, 0);
    body.insert(body.end(), last.begin(), last.end());
    Bytes out = header(0x5004, entry, 0, unsigned(body.size()));
    out.insert(out.end(), body.begin(), body.end());
    return out;
}

struct Found { std::string status; unsigned offset; };

// locate_zero under the test policy (0x1000..0x400000 allowed); it never modifies the image.
Found locate(const Bytes& data, unsigned target, unsigned count) {
    Bytes copy = data;
    xyf_core::Range allowed[] = {{0x1000, 0x400000}};
    const xyf_core::Policy policy = {allowed, 1, 0, 0};
    const xyf_core::trace_slot::Result result =
        xyf_core::trace_slot::locate_zero(copy.data(), unsigned(copy.size()), policy, target, count);
    CHECK(copy == data);
    return {xyf_core::trace_slot::status_name(result.status), result.file_offset};
}

}  // namespace

TEST_CASE("locate_zero finds the unique zero slot of a large size") {
    const unsigned size = 0x40000;
    const Bytes data = image({{0x20000, Bytes(size)}});
    const Found found = locate(data, 0x20000, size);
    REQUIRE(found.status == "ok");
    const unsigned at = found.offset - 16 + 4;
    CHECK((data[at] | data[at + 1] << 8 | data[at + 2] << 16 | unsigned(data[at + 3]) << 24) == 0x20000u);
}

TEST_CASE("locate_zero refuses mismatches, duplicates, overlaps, misses, bad sizes and images") {
    const unsigned size = 0x2000;
    Bytes first(size);
    first[0] = 1;
    CHECK(locate(image({{0x20000, first}}), 0x20000, size).status == "preimage_mismatch");
    CHECK(locate(image({{0x20000, Bytes(size)}, {0x20000, Bytes(size)}}), 0x20000, size).status == "duplicate_slot");
    CHECK(locate(image({{0x20000, Bytes(size)}, {0x20100, Bytes(16)}}), 0x20000, size).status ==
          "overlapping_record");
    CHECK(locate(image({{0x20000, Bytes(size)}}), 0x30000, size).status == "missing_slot");
    CHECK(locate(image({{0x20000, Bytes(size)}}), 0x20000, 0x400001).status == "invalid_argument");
    Bytes trailing = image({{0x20000, Bytes(size)}});
    trailing.push_back('x');
    CHECK(locate(trailing, 0x20000, size).status == "invalid_image");
}

// target/xyf_crc32.hpp's CRC-32 and native/inflate.hpp's zip CRC, fed from a chosen alignment.
#include <cstdint>
#include <cstring>
#include <vector>
#include <nanobind/nanobind.h>
#include "native/inflate.hpp"
#include "target/xyf_crc32.hpp"

namespace nb = nanobind;

namespace {

// data copied offset bytes past an aligned start (misaligned when offset is not 0 mod 4), then f(pointer, size).
template<class F> uint32_t at(nb::bytes data, unsigned offset, F f) {
    if (offset > 7) throw nb::value_error("offset: 0 to 7");
    std::vector<uint32_t> words((data.size() + 8) / 4 + 1);
    uint8_t* bytes = reinterpret_cast<uint8_t*>(words.data()) + offset;
    std::memcpy(bytes, data.data(), data.size());
    return f(bytes, static_cast<uint32_t>(data.size()));
}

}  // namespace

void bind_crc32(nb::module_ m) {
    // update(crc, data, offset): xyf::crc::update chained from crc; update_volatile likewise; zip(data, offset):
    // the zip CRC of data (xyf_core::zip::crc32_update from the initial state, finalized).
    m.def(
        "update",
        [](uint32_t crc, nb::bytes data, unsigned offset) {
            return at(data, offset, [crc](const uint8_t* p, uint32_t n) { return xyf::crc::update(crc, p, n); });
        },
        nb::arg("crc"), nb::arg("data"), nb::arg("offset") = 0);
    m.def(
        "update_volatile",
        [](uint32_t crc, nb::bytes data, unsigned offset) {
            return at(data, offset,
                      [crc](const uint8_t* p, uint32_t n) { return xyf::crc::update_volatile(crc, p, n); });
        },
        nb::arg("crc"), nb::arg("data"), nb::arg("offset") = 0);
    m.def(
        "zip",
        [](nb::bytes data, unsigned offset) {
            return at(data, offset, [](const uint8_t* p, uint32_t n) {
                return xyf_core::zip::crc32_update(0xffffffffu, p, n) ^ 0xffffffffu;
            });
        },
        nb::arg("data"), nb::arg("offset") = 0);
}

// native/ldr.hpp: inspect and expand an LDR image (tests/test_ldr.py).
#include <cstdint>
#include <tuple>
#include <vector>
#include <nanobind/nanobind.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/vector.h>
#include "native/ldr.hpp"

namespace nb = nanobind;
using namespace xyf_core;

namespace {

std::vector<Range> ranges(const std::vector<std::tuple<unsigned, unsigned>>& rows) {
    std::vector<Range> out;
    for (const auto& r : rows) out.push_back({std::get<0>(r), std::get<1>(r)});
    return out;
}

const Byte* bytes_of(const nb::bytes& b) { return reinterpret_cast<const Byte*>(b.data()); }

}  // namespace

void bind_ldr(nb::module_ m) {
    // inspect(data, allowed, protected) -> (error, (records, expanded_bytes, entry)); ranges are (begin, end).
    m.def("inspect", [](nb::bytes data, const std::vector<std::tuple<unsigned, unsigned>>& allowed,
                        const std::vector<std::tuple<unsigned, unsigned>>& protected_ranges) {
        const std::vector<Range> a = ranges(allowed), p = ranges(protected_ranges);
        const Policy policy = {a.data(), static_cast<U32>(a.size()), p.data(), static_cast<U32>(p.size())};
        Summary s = {0, 0, 0};
        const Error error = xyf_core::inspect(bytes_of(data), static_cast<U32>(data.size()), policy, s);
        return std::make_tuple(static_cast<int>(error), std::make_tuple(s.records, s.expanded_bytes, s.entry));
    });
    // expand(data, offset, delta, capacity) -> (error, the bytes produced).
    m.def("expand", [](nb::bytes data, unsigned offset, unsigned delta, unsigned capacity) {
        std::vector<Byte> out(capacity ? capacity : 1);
        U32 produced = 0;
        const Error error = xyf_core::expand(bytes_of(data), static_cast<U32>(data.size()), offset, delta, out.data(),
                                             capacity, produced);
        return std::make_tuple(static_cast<int>(error),
                               nb::bytes(reinterpret_cast<const char*>(out.data()), error == Error::ok ? produced : 0));
    });
}

// native/trace_slot.hpp: replace one exact LDR data slot in place, under a policy, with the caller's preimage and
// replacement (and the aliasing and null-pointer cases the test asks for by mode).
#include <cstring>
#include <string>
#include <vector>
#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/tuple.h>
#include "native/trace_slot.hpp"

namespace nb = nanobind;

void bind_trace_slot(nb::module_ m) {
    // replace(image, expected, replacement, target, count, mode) -> (status, file_offset, validation, image after)
    // mode: normal, protect (the slot itself protected), stock (the stock policy), alias_start, alias_end,
    // alias_prefix, alias_after (the replacement inside or right after the image), null_image, null_expected,
    // null_replacement.
    m.def("replace", [](nb::bytes original, nb::bytes expected, nb::bytes replacement, unsigned target, unsigned count,
                        const std::string& mode) {
        if (count > expected.size() || count > replacement.size()) throw nb::value_error("count over the slot bytes");
        const size_t size = original.size();
        std::vector<unsigned char> buffer(4096 + size + 4096);
        unsigned char* image = buffer.data() + 4096;
        std::memcpy(image, original.data(), size);
        const unsigned char* preimage = static_cast<const unsigned char*>(expected.data());
        const unsigned char* next = static_cast<const unsigned char*>(replacement.data());
        unsigned char* input = image;
        xyf_core::Range allowed[] = {{0x1000, 0x10000}};
        xyf_core::Range stock_allowed[] = {{0x08000000, 0x08040000}, {0x11800000, 0x11c00000},
                                           {0x80000000, 0x90000000}};
        xyf_core::Range stock_protected[] = {{0x0803e0c0, 0x0803e142}, {0x11803000, 0x11804000},
            {0x8803d000, 0x8803d02a}, {0x88a346a6, 0x88a3481c}, {0x88a34aec, 0x88a34d6c},
            {0x8a000000, 0x8a800000}, {0x8ab00000, 0x8ab10000}, {0x8b000000, 0x8b800000}};
        xyf_core::Range protected_range[] = {{target, target + count}};
        if (mode == "alias_start") next = image;
        if (mode == "alias_end") next = image + size - 1;
        if (mode == "alias_prefix") next = image - 1;
        if (mode == "alias_after") {
            next = image + size;
            std::memcpy(image + size, replacement.data(), count);
        }
        if (mode == "null_image") input = 0;
        if (mode == "null_expected") preimage = 0;
        if (mode == "null_replacement") next = 0;
        const bool protect = mode == "protect", stock = mode == "stock";
        const xyf_core::Policy policy = {stock ? stock_allowed : allowed, stock ? 3u : 1u,
            stock ? stock_protected : protected_range, stock ? 8u : protect ? 1u : 0u};
        const xyf_core::trace_slot::Result result =
            xyf_core::trace_slot::replace(input, unsigned(size), policy, target, preimage, next, count);
        return std::make_tuple(std::string(xyf_core::trace_slot::status_name(result.status)), result.file_offset,
                               std::string(xyf_core::error_name(result.validation_error)),
                               nb::bytes(reinterpret_cast<const char*>(image), size));
    });
}

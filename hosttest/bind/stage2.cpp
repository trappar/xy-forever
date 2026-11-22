// target/xyf_stage2.hpp: stage 2 image checks, its constants and path, and stage 1's decisions after it.
#include <cstdint>
#include <nanobind/nanobind.h>
#include <nanobind/stl/tuple.h>
#include "target/xyf_stage2.hpp"

namespace nb = nanobind;
using namespace xyf;

namespace {
const uint8_t* bytes_of(const nb::bytes& data) { return static_cast<const uint8_t*>(data.data()); }
}  // namespace

void bind_stage2(nb::module_ m) {
    m.def("validate", [](nb::bytes data, unsigned stage1) {
        stage2::Header h;
        return unsigned(stage2::validate(bytes_of(data), unsigned(data.size()), stage1, h));
    });
    m.def("accept", [](nb::bytes data, unsigned stage1) {
        stage2::Header h;
        return unsigned(stage2::accept(bytes_of(data), unsigned(data.size()), stage1, h));
    });
    m.def("constant", [](unsigned which) {
        using namespace stage2;
        const unsigned values[] = {window_begin, window_end, max_image, header_bytes, abi, this_stage1,
                                   exit_continue, exit_menu, event_key, event_link_lost};
        return which < sizeof(values) / sizeof(values[0]) ? values[which] : 0xdeadbeefu;
    });
    m.def("path", [] { return nb::bytes(stage2::path); });
    // classify(data, stage1) -> (Classification, detail)
    m.def("classify", [](nb::bytes data, unsigned stage1) {
        stage2::Header h;
        uint32_t detail = 0xdead;
        const unsigned out = unsigned(stage2::classify(bytes_of(data), unsigned(data.size()), stage1, h, detail));
        return std::make_tuple(out, detail);
    });
    m.def("after", [](unsigned exit, unsigned link_lost) { return unsigned(stage2::after(exit, link_lost != 0)); });
    // decide(exit, link_lost, count, features) -> (Decision, entry)
    m.def("decide", [](uint32_t exit, unsigned link_lost, unsigned count, unsigned features) {
        uint32_t entry = 0xdeadu;
        const unsigned out = unsigned(stage2::decide(exit, link_lost != 0, count, features, entry));
        return std::make_tuple(out, entry);
    });
}

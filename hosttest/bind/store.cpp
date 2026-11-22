// target/xyf_store.hpp: state.txt and info.txt parsing, the canonical state.txt, entry ids.
#include <cstring>
#include <string>
#include <tuple>
#include <vector>
#include <nanobind/nanobind.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/tuple.h>
#include "hosttest/bind/glue.hpp"
#include "target/xyf_store.hpp"

namespace nb = nanobind;
using namespace xyf::store;

namespace {

template <size_t N> nb::bytes field(const char (&s)[N]) { return nb::bytes(s, strnlen(s, N)); }
template <size_t N> void set(char (&out)[N], const nb::bytes& value) {
    if (value.size() >= N) throw nb::value_error("field too long");
    std::memcpy(out, value.data(), value.size());
}

typedef std::tuple<nb::bytes, nb::bytes, nb::bytes, nb::bytes, uint32_t> StateTuple;

StateTuple state_tuple(const State& s) {
    return StateTuple(field(s.default_id), field(s.once), field(s.stage2), field(s.stage2_previous), s.stage2_tries);
}

}  // namespace

void bind_store(nb::module_ m) {
    m.def("valid_id", [](xyf_glue::CStr id) { return valid_id(id); });
    // parse_state(text) -> None (refused) or (default_id, once, stage2, stage2_previous, stage2_tries)
    m.def("parse_state", [](nb::bytes text) -> std::optional<StateTuple> {
        State s;
        std::memset(&s, 0, sizeof(s));
        if (!parse_state(static_cast<const char*>(text.data()), uint32_t(text.size()), s)) return std::nullopt;
        return state_tuple(s);
    });
    // write_state(state tuple, capacity) -> the canonical text (b'' when it does not fit)
    m.def("write_state", [](const StateTuple& t, unsigned capacity) {
        State s;
        std::memset(&s, 0, sizeof(s));
        set(s.default_id, std::get<0>(t)); set(s.once, std::get<1>(t));
        set(s.stage2, std::get<2>(t)); set(s.stage2_previous, std::get<3>(t));
        s.stage2_tries = std::get<4>(t);
        std::vector<char> out(capacity + 1);
        const uint32_t n = write_state(s, out.data(), capacity);
        return nb::bytes(out.data(), n);
    });
    // parse_info(text) -> None or (name, version, kind, bytes, crc32, machines, has_bytes, has_crc32,
    // has_machines, has_generation)
    m.def("parse_info", [](nb::bytes text) -> nb::object {
        Info f;
        std::memset(&f, 0, sizeof(f));
        if (!parse_info(static_cast<const char*>(text.data()), uint32_t(text.size()), f)) return nb::none();
        return nb::make_tuple(field(f.name), field(f.version), unsigned(f.kind), f.bytes, f.crc32, f.machines,
                              f.has_bytes, f.has_crc32, f.has_machines, f.has_generation);
    });
}

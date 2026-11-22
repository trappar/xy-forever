// target/xyf_state.hpp: the device's state slot text (write_slot) for the host tool's parser.
#include <cstring>
#include <vector>
#include <nanobind/nanobind.h>
#include "hosttest/bind/glue.hpp"
#include "target/xyf_state.hpp"

namespace nb = nanobind;

void bind_state(nb::module_ m) {
    // slot_default(id, seq, capacity) -> the slot text of a state with only that default
    m.def(
        "slot_default",
        [](xyf_glue::CStr id, unsigned seq, unsigned capacity) {
            xyf::store::State s;
            std::memset(&s, 0, sizeof(s));
            std::strncpy(s.default_id, id, sizeof(s.default_id) - 1);
            std::vector<char> out(capacity + 1);
            return nb::bytes(out.data(), xyf::state::write_slot(s, seq, out.data(), capacity));
        },
        nb::arg("id"), nb::arg("seq"), nb::arg("capacity") = 512);
}

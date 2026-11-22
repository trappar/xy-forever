// native/json.hpp: a package or configuration document's bounded text view (tests/test_native_json.py).
#include <cstring>
#include <memory>
#include <optional>
#include <tuple>
#include <vector>
#include <nanobind/nanobind.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/tuple.h>
#include "native/json.hpp"

namespace nb = nanobind;
namespace j = xyf_core::json;

void bind_json(nb::module_ m) {
    // view(source, config, capacity) -> (text view or None, the source buffer after the parse).
    m.def("view", [](nb::bytes source, bool config, unsigned capacity) {
        std::vector<char> raw(source.size() + 1, 0);          // NUL-terminated, as a C caller's buffer
        std::memcpy(raw.data(), source.c_str(), source.size());
        std::vector<char> out(capacity ? capacity : 1);
        std::unique_ptr<j::Workspace> workspace(new j::Workspace);
        const j::Document d = j::parse(raw.data(), static_cast<uint32_t>(source.size()), *workspace,
                                       config ? j::config_schema : j::package_schema);
        uint32_t bytes = 0;
        const j::Status status = config ? j::config_text(d, out.data(), capacity, bytes)
                                        : j::package_text(d, out.data(), capacity, bytes);
        std::optional<nb::bytes> text;
        if (status == j::ok) text = nb::bytes(out.data(), bytes);
        return std::make_tuple(text, nb::bytes(raw.data(), source.size()));
    }, nb::arg("source"), nb::arg("config") = false, nb::arg("capacity") = 4096);
}

// target/xyf_official_content.hpp: the official paths and op1_factory.json put back (#331).
#include <string>
#include <tuple>
#include <vector>
#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/vector.h>
#include "target/xyf_official_content.hpp"

namespace nb = nanobind;
namespace o = xyf::official;

void bind_official_content(nb::module_ m) {
    // The table's entries, in order.
    m.def("path_hashes", [] { return std::vector<uint32_t>(o::path_hashes, o::path_hashes + o::path_count); });
    m.def("listed", [](const std::string& path) { return o::listed(path.c_str(), o::path_hashes, o::path_count); });
    // restore(text[, lists]): the text put back, or None (0). lists: (key, count) pairs, else the firmware's.
    m.def(
        "restore",
        [](nb::bytes text, nb::object lists) -> nb::object {
            std::vector<uint8_t> t(text.c_str(), text.c_str() + text.size());
            std::vector<std::tuple<std::string, uint32_t>> given;
            if (!lists.is_none()) given = nb::cast<std::vector<std::tuple<std::string, uint32_t>>>(lists);
            std::vector<o::List> wanted;
            for (const auto& l : given) wanted.push_back({std::get<0>(l).c_str(), std::get<1>(l)});
            const uint32_t n = lists.is_none() ?
                                   o::restore(t.data(), uint32_t(t.size())) :
                                   o::restore(t.data(), uint32_t(t.size()), wanted.data(), uint32_t(wanted.size()));
            if (!n) return nb::none();
            return nb::bytes(reinterpret_cast<const char*>(t.data()), n);
        },
        nb::arg("text"), nb::arg("lists") = nb::none());
}

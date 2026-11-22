// native/package_policy.hpp: a package member's destination and a stock version file (tests/test_policy.py).
#include <cstring>
#include <optional>
#include <tuple>
#include <nanobind/nanobind.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/tuple.h>
#include "native/package_policy.hpp"

namespace nb = nanobind;
using namespace xyf_core;

void bind_policy(nb::module_ m) {
    // destination(name) -> (status, role index, relative path bytes); the destination starts filled with 0xff,
    // so a path classify did not write shows as garbage, not as empty.
    m.def("destination", [](nb::bytes name) {
        package::Destination result;
        std::memset(&result, 0xff, sizeof result);
        const package::Status status = package::classify(name.c_str(), static_cast<U32>(name.size()), result);
        return std::make_tuple(static_cast<int>(status), static_cast<unsigned>(result.role),
                               nb::bytes(result.relative, strnlen(result.relative, sizeof result.relative)));
    });
    // version(text) -> (status, (four fields) or None when not ok).
    m.def("version", [](nb::bytes text) {
        package::Version version = {{0, 0, 0, 0}};
        const package::Status status =
            package::parse_version(reinterpret_cast<const Byte*>(text.data()), static_cast<U32>(text.size()), version);
        std::optional<std::tuple<U32, U32, U32, U32>> fields;
        if (status == package::Status::ok)
            fields = std::make_tuple(version.field[0], version.field[1], version.field[2], version.field[3]);
        return std::make_tuple(static_cast<int>(status), fields);
    });
}

// target/xyf_package_record.hpp: direct typed extraction of a package record, against the former JSON-to-text
// pipeline (tests/test_native_package_record.py).
#include <cstring>
#include <memory>
#include <tuple>
#include <nanobind/nanobind.h>
#include <nanobind/stl/tuple.h>
#include "target/xyf_package_record.hpp"

namespace nb = nanobind;

void bind_package_record(nb::module_ m) {
    // decode(json, direct) -> (status, the manifest's raw bytes): direct, package_record::parse; else
    // json::package_text then manifest::parse.
    m.def("decode", [](nb::bytes raw, bool direct) {
        std::unique_ptr<xyf::package_record::Workspace> work(new xyf::package_record::Workspace);
        std::memset(work.get(), 0, sizeof *work);
        const unsigned n = static_cast<unsigned>(raw.size());
        int status;
        if (direct) status = xyf::package_record::parse(raw.c_str(), n, *work);
        else {
            xyf::manifest::parse("", 0, work->manifest);
            const auto doc = xyf_core::json::parse(raw.c_str(), n, work->json, xyf_core::json::package_schema);
            unsigned length = 0;
            char text[xyf::manifest::max_text];
            status = xyf_core::json::package_text(doc, text, sizeof(text), length);
            if (!status) xyf::manifest::parse(text, length, work->manifest);
        }
        return std::make_tuple(status, nb::bytes(reinterpret_cast<const char*>(&work->manifest), sizeof work->manifest));
    });
}

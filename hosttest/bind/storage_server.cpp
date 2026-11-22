// target/storage_server.hpp's non-device part as the default build sees it: B's READ_FILE path format and the
// report block's size.
#include <nanobind/nanobind.h>
#include "hosttest/bind/glue.hpp"
#include "target/storage_server.hpp"

namespace nb = nanobind;
using namespace xy_forever::storage_server;

void bind_storage_server(nb::module_ m) {
    m.def("valid_path", [](xyf_glue::CStr path) { return valid_path(path); });
    m.def("result_bytes", [] { return unsigned(sizeof(Result)); });
}

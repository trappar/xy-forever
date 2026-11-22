// The XY Forever USB file write (Test 049; target/storage_server.cpp's non-device part with
// XYF_USB_FILE_WRITE): B's FILE_WRITE path scope and its constants.
#define XYF_USB_FILE_WRITE 1
#include <nanobind/nanobind.h>
#include "hosttest/bind/glue.hpp"
#include "target/storage_server.hpp"

namespace nb = nanobind;
using namespace xy_forever::storage_server;

void bind_file_write(nb::module_ m) {
    m.def("valid_path", [](xyf_glue::CStr path) { return valid_write_path(path); });
    m.def("item", [] { return unsigned(file_write_item); });
    m.def("open_flags", [] { return unsigned(write_open_flags); });
}

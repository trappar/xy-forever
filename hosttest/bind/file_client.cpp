// B's file client (target/file_client.cpp, non-device part): request bounds and the USB status of an outcome;
// change statuses, list entries, child paths; readdir results.
#include <cstdint>
#include <vector>
#include <nanobind/nanobind.h>
#include "hosttest/bind/glue.hpp"
#include "target/file_client.hpp"
#include "target/file_engine.hpp"

namespace nb = nanobind;
using namespace xy_forever::file_client;

void bind_file_client(nb::module_ m) {
    m.def("bound_ms", [](uint32_t op, uint32_t argument) { return bound_ms(op, argument); });
    m.def("status", [](uint32_t outcome) { return status(outcome); });
    m.def("request_ms", [] { return uint32_t(request_ms); });
    m.def("change_ms", [] { return uint32_t(change_ms); });
    m.def("change_status", [](uint32_t error) { return change_status(error); });
    m.def("rename_status", [](uint32_t error) { return rename_status(error); });
    // list_entry(dirent (0x10c bytes), have_record, record (0x34 bytes), room) -> the entry bytes (b'': no room)
    m.def("list_entry", [](nb::bytes entry, bool have, nb::bytes record, uint32_t room) {
        if (entry.size() < 0x10c || record.size() < 0x34) throw nb::value_error("dirent 0x10c, record 0x34 bytes");
        std::vector<uint8_t> out(room ? room : 1);
        const uint32_t n = list_entry(static_cast<const uint8_t*>(entry.data()), have,
                                      static_cast<const uint8_t*>(record.data()), out.data(), room);
        return nb::bytes(reinterpret_cast<const char*>(out.data()), n);
    });
    // child_path(folder, name) -> the path, or None when it does not fit
    m.def("child_path", [](xyf_glue::CStr folder, xyf_glue::CStr name) -> nb::object {
        char out[xy_forever::file_engine::max_path];
        if (!child_path(folder, name, out)) return nb::none();
        return nb::bytes(out);
    });
    m.def("readdir_result", [](int32_t value) { return readdir_result(value); });
}

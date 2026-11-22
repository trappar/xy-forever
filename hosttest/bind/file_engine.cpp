// The file engine's path policy and request decision (target/file_engine.cpp). Paths: bytes (the C string up to
// its first NUL), str, or None (no pointer).
#include <cstdint>
#include <cstring>
#include <nanobind/nanobind.h>
#include <nanobind/stl/tuple.h>
#include "hosttest/bind/glue.hpp"
#include "target/file_engine.hpp"

namespace nb = nanobind;
using namespace xy_forever::file_engine;

namespace {

const State open_window(unsigned window) { return State{1, 1, window, 1, 1, 1, 1, 0}; }

}  // namespace

void bind_file_engine(nb::module_ m) {
    m.def("decide", [](uint32_t op, xyf_glue::CStr path, xyf_glue::CStr second) {
        return unsigned(decide_engine(static_cast<Operation>(op), path, second));
    });
    m.def("request", [](uint32_t vtable, uint32_t op, uint32_t argument, uint32_t pointer, xyf_glue::CStr path, unsigned on,
                        unsigned session, unsigned window, unsigned fd_known, unsigned fd_space, unsigned dir_known,
                        unsigned dir_space, unsigned partition) {
        const State state = {on, session, window, fd_known, fd_space, dir_known, dir_space, partition};
        return unsigned(decide_request(vtable, op, argument, pointer, path, state));
    });
    // path 0x21 rename_to / rename_over with the armed source (None: none armed)
    m.def("rename_to", [](xyf_glue::CStr source, xyf_glue::CStr target, unsigned window) {
        return unsigned(decide_request(path_class, 0x21, rename_to, path_buffer, target, open_window(window),
                                       source));
    });
    m.def("rename_over", [](xyf_glue::CStr source, xyf_glue::CStr target, unsigned window) {
        return unsigned(decide_request(path_class, 0x21, rename_over, path_buffer, target, open_window(window),
                                       source));
    });
    // path 0x21 rename_upload (#144), as A splits it
    m.def("rename_upload", [](xyf_glue::CStr path, unsigned window) {
        char target[max_path], source[max_path];
        const char* p = path;
        for (unsigned i = 0; i != max_path; ++i) if (!(target[i] = p[i])) break;
        const bool split = split_upload(target, source);
        return unsigned(decide_request(path_class, 0x21, rename_upload, path_buffer, target, open_window(window),
                                       split ? source : 0));
    });
    // join_upload(source, target) -> the joined path, or None
    m.def("join_upload", [](xyf_glue::CStr source, xyf_glue::CStr target) -> nb::object {
        char out[max_path];
        if (!join_upload(source, target, out)) return nb::none();
        return nb::bytes(out);
    });
    // split_upload(path) -> (target, source), or None
    m.def("split_upload", [](nb::bytes path) -> nb::object {
        char buffer[max_path] = {}, source[max_path] = {};
        if (path.size() >= max_path) throw nb::value_error("path over max_path");
        std::memcpy(buffer, path.data(), path.size());
        if (!split_upload(buffer, source)) return nb::none();
        return nb::make_tuple(nb::bytes(buffer), nb::bytes(source));
    });
    // upload_size(path) -> the size its name states, or None
    m.def("upload_size", [](xyf_glue::CStr path) -> nb::object {
        uint32_t n = 0;
        if (!upload_size(path, n)) return nb::none();
        return nb::int_(n);
    });
    m.def("upload_name", [](uint32_t size, uint32_t tag) {
        char out[64];
        upload_name(out, size, tag);
        return nb::bytes(out);
    });
    m.def("raw", [](uint32_t vtable, uint32_t op) { return raw_request(vtable, op); });
    m.def("max_path", [] { return unsigned(max_path); });
    m.def("depth", [](xyf_glue::CStr path) { return unsigned(depth(path)); });
}

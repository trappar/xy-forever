// Shared helpers for the binding glue of target/ code (C strings and caller buffers as ctypes passed them).
#ifndef XYF_HOSTTEST_GLUE_HPP
#define XYF_HOSTTEST_GLUE_HPP
#include <cstddef>
#include <nanobind/nanobind.h>

namespace xyf_glue {
namespace nb = nanobind;

// A C string parameter: bytes, str or None (a null pointer), valid for the call.
struct CStr {
    const char* p;
    operator const char*() const { return p; }
};

// A C string result as ctypes' c_char_p gave it: null -> None, else bytes.
inline nb::object bytes_or_none(const char* s) {
    if (!s) return nb::none();
    return nb::bytes(s);
}

// A caller's buffer (bytearray, array.array, memoryview, ...) for the C++ to read or write in place.
class Buffer {
public:
    Buffer(nb::handle h, bool writable) {
        if (PyObject_GetBuffer(h.ptr(), &view_, writable ? PyBUF_WRITABLE | PyBUF_C_CONTIGUOUS : PyBUF_C_CONTIGUOUS))
            throw nb::python_error();
    }
    ~Buffer() { PyBuffer_Release(&view_); }
    Buffer(const Buffer&) = delete;
    Buffer& operator=(const Buffer&) = delete;
    template <class T> T* data() const { return static_cast<T*>(view_.buf); }
    size_t bytes() const { return static_cast<size_t>(view_.len); }

private:
    Py_buffer view_;
};

}  // namespace xyf_glue

namespace nanobind { namespace detail {
template <> struct has_arg_defaults<xyf_glue::CStr> : std::true_type {};   // None: a null pointer
template <> struct type_caster<xyf_glue::CStr> {
    NB_TYPE_CASTER(xyf_glue::CStr, const_name("bytes | str"))
    bool from_python(handle src, uint32_t, cleanup_list*) noexcept {
        if (src.is_none()) { value.p = nullptr; return true; }
        if (PyBytes_Check(src.ptr())) { value.p = PyBytes_AsString(src.ptr()); return true; }
        if (PyUnicode_Check(src.ptr())) {
            value.p = PyUnicode_AsUTF8(src.ptr());
            if (!value.p) PyErr_Clear();
            return value.p != nullptr;
        }
        return false;
    }
    static handle from_cpp(xyf_glue::CStr s, rv_policy, cleanup_list*) noexcept {
        return s.p ? PyBytes_FromString(s.p) : none().release();
    }
};
} }
#endif

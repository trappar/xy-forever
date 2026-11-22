// Helpers for the glue: an output buffer with guard bytes, and bytes <-> pointers.
#ifndef XYF_HOSTTEST_GUARDED_HPP
#define XYF_HOSTTEST_GUARDED_HPP
#include <tuple>
#include <vector>
#include <nanobind/nanobind.h>

namespace xyf_glue {

namespace nb = nanobind;
typedef unsigned char Byte;

inline const Byte* bytes_of(const nb::bytes& b) { return reinterpret_cast<const Byte*>(b.data()); }
inline nb::bytes as_bytes(const Byte* b, size_t n) { return nb::bytes(reinterpret_cast<const char*>(b), n); }
// data's bytes, or a null pointer for None (data must then be bytes or None).
inline const Byte* optional_bytes(const nb::object& data, nb::bytes& hold) {
    if (data.is_none()) return nullptr;
    hold = nb::cast<nb::bytes>(data);
    return bytes_of(hold);
}

// Runs write(out, capacity, produced) on an output of capacity bytes with 16 guard bytes on each side, every byte
// 0xA5 before the call and produced 0xFFFFFFFF: (status, produced, the whole buffer, guards included).
template <class Write> std::tuple<int, unsigned, nb::bytes> guarded(unsigned capacity, Write write) {
    std::vector<Byte> buffer(capacity + 32u, 0xA5);
    unsigned produced = 0xFFFFFFFFu;
    const int status = static_cast<int>(write(buffer.data() + 16, capacity, produced));
    return std::make_tuple(status, produced, as_bytes(buffer.data(), buffer.size()));
}

}  // namespace xyf_glue
#endif

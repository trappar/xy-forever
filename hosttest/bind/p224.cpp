// native/p224.hpp and native/tfw_signature.hpp: P-224 verification and the .tfw header check (tests/test_p224.py).
#include <chrono>
#include <nanobind/nanobind.h>
#include "native/tfw_signature.hpp"

namespace nb = nanobind;
using namespace xyf_core;

namespace {

const Byte* sized(const nb::bytes& b, size_t size, const char* what) {
    if (b.size() != size) throw nb::value_error(what);
    return reinterpret_cast<const Byte*>(b.data());
}

}  // namespace

void bind_p224(nb::module_ m) {
    // verify(key (56 bytes, or None), digest, r, s (28 bytes each)) -> p224::Status.
    m.def("verify", [](nb::object key, nb::bytes digest, nb::bytes r, nb::bytes s) {
        nb::bytes k;
        if (!key.is_none()) k = nb::cast<nb::bytes>(key);
        return static_cast<int>(p224::verify(key.is_none() ? nullptr : sized(k, 56, "key: 56 bytes"),
                                             reinterpret_cast<const Byte*>(digest.data()),
                                             static_cast<U32>(digest.size()), sized(r, 28, "r: 28 bytes"),
                                             sized(s, 28, "s: 28 bytes")));
    }, nb::arg("key").none(), nb::arg("digest"), nb::arg("r"), nb::arg("s"));
    // verify_header(header, size (at most its length), key (56 bytes)) -> tfw::Signature.
    m.def("verify_header", [](nb::bytes header, unsigned size, nb::bytes key) {
        if (size > header.size()) throw nb::value_error("size past the header");
        return static_cast<int>(tfw::verify_header(reinterpret_cast<const Byte*>(header.data()), size,
                                                   sized(key, 56, "key: 56 bytes")));
    });
    // signed_digest(header (0x300 bytes)) -> the 32-byte digest the signature covers.
    m.def("signed_digest", [](nb::bytes header) {
        hash::Digest digest;
        tfw::signed_digest(sized(header, 0x300, "header: 0x300 bytes"), digest);
        return nb::bytes(reinterpret_cast<const char*>(digest.bytes), 32);
    });
    // time(key, digest (32 bytes), r, s, count) -> microseconds per verification (-1: one was not valid).
    m.def("time", [](nb::bytes key, nb::bytes digest, nb::bytes r, nb::bytes s, unsigned count) {
        const Byte *k = sized(key, 56, "key: 56 bytes"), *d = sized(digest, 32, "digest: 32 bytes"),
                   *rb = sized(r, 28, "r: 28 bytes"), *sb = sized(s, 28, "s: 28 bytes");
        const auto start = std::chrono::steady_clock::now();
        for (unsigned i = 0; i < count; ++i)
            if (p224::verify(k, d, 32, rb, sb) != p224::Status::valid) return -1.0;
        return std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - start).count() / count;
    });
}

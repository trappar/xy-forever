#include <cstring>
#include <stdexcept>
#include <vector>
#include <nanobind/nanobind.h>
#include "native/hash.hpp"

namespace nb = nanobind;

void bind_hash(nb::module_ m) {
    // SHA-256 of data, fed chunk bytes at a time from offset bytes into a buffer (misaligned when not 0 mod 4);
    // raises when the state misbehaves (the step that did).
    m.def("sha256", [](nb::bytes data, unsigned int chunk, unsigned int offset) {
        if (!chunk || offset > 7) throw nb::value_error("chunk: at least 1; offset: 0 to 7");
        std::vector<unsigned char> buffer(data.size() + 8);
        std::memcpy(buffer.data() + offset, data.data(), data.size());
        const unsigned char* bytes = buffer.data() + offset;
        const unsigned int size = static_cast<unsigned int>(data.size());
        xyf_core::hash::Sha256 state;
        for (unsigned int at = 0; at < size;) {
            const unsigned int amount = size - at < chunk ? size - at : chunk;
            if (!state.update(bytes + at, amount)) throw std::runtime_error("update failed");
            at += amount;
        }
        xyf_core::hash::Digest digest;
        if (!state.finish(digest)) throw std::runtime_error("finish failed");
        if (state.update(0, 0) || state.finish(digest)) throw std::runtime_error("a finished state was reused");
        return nb::bytes(reinterpret_cast<const char*>(digest.bytes), 32);
    }, nb::arg("data"), nb::arg("chunk"), nb::arg("offset") = 0);
}

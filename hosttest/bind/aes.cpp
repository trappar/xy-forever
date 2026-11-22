#include <stdexcept>
#include <nanobind/nanobind.h>
#include "native/aes.hpp"

namespace nb = nanobind;

void bind_aes(nb::module_ m) {
    // AES-256 decrypt of one block; raises when a cipher not yet keyed decrypts, or an erased one still does.
    m.def("decrypt", [](nb::bytes key, nb::bytes input) {
        if (key.size() != 32 || input.size() != 16) throw nb::value_error("key: 32 bytes, input: 16");
        const unsigned char* in = static_cast<const unsigned char*>(input.data());
        unsigned char output[16] = {};
        xyf_core::crypto::Aes256 aes;
        if (aes.decrypt(in, output)) throw std::runtime_error("decrypted before a key");
        if (!aes.initialize(static_cast<const unsigned char*>(key.data())) || !aes.decrypt(in, output))
            throw std::runtime_error("decrypt failed");
        nb::bytes plain(reinterpret_cast<const char*>(output), 16);
        aes.erase();
        if (aes.decrypt(in, output)) throw std::runtime_error("decrypted after erase");
        return plain;
    });
}

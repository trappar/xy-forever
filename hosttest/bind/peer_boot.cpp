// native/peer_boot.hpp: the stock storage request codecs and the owner (Server) and peer (Client) state machines
// over bytearrays the test keeps and changes (tests/test_peer_boot.py).
#include <cstring>
#include <tuple>
#include <vector>
#include <nanobind/nanobind.h>
#include <nanobind/stl/tuple.h>
#include "native/peer_boot.hpp"

namespace nb = nanobind;
using namespace xyf_core;

namespace {

const Byte* bytes_of(const nb::bytes& b) { return reinterpret_cast<const Byte*>(b.data()); }
Byte* bytes_of(nb::bytearray& b) { return static_cast<Byte*>(b.data()); }
nb::bytes as_bytes(const Byte* b, size_t n) { return nb::bytes(reinterpret_cast<const char*>(b), n); }

hash::Digest digest_of(const nb::bytes& b) {
    if (b.size() != 32) throw nb::value_error("digest: 32 bytes");
    hash::Digest digest;
    std::memcpy(digest.bytes, b.data(), 32);
    return digest;
}

// The image the owner serves: a bytearray the test may change while the server holds it.
struct Server {
    peer::Server server;
    nb::object image;
    const Byte* hold(nb::bytearray source, unsigned size) {
        if (size > source.size()) throw nb::value_error("size past the image");
        image = source;
        return bytes_of(source);
    }
};

struct Client {
    peer::Client client;
    nb::object destination;
};

// Runs encode into a copy of out (its first capacity bytes the output): (status, produced, out after).
template<class Encode> std::tuple<int, U32, nb::bytes> into(nb::bytes out, unsigned capacity, Encode encode) {
    if (capacity > out.size()) throw nb::value_error("capacity past the buffer");
    std::vector<Byte> buffer(bytes_of(out), bytes_of(out) + out.size());
    U32 produced = 0xffffffffu;
    const int status = static_cast<int>(encode(buffer.data(), produced));
    return std::make_tuple(status, produced, as_bytes(buffer.data(), buffer.size()));
}

}  // namespace

void bind_peer_boot(nb::module_ m) {
    // encode_command(operation, a, b, sequence, out, capacity) -> (status, produced, out after).
    m.def("encode_command", [](U32 operation, U32 a, U32 b, U32 sequence, nb::bytes out, unsigned capacity) {
        const peer::Request r = {operation, a, b};
        return into(out, capacity, [&](Byte* o, U32& n) { return peer::encode_command(r, sequence, o, capacity, n); });
    });
    // decode_command(bytes) -> (status, operation, a, b, sequence).
    m.def("decode_command", [](nb::bytes in) {
        peer::Request r = {0, 0, 0};
        U32 sequence = 0;
        const int status =
            static_cast<int>(peer::decode_command(bytes_of(in), static_cast<U32>(in.size()), r, sequence));
        return std::make_tuple(status, r.operation, r.argument0, r.argument1, sequence);
    });
    // encode_result(operation, sequence, result, out, capacity) -> (status, produced, out after).
    m.def("encode_result", [](U32 operation, U32 sequence, U32 result, nb::bytes out, unsigned capacity) {
        return into(out, capacity,
                    [&](Byte* o, U32& n) { return peer::encode_result(operation, sequence, result, o, capacity, n); });
    });
    // decode_result(bytes, operation, sequence) -> (status, result).
    m.def("decode_result", [](nb::bytes in, U32 operation, U32 sequence) {
        U32 result = 0;
        const int status = static_cast<int>(
            peer::decode_result(bytes_of(in), static_cast<U32>(in.size()), operation, sequence, result));
        return std::make_tuple(status, result);
    });

    nb::class_<Server>(m, "Server")
        .def(nb::init<>())
        .def("begin", [](Server& s) { s.server.begin(); })
        .def("freeze",
             [](Server& s, nb::bytearray image, unsigned size, nb::bytes digest) {
                 return static_cast<int>(s.server.freeze(s.hold(image, size), size, digest_of(digest)));
             })
        // freeze_hashing(image, size) -> (status, the digest it wrote).
        .def("freeze_hashing",
             [](Server& s, nb::bytearray image, unsigned size) {
                 hash::Digest digest;
                 std::memset(digest.bytes, 0, 32);
                 const int status = static_cast<int>(s.server.freeze_hashing(s.hold(image, size), size, digest));
                 return std::make_tuple(status, as_bytes(digest.bytes, 32));
             })
        .def("freeze_unhashed",
             [](Server& s, nb::bytearray image, unsigned size, U32 crc) {
                 return static_cast<int>(s.server.freeze_unhashed(s.hold(image, size), size, crc));
             })
        // process(operation, a, b, capacity) -> (status, produced, guarded): guarded is the output with 16 bytes
        // of 0xA5 on each side, all 0xA5 before the call.
        .def("process",
             [](Server& s, U32 operation, U32 a, U32 b, unsigned capacity) {
                 std::vector<Byte> guard(capacity + 32, 0xA5);
                 const peer::Request r = {operation, a, b};
                 U32 produced = 0xffffffffu;
                 const int status = static_cast<int>(s.server.process(r, guard.data() + 16, capacity, produced));
                 return std::make_tuple(status, produced, as_bytes(guard.data(), guard.size()));
             })
        .def("phase", [](Server& s) { return static_cast<int>(s.server.phase()); })
        .def("ready", [](Server& s) { return s.server.ready(); });

    nb::class_<Client>(m, "Client")
        .def(nb::init<>())
        .def(
            "init",
            [](Client& c, nb::bytearray destination, unsigned capacity, bool hashed) {
                if (capacity > destination.size()) throw nb::value_error("capacity past the destination");
                c.destination = destination;
                return static_cast<int>(c.client.initialize(bytes_of(destination), capacity, hashed));
            },
            nb::arg("destination"), nb::arg("capacity"), nb::arg("hashed") = true)
        // request() -> (status, operation, a, b).
        .def("request",
             [](Client& c) {
                 peer::Request r = {0, 0, 0};
                 const int status = static_cast<int>(c.client.request(r));
                 return std::make_tuple(status, r.operation, r.argument0, r.argument1);
             })
        // accept(device result, data or None).
        .def(
            "accept",
            [](Client& c, U32 result, nb::object data) {
                if (data.is_none()) return static_cast<int>(c.client.accept(result, nullptr, 0));
                nb::bytes b = nb::cast<nb::bytes>(data);
                return static_cast<int>(c.client.accept(result, bytes_of(b), static_cast<U32>(b.size())));
            },
            nb::arg("result"), nb::arg("data").none())
        .def("admit", [](Client& c, nb::bytes digest) { return static_cast<int>(c.client.admit(digest_of(digest))); })
        .def("phase", [](Client& c) { return static_cast<int>(c.client.phase()); })
        .def("ready", [](Client& c) { return c.client.ready(); })
        .def("image_size", [](Client& c) { return c.client.image_size(); })
        .def("digest", [](Client& c) { return as_bytes(c.client.digest().bytes, 32); })
        .def("crc", [](Client& c) { return c.client.crc(); });
}

// native/companions.hpp: the Cypress and Nordic codecs and the Cypress session (tests/test_native_companions.py).
// Outputs are guarded (guarded.hpp): (status, produced, buffer with 16 guard bytes each side).
#include <cstring>
#include <new>
#include <optional>
#include <string>
#include <tuple>
#include <vector>
#include <nanobind/nanobind.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/vector.h>
#include "guarded.hpp"
#include "native/companions.hpp"

namespace nb = nanobind;
using namespace xyf_core;
using namespace xyf_core::companion;
using xyf_glue::as_bytes;
using xyf_glue::bytes_of;
using xyf_glue::guarded;

namespace {

typedef std::tuple<U32, U32, U32, U32> Version;

// A session over copies of its rows, which it points into; constructed over storage first filled with 0xA5.
struct Session {
    alignas(CypressSession) Byte storage[sizeof(CypressSession)];
    CypressSession& session;
    std::vector<std::string> data;
    std::vector<CypressRow> rows;
    Session() : session(*construct(storage)) {}
    static CypressSession* construct(Byte* at) {
        std::memset(at, 0xA5, sizeof(CypressSession));
        return new (at) CypressSession;
    }
};

}  // namespace

void bind_companions(nb::module_ m) {
    // encode(command, payload or None, size, capacity).
    m.def(
        "encode",
        [](U32 command, nb::object payload, U32 size, unsigned capacity) {
            nb::bytes hold;
            const Byte* data = xyf_glue::optional_bytes(payload, hold);
            if (data && size > hold.size()) throw nb::value_error("size past the payload");
            return guarded(capacity,
                           [&](Byte* o, U32 c, U32& n) { return cypress_encode(command, data, size, o, c, n); });
        },
        nb::arg("command"), nb::arg("payload").none(), nb::arg("size"), nb::arg("capacity") = 512);
    // decode(packet, expected) -> (status, the payload's offset in packet, or None for a null payload); the
    // payload pointer starts non-null.
    m.def("decode", [](nb::bytes packet, U32 expected) {
        const Byte* payload = reinterpret_cast<const Byte*>(1);
        const int status =
            static_cast<int>(cypress_decode(bytes_of(packet), static_cast<U32>(packet.size()), expected, payload));
        std::optional<long> offset;
        if (payload) offset = static_cast<long>(payload - bytes_of(packet));
        return std::make_tuple(status, offset);
    });
    // crc(data or None, size, initial) -> (status, crc); the result starts at 1.
    m.def(
        "crc",
        [](nb::object data, U32 size, U32 initial) {
            nb::bytes hold;
            const Byte* bytes = xyf_glue::optional_bytes(data, hold);
            if (bytes && size > hold.size()) throw nb::value_error("size past the data");
            U32 result = 1;
            const int status = static_cast<int>(crc16_ccitt(bytes, size, initial, result));
            return std::make_tuple(status, result);
        },
        nb::arg("data").none(), nb::arg("size"), nb::arg("initial"));
    m.def(
        "ble_header",
        [](U32 size, Version version, U32 crc, U32 device, unsigned capacity) {
            const U32 v[4] = {std::get<0>(version), std::get<1>(version), std::get<2>(version), std::get<3>(version)};
            return guarded(capacity, [&](Byte* o, U32 c, U32& n) { return ble_header(size, v, crc, device, o, c, n); });
        },
        nb::arg("size"), nb::arg("version"), nb::arg("crc"), nb::arg("device"), nb::arg("capacity") = 512);
    m.def(
        "ble_start",
        [](Version version, U32 kind, U32 device, unsigned capacity) {
            const U32 v[4] = {std::get<0>(version), std::get<1>(version), std::get<2>(version), std::get<3>(version)};
            return guarded(capacity, [&](Byte* o, U32 c, U32& n) { return ble_start(v, kind, device, o, c, n); });
        },
        nb::arg("version"), nb::arg("kind"), nb::arg("device"), nb::arg("capacity") = 512);
    // chunk(image, size, offset, capacity) -> (status, produced, consumed, guarded buffer); the image is zero-padded
    // to size when size is past it, produced and consumed start at 123 and 456.
    m.def("ble_chunk", [](nb::bytes image, U32 size, U32 offset, unsigned capacity) {
        std::vector<Byte> source(bytes_of(image), bytes_of(image) + image.size());
        if (source.size() < size) source.resize(size);
        U32 consumed = 456;
        auto out = guarded(capacity, [&](Byte* o, U32 c, U32& n) {
            n = 123;
            return ble_chunk(source.data(), size, offset, o, c, n, consumed);
        });
        return std::make_tuple(std::get<0>(out), std::get<1>(out), consumed, std::get<2>(out));
    });

    nb::class_<Session>(m, "Session")
        .def(nb::init<>())
        // init(rows: (array_id, number, data), mtu): constructs the session again, then initializes it.
        .def("init",
             [](Session& s, const std::vector<std::tuple<U32, U32, nb::bytes>>& rows, U32 mtu) {
                 Session::construct(s.storage);
                 s.data.clear();
                 s.rows.clear();
                 for (const auto& row : rows) s.data.emplace_back(std::get<2>(row).c_str(), std::get<2>(row).size());
                 for (size_t i = 0; i < rows.size(); ++i)
                     s.rows.push_back({std::get<0>(rows[i]), std::get<1>(rows[i]),
                                       reinterpret_cast<const Byte*>(s.data[i].data()),
                                       static_cast<U32>(s.data[i].size())});
                 return static_cast<int>(s.session.initialize(s.rows.data(), static_cast<U32>(s.rows.size()), mtu));
             })
        .def(
            "request",
            [](const Session& s, unsigned capacity) {
                return guarded(capacity, [&](Byte* o, U32 c, U32& n) { return s.session.request(o, c, n); });
            },
            nb::arg("capacity") = 512)
        .def(
            "exit",
            [](const Session& s, unsigned capacity) {
                return guarded(capacity, [&](Byte* o, U32 c, U32& n) { return s.session.exit_request(o, c, n); });
            },
            nb::arg("capacity") = 512)
        .def("accept",
             [](Session& s, nb::bytes packet) {
                 return static_cast<int>(s.session.accept(bytes_of(packet), static_cast<U32>(packet.size())));
             })
        .def("phase", [](const Session& s) { return static_cast<int>(s.session.phase()); });
}

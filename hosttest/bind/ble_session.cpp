// native/ble_session.hpp: the Nordic BLE update session, its parsers, and the backplane (Cypress) wrapper
// (tests/test_ble_session.py). Outputs are guarded (guarded.hpp): (status, produced, buffer with 16 guard bytes
// each side).
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
#include "native/ble_session.hpp"

namespace nb = nanobind;
using namespace xyf_core;
using namespace xyf_core::companion;
using xyf_glue::bytes_of;
using xyf_glue::guarded;

namespace {

typedef std::tuple<U32, U32, U32, U32> Version;

// Each session is constructed over storage first filled with 0xA5, and holds copies of what it points into.
template <class T> T* construct(Byte* at) {
    std::memset(at, 0xA5, sizeof(T));
    return new (at) T;
}

struct Ble {
    alignas(BleSession) Byte storage[sizeof(BleSession)];
    BleSession& session;
    std::string image;
    std::vector<BleKnownImage> known;
    Ble() : session(*construct<BleSession>(storage)) {}
};

struct Backplane {
    alignas(BackplaneSession) Byte storage[sizeof(BackplaneSession)];
    BackplaneSession& session;
    std::vector<std::string> data;
    std::vector<CypressRow> rows;
    Backplane() : session(*construct<BackplaneSession>(storage)) {}
};

}  // namespace

void bind_ble_session(nb::module_ m) {
    m.def("known_size", []() { return static_cast<unsigned>(sizeof(BleKnownImage)); });
    // version(text) -> (status, the four fields; (9, 9, 9, 9) where not written).
    m.def("version", [](nb::bytes text) {
        U32 v[4] = {9, 9, 9, 9};
        const int status = static_cast<int>(ble_parse_version(bytes_of(text), static_cast<U32>(text.size()), v));
        return std::make_tuple(status, Version(v[0], v[1], v[2], v[3]));
    });
    // mode(metadata) -> (status, 1 when the bootloader runs); the result starts at 1.
    m.def("mode", [](nb::bytes payload) {
        bool bootloader = true;
        const int status =
            static_cast<int>(ble_mode_is_bootloader(bytes_of(payload), static_cast<U32>(payload.size()), bootloader));
        return std::make_tuple(status, bootloader ? 1u : 0u);
    });
    // word(hardware revision) -> (status, platform word); the word starts at 123.
    m.def("word", [](U32 revision) {
        U32 word = 123;
        const int status = static_cast<int>(ble_platform_word(revision, word));
        return std::make_tuple(status, word);
    });
    // cypress_entry(key, key size, capacity): guarded.
    m.def("cypress_entry", [](nb::bytes key, U32 size, unsigned capacity) {
        if (size > key.size()) throw nb::value_error("size past the key");
        return guarded(capacity, [&](Byte* o, U32 c, U32& n) { return backplane_enter_packet(bytes_of(key), size, o, c, n); });
    });

    nb::class_<Ble>(m, "BleSession")
        .def(nb::init<>())
        // init(image, known: [(sha256, version)], hardware revision).
        .def("init", [](Ble& s, nb::bytes data, const std::vector<std::tuple<nb::bytes, Version>>& known, U32 revision) {
            s.image.assign(data.c_str(), data.size());
            s.known.assign(known.size() ? known.size() : 1, BleKnownImage());
            for (size_t i = 0; i < known.size(); ++i) {
                const nb::bytes& digest = std::get<0>(known[i]);
                if (digest.size() != 32) throw nb::value_error("sha256: 32 bytes");
                std::memcpy(s.known[i].sha256, digest.data(), 32);
                const Version& v = std::get<1>(known[i]);
                const U32 fields[4] = {std::get<0>(v), std::get<1>(v), std::get<2>(v), std::get<3>(v)};
                std::memcpy(s.known[i].version, fields, sizeof fields);
            }
            return static_cast<int>(s.session.initialize(reinterpret_cast<const Byte*>(s.image.data()),
                                                         static_cast<U32>(s.image.size()), s.known.data(),
                                                         static_cast<U32>(known.size()), revision));
        })
        // request(capacity) -> (status, produced, guarded buffer, (action, command, timeout_ms, delay_us)).
        .def("request", [](const Ble& s, unsigned capacity) {
            BridgeRequest info = {BridgeAction::write, 0, 0, 0};
            auto out = guarded(capacity, [&](Byte* o, U32 c, U32& n) { return s.session.request(o, c, n, info); });
            return std::make_tuple(std::get<0>(out), std::get<1>(out), std::get<2>(out),
                                   Version(static_cast<U32>(info.action), info.command, info.timeout_ms, info.delay_us));
        }, nb::arg("capacity") = 249)
        .def("complete", [](Ble& s, bool success) { return static_cast<int>(s.session.complete_action(success)); })
        .def("accept", [](Ble& s, U32 command, nb::bytes payload) {
            return static_cast<int>(s.session.accept(command, bytes_of(payload), static_cast<U32>(payload.size())));
        })
        .def("failed", [](Ble& s) { return static_cast<int>(s.session.transport_failed()); })
        .def("abort", [](Ble& s) { return static_cast<int>(s.session.abort()); })
        .def("phase", [](const Ble& s) { return static_cast<int>(s.session.phase()); })
        .def("offset", [](const Ble& s) { return s.session.offset(); })
        .def("platform", [](const Ble& s) { return s.session.platform_word(); })
        .def("in_session", [](const Ble& s) { return s.session.in_session(); })
        .def("valid", [](const Ble& s) { return s.session.application_valid(); });

    nb::class_<Backplane>(m, "BackplaneSession")
        .def(nb::init<>())
        // init(rows: [(array_id, number, data)] or None, row count, key or None, key size).
        .def("init", [](Backplane& s, std::optional<std::vector<std::tuple<U32, U32, nb::bytes>>> rows, U32 count,
                        nb::object key, U32 key_size) {
            nb::bytes hold;
            const Byte* k = xyf_glue::optional_bytes(key, hold);
            if (k && key_size > hold.size()) throw nb::value_error("key size past the key");
            s.data.clear();
            s.rows.clear();
            if (rows) {
                if (count > rows->size()) throw nb::value_error("count past the rows");
                for (const auto& row : *rows) s.data.emplace_back(std::get<2>(row).c_str(), std::get<2>(row).size());
                for (size_t i = 0; i < rows->size(); ++i)
                    s.rows.push_back({std::get<0>((*rows)[i]), std::get<1>((*rows)[i]),
                                      reinterpret_cast<const Byte*>(s.data[i].data()),
                                      static_cast<U32>(s.data[i].size())});
            }
            return static_cast<int>(s.session.initialize(rows ? s.rows.data() : nullptr, count, k, key_size));
        }, nb::arg("rows").none(), nb::arg("count"), nb::arg("key").none(), nb::arg("key_size"))
        .def("request", [](const Backplane& s, unsigned capacity) {
            return guarded(capacity, [&](Byte* o, U32 c, U32& n) { return s.session.request(o, c, n); });
        })
        .def("accept", [](Backplane& s, nb::bytes packet) {
            return static_cast<int>(s.session.accept(bytes_of(packet), static_cast<U32>(packet.size())));
        })
        .def("exit", [](const Backplane& s, unsigned capacity) {
            return guarded(capacity, [&](Byte* o, U32 c, U32& n) { return s.session.exit_request(o, c, n); });
        })
        .def("phase", [](const Backplane& s) { return static_cast<int>(s.session.phase()); });
}

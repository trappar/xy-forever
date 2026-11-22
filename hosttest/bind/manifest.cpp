// target/xyf_manifest.hpp: the strict info.txt reader, the files.sha256 list reader, the recogniser and the
// dependency rules (tests/test_xyf_manifest.py).
#include <cstddef>
#include <cstring>
#include <string>
#include <tuple>
#include <vector>
#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/vector.h>
#include "target/xyf_manifest.hpp"
#include "target/xyf_store.hpp"

namespace nb = nanobind;
using namespace xyf::manifest;

namespace {

// A char array's text up to its first NUL.
template <size_t N> nb::bytes text(const char (&field)[N]) { return nb::bytes(field, strnlen(field, N)); }
template <size_t N> nb::bytes raw(const uint8_t (&field)[N]) { return nb::bytes(reinterpret_cast<const char*>(field), N); }
template <class T> nb::bytes raw_of(const T& value) { return nb::bytes(reinterpret_cast<const char*>(&value), sizeof value); }

// Each visited entry as: 32 digest bytes, 1 binary byte, the path, NUL; refuses entry stop_at.
struct Collect { std::string out; uint32_t stop_at; };
bool collect(void* context, const FileEntry& e) {
    Collect& c = *static_cast<Collect*>(context);
    if (e.index == c.stop_at) return false;
    c.out.append(reinterpret_cast<const char*>(e.sha256), 32);
    c.out += static_cast<char>(e.binary);
    c.out.append(e.path, e.length);
    c.out += '\0';
    return true;
}

typedef std::tuple<int, int, bool, bool> Recognised;   // kind, reason, accepted, content_set
Recognised recognised(const Recognition& r) { return Recognised(r.kind, r.reason, r.accepted, r.content_set); }

}  // namespace

void bind_manifest(nb::module_ m) {
    m.def("sizes", [](unsigned which) {
        return which == 0 ? sizeof(Manifest) : which == 1 ? sizeof(ListReader) : which == 2 ? sizeof(Recognition)
                          : sizeof(xyf::store::Info);
    });

    nb::class_<Manifest>(m, "Manifest")
        .def_prop_ro("status", [](const Manifest& x) { return x.status; })
        .def_prop_ro("type", [](const Manifest& x) { return x.type; })
        .def_prop_ro("kind", [](const Manifest& x) { return x.kind; })
        .def_prop_ro("machine", [](const Manifest& x) { return x.machine; })
        .def_prop_ro("key", [](const Manifest& x) { return x.key; })
        .def_prop_ro("line", [](const Manifest& x) { return x.line; })
        .def_prop_ro("keys", [](const Manifest& x) { return x.keys; })
        .def_prop_ro("lines", [](const Manifest& x) { return x.lines; })
        .def_prop_ro("bytes", [](const Manifest& x) { return x.bytes; })
        .def_prop_ro("crc32", [](const Manifest& x) { return x.crc32; })
        .def_prop_ro("machines", [](const Manifest& x) { return x.machines; })
        .def_prop_ro("sha256", [](const Manifest& x) { return raw(x.sha256); })
        .def_prop_ro("needs_os", [](const Manifest& x) { return raw(x.needs_os); })
        .def_prop_ro("name", [](const Manifest& x) { return text(x.name); })
        .def_prop_ro("version", [](const Manifest& x) { return text(x.version); })
        .def_prop_ro("content", [](const Manifest& x) { return text(x.content); })
        .def_prop_ro("generation", [](const Manifest& x) { return text(x.generation); })
        .def_prop_ro("profile", [](const Manifest& x) { return text(x.profile); })
        .def_prop_ro("source", [](const Manifest& x) { return text(x.source); })
        .def_prop_ro("licence", [](const Manifest& x) { return text(x.licence); })
        // The fields after the error fields (status, type, kind, machine, key, reserved, line, keys, lines), raw.
        .def("after_error_fields", [](const Manifest& x) {
            const size_t at = offsetof(Manifest, bytes);
            return nb::bytes(reinterpret_cast<const char*>(&x) + at, sizeof x - at);
        });

    // parse(text) -> (status, manifest); the manifest starts zeroed.
    m.def("parse", [](nb::bytes text_) {
        Manifest out;
        std::memset(&out, 0, sizeof out);
        const int status = parse(text_.c_str(), static_cast<uint32_t>(text_.size()), out);
        return std::make_tuple(status, out);
    });
    // legacy(text) -> (status, strict view raw, xyf_store::parse_info raw, (name, version, kind, bytes, has_bytes)
    // of the latter): the strict reader's legacy view and parse_info's fields for the same text (both zeroed first).
    m.def("legacy", [](nb::bytes text_) {
        xyf::store::Info strict, legacy;
        std::memset(&strict, 0, sizeof strict);
        std::memset(&legacy, 0, sizeof legacy);
        Manifest parsed;
        const int status = parse(text_.c_str(), static_cast<uint32_t>(text_.size()), parsed);
        legacy_info(parsed, strict);
        xyf::store::parse_info(text_.c_str(), static_cast<uint32_t>(text_.size()), legacy);
        return std::make_tuple(status, raw_of(strict), raw_of(legacy),
                               std::make_tuple(text(legacy.name), text(legacy.version), static_cast<int>(legacy.kind),
                                               legacy.bytes, legacy.has_bytes));
    });
    // list(data, chunk, stop_at) -> (status, the visited entries (see collect), count, lines): the list fed in pieces
    // of chunk bytes.
    m.def("list", [](nb::bytes data, uint32_t chunk, uint32_t stop_at) {
        ListReader r;
        list_begin(r);
        Collect c = {std::string(), stop_at};
        const char* bytes = data.c_str();
        const uint32_t length = static_cast<uint32_t>(data.size());
        int status = list_ok;
        for (uint32_t at = 0; at < length && status == list_ok; at += chunk)
            status = list_feed(r, bytes + at, length - at < chunk ? length - at : chunk, collect, &c);
        if (status == list_ok) status = list_end(r);
        return std::make_tuple(status, nb::bytes(c.out.data(), c.out.size()), r.count, r.lines);
    });
    m.def("valid_name", [](nb::bytes name) { return valid_name(name.c_str()); });
    // file(name, size, prefix) -> (kind, reason, accepted, content_set); prefix is copied to a buffer of its size.
    m.def("file", [](nb::bytes name, uint32_t size, nb::bytes prefix) {
        std::vector<uint8_t> handed(reinterpret_cast<const uint8_t*>(prefix.c_str()),
                                    reinterpret_cast<const uint8_t*>(prefix.c_str()) + prefix.size());
        Recognition out = {};
        recognise_file(name.c_str(), size, handed.data(), static_cast<uint32_t>(handed.size()), out);
        return recognised(out);
    });
    // folder(name, names, manifest or None, vendor_pack) -> (kind, reason, accepted, content_set).
    m.def("folder", [](nb::bytes name, const std::vector<nb::bytes>& names, const Manifest* info, bool vendor_pack) {
        std::vector<const char*> array(names.size() ? names.size() : 1, nullptr);
        for (size_t i = 0; i < names.size(); ++i) array[i] = names[i].c_str();
        Recognition out = {};
        recognise_folder(name.c_str(), array.data(), static_cast<uint32_t>(names.size()), info, out, vendor_pack);
        return recognised(out);
    }, nb::arg("name"), nb::arg("names"), nb::arg("info").none(), nb::arg("vendor_pack"));
    m.def("check_os", [](const Manifest& a, const Manifest& b) { return static_cast<int>(check_os(a, b)); });
    m.def("check_mod", [](const Manifest& a, const Manifest& b) { return static_cast<int>(check_mod(a, b)); });
    m.def("check_assembled", [](const Manifest& a, const Manifest& b) { return static_cast<int>(check_assembled(a, b)); });
    // check_apply(entry, base, mods, count) -> (dependency, the failing mod's index).
    m.def("check_apply", [](const Manifest& entry, const Manifest& base, const std::vector<const Manifest*>& mods,
                            uint32_t count) {
        if (count > mods.size()) throw nb::value_error("count past the mods");
        uint32_t failed = 0;
        const int d = static_cast<int>(check_apply(entry, base, mods.data(), count, failed));
        return std::make_tuple(d, failed);
    });
    m.def("needs", [](const Manifest& a, const Manifest& b) { return needs(a, b); });
}

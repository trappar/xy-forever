// The USB service's host-checkable parts (XYF_USB_SERVICE): the frame codec (target/usb_proto.hpp), b's install
// allowlist (storage_server.hpp), the image and boot header checks, the stock image constants, the report size.
#define XYF_USB_SERVICE 1
#include <cstdint>
#include <vector>
#include <nanobind/nanobind.h>
#include <nanobind/stl/tuple.h>
#include "hosttest/bind/glue.hpp"
#include "target/storage_server.hpp"
#include "target/usb_proto.hpp"
#include "target/usb_service.hpp"

namespace nb = nanobind;
using namespace xyf::usbp;

namespace {
uint8_t frame_buffer[max_frame];
Parser parser;
const uint8_t* bytes_of(const nb::bytes& data) { return static_cast<const uint8_t*>(data.data()); }
nb::bytes as_bytes(const uint8_t* p, size_t n) { return nb::bytes(reinterpret_cast<const char*>(p), n); }
}  // namespace

void bind_usb_service(nb::module_ m) {
    // encode(type, seq, payload, capacity) -> the frame (b'': refused; capacity 0 passes no buffer)
    m.def("encode", [](unsigned type, unsigned seq, nb::bytes payload, unsigned capacity) {
        std::vector<uint8_t> out(capacity);
        const unsigned n = encode(uint8_t(type), uint8_t(seq), bytes_of(payload), unsigned(payload.size()),
                                  capacity ? out.data() : nullptr, capacity);
        return as_bytes(out.data(), n);
    });
    m.def("reset", [] { reset(parser, frame_buffer); });
    // feed(data) -> (frames completed, type, seq, payload, bad_crc, dropped): the last frame's fields
    m.def("feed", [](nb::bytes data) {
        unsigned frames = 0, type = 0, seq = 0;
        std::vector<uint8_t> payload;
        const uint8_t* p = bytes_of(data);
        for (size_t i = 0; i != data.size(); ++i) {
            if (!feed(parser, p[i])) continue;
            ++frames;
            type = frame_type(parser);
            seq = frame_seq(parser);
            payload.assign(frame_payload(parser), frame_payload(parser) + frame_length(parser));
            next(parser);
        }
        return std::make_tuple(frames, type, seq, as_bytes(payload.data(), payload.size()), unsigned(parser.bad_crc),
                               unsigned(parser.dropped));
    });
    m.def("install_decide", [](uint32_t vtable, uint32_t op, uint32_t argument, uint32_t selected) {
        return unsigned(xy_forever::storage_server::decide_install(vtable, op, argument, selected));
    });
    m.def("has_descriptor", [](nb::bytes data) {
        return xy_forever::usb_service::has_descriptor(bytes_of(data), uint32_t(data.size()));
    });
    m.def("header_matches", [](nb::bytes sector, uint32_t size, uint32_t crc) {
        return xy_forever::usb_service::header_matches(bytes_of(sector), size, crc);
    });
    // accept_image(uninstall, descriptor, size, sha256 (None: no pointer))
    m.def("accept_image", [](unsigned uninstall, unsigned descriptor, uint32_t size, xyf_glue::CStr sha) {
        return xy_forever::usb_service::accept_image(uninstall != 0, descriptor != 0, size,
                                                     reinterpret_cast<const uint8_t*>(sha.p));
    });
    m.def("stock_sha", [] { return as_bytes(xy_forever::usb_service::stock_os_sha256, 32); });
    m.def("stock_bytes", [] { return unsigned(xy_forever::usb_service::stock_os_bytes); });
    m.def("result_bytes", [] { return unsigned(sizeof(xy_forever::usb_service::Result)); });
}

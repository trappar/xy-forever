// Host bridge: USB frame codec, install allowlist and image/header checks
// (device C++, non-device parts). Not linked into the device.
#include "../target/usb_proto.hpp"
#include "../target/usb_service.hpp"
#include "../target/storage_server.hpp"
using namespace xyf::usbp;
static uint8_t frame_buffer[max_frame];
static Parser parser;
extern "C" {
unsigned usb_encode(unsigned type, unsigned seq, const uint8_t* payload, unsigned length, uint8_t* out, unsigned cap) {
    return encode(uint8_t(type), uint8_t(seq), payload, length, out, cap);
}
void usb_reset() { reset(parser, frame_buffer); }
// Feeds bytes; returns the number of frames completed; the last frame is copied out.
unsigned usb_feed(const uint8_t* data, unsigned n, unsigned* type, unsigned* seq, uint8_t* payload, unsigned* length,
                  unsigned* bad_crc, unsigned* dropped) {
    unsigned frames = 0;
    for (unsigned i = 0; i != n; ++i) {
        if (!feed(parser, data[i])) continue;
        ++frames;
        *type = frame_type(parser); *seq = frame_seq(parser); *length = frame_length(parser);
        for (unsigned k = 0; k != *length; ++k) payload[k] = frame_payload(parser)[k];
        next(parser);
    }
    *bad_crc = parser.bad_crc; *dropped = parser.dropped;
    return frames;
}
unsigned install_decide(unsigned vtable, unsigned op, unsigned argument, unsigned selected) {
    return xy_forever::storage_server::decide_install(vtable, op, argument, selected);
}
int image_has_descriptor(const uint8_t* data, unsigned size) { return xy_forever::usb_service::has_descriptor(data, size); }
int boot_header_matches(const uint8_t* sector, unsigned size, unsigned crc) {
    return xy_forever::usb_service::header_matches(sector, size, crc);
}
int image_accept(int uninstall, int descriptor, unsigned size, const uint8_t* sha) {
    return xy_forever::usb_service::accept_image(uninstall != 0, descriptor != 0, size, sha);
}
const uint8_t* stock_sha() { return xy_forever::usb_service::stock_os_sha256; }
unsigned stock_bytes() { return xy_forever::usb_service::stock_os_bytes; }
unsigned usb_result_bytes() { return sizeof(xy_forever::usb_service::Result); }
}

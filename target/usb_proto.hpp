#ifndef XYF_USB_PROTO_HPP
#define XYF_USB_PROTO_HPP
#include <stdint.h>
// XY Forever USB service protocol (CDC serial, processor b). Host tool:
// xyf_host.py. Frame (little endian):
//   'X' 'F' | type u8 | seq u8 | length u32 | payload[length] | crc32 u32
// crc32 (zlib) covers every byte before it. Replies use type | 0x80 and start
// their payload with a u32 status (0 = ok). The parser resynchronises on the
// next 'X' 'F' after a bad header, length or CRC, rescanning the rejected
// bytes. The parser itself has no clock: usb_service's home_pump drops a partial
// frame after idle_reset_ms of silence (an empty receive ring).
// Numbers (types, status codes, limits): usb_proto_ids.hpp, generated from
// protocol/v1.json by scripts/protogen.py (docs/PROTOCOL.md).
#include "usb_proto_ids.hpp"
namespace xyf { namespace usbp {
static const uint32_t max_frame = header_bytes + max_payload + trailer_bytes;
// Encodes one frame; returns its size, 0 if it does not fit or is too long.
uint32_t encode(uint8_t type, uint8_t seq, const uint8_t* payload, uint32_t length, uint8_t* out, uint32_t capacity);
struct Parser {
    uint8_t* buffer;      // max_frame bytes, caller-owned
    uint32_t have;        // bytes collected for the current frame
    uint32_t dropped;     // bytes skipped while resynchronising
    uint32_t bad_crc;     // frames rejected by CRC
};
void reset(Parser& parser, uint8_t* buffer);
// Feeds one byte. Returns true when a complete, CRC-valid frame is in buffer;
// read it with frame_type/seq/length/payload, then call reset() or next().
bool feed(Parser& parser, uint8_t byte);
void next(Parser& parser);
uint8_t frame_type(const Parser& parser);
uint8_t frame_seq(const Parser& parser);
uint32_t frame_length(const Parser& parser);
const uint8_t* frame_payload(const Parser& parser);
// Always inlined: an out-of-line copy gets its address hoisted into a P
// register, which the builder's straight-line call audit cannot resolve.
__attribute__((always_inline)) inline uint32_t get32(const uint8_t* p) {
    return p[0] | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}
__attribute__((always_inline)) inline void put32(uint8_t* p, uint32_t v) {
    p[0] = uint8_t(v); p[1] = uint8_t(v >> 8); p[2] = uint8_t(v >> 16); p[3] = uint8_t(v >> 24);
}
} }
#endif

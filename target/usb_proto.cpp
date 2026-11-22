#include "usb_proto.hpp"
#include "xyf_crc32.hpp"
namespace xyf { namespace usbp {
uint32_t encode(uint8_t type, uint8_t seq, const uint8_t* payload, uint32_t length, uint8_t* out, uint32_t capacity) {
    if (length > max_payload || capacity < header_bytes + length + trailer_bytes) return 0;
    out[0] = 'X';
    out[1] = 'F';
    out[2] = type;
    out[3] = seq;
    put32(out + 4, length);
    for (uint32_t i = 0; i != length; ++i) out[header_bytes + i] = payload[i];
    put32(out + header_bytes + length, crc::update(0, out, header_bytes + length));
    return header_bytes + length + trailer_bytes;
}

void reset(Parser& p, uint8_t* buffer) {
    p.buffer = buffer;
    p.have = 0;
    p.dropped = 0;
    p.bad_crc = 0;
}

namespace {
void drop(Parser& p, uint32_t n) {                      // remove n bytes from the front
    if (n > p.have) n = p.have;
    for (uint32_t i = n; i != p.have; ++i) p.buffer[i - n] = p.buffer[i];
    p.have -= n;
}
// Iterative (no recursion: bounded stack). Drops one byte per failed check,
// so a frame hidden inside rejected bytes is still found. True when a
// complete, CRC-valid frame starts at buffer[0].
bool settle(Parser& p) {
    for (;;) {
        if (p.have >= 1 && p.buffer[0] != 'X') {
            drop(p, 1);
            ++p.dropped;
            continue;
        }
        if (p.have >= 2 && p.buffer[1] != 'F') {
            drop(p, 1);
            ++p.dropped;
            continue;
        }
        if (p.have < header_bytes) return false;
        const uint32_t length = get32(p.buffer + 4);
        if (length > max_payload) {
            drop(p, 1);
            ++p.dropped;
            continue;
        }
        const uint32_t total = header_bytes + length + trailer_bytes;
        if (p.have < total) return false;
        if (get32(p.buffer + total - trailer_bytes) == crc::update(0, p.buffer, total - trailer_bytes)) return true;
        ++p.bad_crc;
        drop(p, 1);
    }
}
}

bool feed(Parser& p, uint8_t byte) {
    if (p.have == max_frame) {
        drop(p, 1);
        ++p.dropped;
    }
    p.buffer[p.have++] = byte;
    return settle(p);
}

void next(Parser& p) {
    if (p.have >= header_bytes) drop(p, header_bytes + get32(p.buffer + 4) + trailer_bytes);
    else p.have = 0;
}

uint8_t frame_type(const Parser& p) { return p.buffer[2]; }
uint8_t frame_seq(const Parser& p) { return p.buffer[3]; }
uint32_t frame_length(const Parser& p) { return get32(p.buffer + 4); }
const uint8_t* frame_payload(const Parser& p) { return p.buffer + header_bytes; }
}}

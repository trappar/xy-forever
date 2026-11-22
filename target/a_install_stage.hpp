#ifndef XYF_A_INSTALL_STAGE_HPP
#define XYF_A_INSTALL_STAGE_HPP
#include "usb_proto.hpp"
#include "usb_service.hpp"
#include "xyf_crc32.hpp"
#include "../native/hash.hpp"
// Product developer rehearsal only. This is RAM admission, never a boot writer.
namespace xy_forever { namespace a_install_stage {
namespace P = xyf::usbp;
using xyf_core::Byte;
using xyf_core::U32;
static const U32 version = 1, magic = 0x31534941u; // AIS1; independent version word follows
static const U32 handle = 0x41535447u;            // quarantined before every file executor
static const U32 header_bytes = 32, piece = 4096 - header_bytes;
static const U32 lease_ms = 10000, max_ms = 120000, max_bytes = usb_service::image_capacity;
enum Operation { begin = 0, chunk, finalize, digest, abort, preflight };
struct State {
    U32 token, last_token, size, done, crc, kind, active, admitted, touched, started;
    Byte expected[32], observed[32];
    void clear() { active = admitted = size = done = 0; }
    // Keep this tiny predicate inline: reusing an outlined member-call address across
    // a large dispatcher falls outside the stock-call audit's straight-line window.
    __attribute__((always_inline)) bool expired(U32 now) const {
        return active && (now - touched >= lease_ms || now - started >= max_ms);
    }
    U32 fail(U32 code) {
        clear();
        return code;
    }
    // All offsets and CRC/SHA are checked on A's own buffer. No B verdict is accepted.
    U32 apply(const Byte* p, U32 n, Byte* buffer, U32 capacity, U32 now, U32& value) {
        value = 0;
        if (expired(now)) clear();
        if (n < header_bytes || P::get32(p) != magic) return fail(P::s_bad_request);
        if (P::get32(p + 4) != version) return fail(P::s_unsupported);
        const U32 op = P::get32(p + 8), id = P::get32(p + 12), at = P::get32(p + 16);
        const U32 bytes = P::get32(p + 20), checksum = P::get32(p + 24), type = P::get32(p + 28);
        if (op == preflight) {
            if (active) return fail(P::s_busy);
            if (n != header_bytes || at || bytes || checksum || type) return fail(P::s_bad_request);
            value = version;
            return P::s_ok;
        }
        if (op == begin) {
            if (active || !id || id <= last_token) return fail(P::s_state);
            last_token = id;                         // burn even a refused begin; no replay
            if (n != 64 || at || type > 1) return fail(P::s_bad_request);
            if (bytes < 32 || bytes > max_bytes || capacity < 512 || bytes > capacity - 512)
                return fail(P::s_too_large);
            token = id;
            size = bytes;
            crc = checksum;
            kind = type;
            done = admitted = 0;
            for (U32 i = 0; i != 32; ++i) expected[i] = p[32 + i];
            active = 1;
            touched = started = now;
            return P::s_ok;
        }
        if (!active || id != token) return fail(P::s_state);
        touched = now;
        if (op == abort && n == header_bytes && !at && !bytes && !checksum && !type) {
            clear();
            return P::s_ok;
        }
        if (op == chunk) {
            if (admitted || at != done || !bytes || bytes > piece || bytes > size - done || n != header_bytes + bytes ||
                type)
                return fail(P::s_range);
            if (xyf::crc::update(0, p + header_bytes, bytes) != checksum) return fail(P::s_crc);
            for (U32 i = 0; i != bytes; ++i) buffer[done + i] = p[header_bytes + i];
            done += bytes;
            value = done;
            return P::s_ok;
        }
        if (n != header_bytes || bytes || checksum || type) return fail(P::s_bad_request);
        if (op == digest && admitted && at < 4) {
            value = P::get32(observed + at * 8);
            return P::get32(observed + at * 8 + 4);
        }
        if (op != finalize || at || admitted || done != size) return fail(P::s_state);
        if (xyf::crc::update(0, buffer, size) != crc) return fail(P::s_crc);
        xyf_core::hash::Sha256 sha;
        xyf_core::hash::Digest hash;
        if (!sha.update(buffer, size) || !sha.finish(hash)) return fail(P::s_sha);
        U32 different = 0;
        for (U32 i = 0; i != 32; ++i) {
            observed[i] = hash.bytes[i];
            different |= observed[i] ^ expected[i];
        }
        if (different) return fail(P::s_sha);
        // Exactly the existing B install/uninstall policy, independently applied to A's bytes.
        if (!usb_service::accept_image(kind != 0, usb_service::has_descriptor(buffer, size), size, observed))
            return fail(P::s_image);
        static const xyf_core::Range allowed[] = {
            {0x08000000u, 0x08100000u}, {0x11800000u, 0x11c00000u}, {0x80000000u, 0x90000000u}};
        const xyf_core::Policy policy = {allowed, 3, 0, 0};
        xyf_core::Summary summary;
        if (xyf_core::inspect(buffer, size, policy, summary) != xyf_core::Error::ok) return fail(P::s_image);
        // The stock writer's extra sector fits, even for sector-aligned input. No writer is called.
        for (U32 i = size; i != ((size >> 9) + 1) * 512; ++i) buffer[i] = 0;
        admitted = 1;
        value = crc;
        return P::s_ok;
    }
};
}}
#endif

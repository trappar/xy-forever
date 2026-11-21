#ifndef XYF_CONFIG_IO_HPP
#define XYF_CONFIG_IO_HPP
#include "file_client.hpp"
#include "xyf_config.hpp"
#include "usb_proto_ids.hpp"
#include "../native/json.hpp"

// The Mods page and mod builder use the same on-device configuration lookup.
namespace xy_forever { namespace config_io {
namespace fc = file_client;
namespace xg = xyf::config;
namespace P = xyf::usbp;
namespace json = xyf_core::json;
struct Scan { uint32_t skip, choice; };

static uint32_t transport(const fc::Reply& r, volatile uint32_t& mount) {
    if (r.outcome == fc::link) mount = fc::mounted_stale;
    return fc::status(r.outcome);
}

static uint32_t find(Scan& scan, uint8_t* raw, volatile uint32_t& mount) {
    const char* root = "/yaffs2/xy-forever";
    uint32_t code = fc::mount_ready(mount);
    if (code != P::s_ok) return code;
    if (!scan.skip) {
        uint8_t record[fc::record_bytes];
        const fc::Reply r = fc::stat(root, record);
        if (r.outcome) return transport(r, mount);
        if (r.value) return fc::change_status(r.error);
        if ((P::get32(record + 8) & 0xf000u) != 0x4000u) return P::s_storage;
    }
    // The page read closes its directory before returning, so other work can
    // open A's write window between configuration reads.
    uint32_t count = 0, more = 0, bytes = 0;
    code = fc::list(root, scan.skip, raw, json::document_bytes, count, more, bytes, mount);
    if (code != P::s_ok) return code == P::s_not_found ? P::s_storage : code;
    if (more && !count) return P::s_storage;
    for (uint32_t at = 0; at < bytes; at += 6 + raw[at + 5]) {
        const uint8_t n = raw[at + 5];
        const char* name = reinterpret_cast<const char*>(raw + at + 6);
        for (uint32_t k = 0; k != xg::path_count && (scan.choice == 0 || k + 1u < scan.choice); ++k) {
            const char* expected = xg::names[k];
            uint32_t len = 0;
            while (expected[len]) ++len;
            if (len == n && xg::same_n(name, expected, n)) scan.choice = k + 1u;
        }
    }
    scan.skip += count;
    if (more && scan.choice != 1) return P::s_busy;
    return scan.choice ? P::s_ok : P::s_not_found;
}

// Select by name, so a broken link or unreadable selected record never authorizes fallback.
static uint32_t read(Scan& scan, uint8_t* raw, uint32_t& bytes, bool& native, volatile uint32_t& mount) {
    bytes = 0;
    const uint32_t ready = fc::mount_ready(mount);
    if (ready != P::s_ok) return ready;
    const char* path = xg::path;
    uint8_t stat[fc::record_bytes];
    fc::Reply r = fc::stat(path, stat);
    if (r.outcome) return transport(r, mount);
    native = true;
    if (r.value) {
        if (r.error != fc::enoent) return fc::change_status(r.error);
        const uint32_t code = find(scan, raw, mount);
        if (code == P::s_busy) return code;
        const uint32_t chosen = scan.choice;
        scan.skip = scan.choice = 0;
        if (code != P::s_ok) return code;
        native = xg::native_record(chosen - 1u);
        path = xg::paths[chosen - 1u];
        r = fc::stat(path, stat);
        if (r.outcome) return transport(r, mount);
    }
    scan.skip = scan.choice = 0;
    if (r.value || (P::get32(stat + 8) & 0xf000u) != 0x8000u) return P::s_storage;
    const uint32_t capacity = native ? json::document_bytes : xg::max_text;
    const uint32_t status = fc::read_file(path, 0, false, raw, capacity, bytes, mount);
    return status == P::s_not_found ? P::s_storage : status;
}

} }
#endif

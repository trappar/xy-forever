#include "xyf_cold_library.hpp"
namespace xyf { namespace cold_library {
using xy_forever::cold_storage::Stock121ReadOnlySession;
namespace {
bool append(char* out, uint32_t& at, const char* text) {
    for (; *text; ++text) {
        if (at + 1 >= xy_forever::services::path_capacity) return false;
        out[at++] = *text;
    }
    out[at] = 0;
    return true;
}
}

library::Error select(Stock121ReadOnlySession& session, library::Library& lib, uint8_t* scratch, uint32_t scratch_bytes,
                      Selection& out) {
    for (uint32_t i = 0; i != sizeof(out); ++i) reinterpret_cast<char*>(&out)[i] = 0;
    out.choice = library::Choice::menu;
    if (!session.direct_ready()) return library::Error::unavailable;
    const library::Error error = library::scan_direct(session, lib, scratch, scratch_bytes);
    if (error != library::Error::ok) return error;
    lib.state.once[0] = 0;                              // see header: no "once" while read-only
    out.choice = library::boot_choice(lib, out.index);
    if (out.choice == library::Choice::menu) return library::Error::ok;
    const library::Entry& e = lib.entries[out.index];
    uint32_t at = 0;
    out.path[0] = 0;
    if (!append(out.path, at, "/yaffs2/xy-forever/firmware/") || !append(out.path, at, e.id) ||
        !append(out.path, at, "/os.ldr")) {
        out.choice = library::Choice::menu;
        return library::Error::path;
    }
    out.has_bytes = e.has_info && e.info.has_bytes;
    out.has_crc32 = e.has_info && e.info.has_crc32;
    out.bytes = e.info.bytes;
    out.crc32 = e.info.crc32;
    return library::Error::ok;
}

bool matches(const Selection& selection, uint32_t bytes, uint32_t crc32) {
    return (!selection.has_bytes || selection.bytes == bytes) && (!selection.has_crc32 || selection.crc32 == crc32);
}
}}

#ifndef XYF_COLD_LIBRARY_HPP
#define XYF_COLD_LIBRARY_HPP
#include "cold_storage.hpp"
#include "xyf_library.hpp"
// Processor a, cold read-only mount: scan the XY Forever library through the
// session's direct yaffs API and choose the firmware image to serve.
namespace xyf { namespace cold_library {
struct Selection {
    library::Choice choice;            // menu = nothing bootable (serve nothing)
    uint32_t index;
    char path[xy_forever::services::path_capacity];
    bool has_bytes, has_crc32;         // from info.txt, checked after loading
    uint32_t bytes, crc32;
};
// The read-only phase cannot clear state.txt's "once" after using it, so
// "once" is ignored until writes exist (it would otherwise stick).
// Unused since the menu boot (file_boot's entry_selection replaced it) and never
// linked, but this file's copy of the scan_direct template is the one the linker
// keeps: removing select() moves that code and changes the candidate. Remove it
// with the next device-tested build.
library::Error select(xy_forever::cold_storage::Stock121ReadOnlySession& session, library::Library& lib,
                      uint8_t* scratch, uint32_t scratch_bytes, Selection& out);
// After loading: does the image match info.txt (when it gave size/CRC)?
bool matches(const Selection& selection, uint32_t bytes, uint32_t crc32);
}}
#endif

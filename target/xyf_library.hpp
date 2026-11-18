#ifndef XYF_LIBRARY_HPP
#define XYF_LIBRARY_HPP
#include "boot_services.hpp"
#include "xyf_store.hpp"
#include "xyf_state.hpp"
#include "xyf_content.hpp"
// XY Forever firmware library (docs/STORAGE.md): scans
// /yaffs2/xy-forever/{state.txt, firmware/<id>/{info.txt,os.ldr}}
// and decides what to boot. Read-only: never creates, writes, renames or
// removes anything.
//
// The scanner is a template over the storage object so every call is a direct
// call (no function pointers): the builder's call audit rejects calls through
// registers it cannot resolve. Fs needs the direct yaffs API of the cold
// read-only mount (cold_storage::Stock121ReadOnlySession):
//   int direct_open(const char*)                 fd, <0 on failure (read-only)
//   int direct_read(int, Byte*, U32)             bytes, 0 at EOF, <0 on error
//   int direct_close(int)                        0 on success
//   U32 direct_opendir(const char*)              0 on failure
//   const Byte* direct_readdir(U32)              dirent or 0 at end
//   int direct_closedir(U32)                     0 on success
// There is no seek/stat: an image counts as present when os.ldr opens; its
// size/CRC are checked against info.txt when the chosen image is loaded.
namespace xyf { namespace library {
using xy_forever::services::Error;
static const uint32_t max_entries = 16;
// Dirent layout as decoded by Test 013: type byte at +10 (8 = regular file),
// NUL-terminated name at +11.
static const uint32_t dirent_type = 10, dirent_name = 11, dirent_regular = 8;
struct Entry {
    char id[store::max_id + 1];
    store::Info info;
    bool has_info, image_present;
    uint32_t machines;         // optional native info.txt count; no inference from external layout history
    bool content_missing;      // its content is not installed in /yaffs2/content/ (content_installed)
    bool usable;               // os.ldr present and content installed (size/CRC checked at load)
};
struct Library {
    store::State state;
    bool has_state;
    uint32_t state_source, state_seq;   // xyf::state::Source of the state used, and its seq
    Entry entries[max_entries];
    uint32_t count;            // sorted by id
    uint32_t skipped;          // invalid ids, duplicates, over capacity, missing images
    content::State content;    // what /yaffs2/content/ matches (the content state record's head)
};
// scratch: one native text file.
static const uint32_t scratch_needed = store::max_text;
enum class Choice { menu, once, by_default };
// once (if usable) > default (if usable) > menu ("XY Forever is home").
Choice boot_choice(const Library& library, uint32_t& index);
int32_t find(const Library& library, const char* id);

// THE rule for whether an entry's content is installed in /yaffs2/content/ (an entry whose
// content is not installed is listed, "content not installed", but never booted or made the default as it
// is: a mod firmware halts at boot without its content files, and a catalog naming mods the firmware lacks
// breaks the OS's menus). The content state record says what content/ matches: an assembled
// mod set (info.txt with `generation`) when the record is installed for it, every other entry when it is
// official (no record), nothing while a switch is unfinished (xyf_content.hpp).
inline bool content_installed(const content::State& state, const Entry& e) {
    return content::matches(state, e.id, e.has_info && e.info.has_generation);
}

namespace detail {
static const uint32_t path_capacity = xy_forever::services::path_capacity;
void reset(Library& out);
bool path(char* out, const char* a, const char* b = "", const char* c = "", const char* d = "");
void add_entry(Library& out, const char* name);
void sort(Library& out);
void apply_state(Library& out, state::Source source, const uint8_t* text, uint32_t length);
void apply_info(Entry& e, const uint8_t* text, uint32_t length);
void finish(Library& out, Entry& e);
extern const char root[];

template <class Fs> bool read_text(Fs& fs, const char* file, uint8_t* buffer, uint32_t capacity, uint32_t& length) {
    const int fd = fs.direct_open(file);
    if (fd < 0) return false;
    length = 0;
    bool ok = true;
    for (uint32_t guard = 0; guard != 64; ++guard) {
        const uint32_t room = capacity - length;
        if (!room) {                                   // full: must be exactly at EOF
            uint8_t probe;
            ok = fs.direct_read(fd, &probe, 1) == 0;
            break;
        }
        const int got = fs.direct_read(fd, buffer + length, room);
        if (got < 0 || uint32_t(got) > room) { ok = false; break; }
        if (!got) break;
        length += uint32_t(got);
    }
    return fs.direct_close(fd) == 0 && ok;
}
template <class Fs> bool present(Fs& fs, const char* file) {
    const int fd = fs.direct_open(file);
    return fd >= 0 && fs.direct_close(fd) == 0;
}
// Lists subdirectories; false on a close error. missing: no such directory.
template <class Fs> bool list(Fs& fs, const char* directory, Library& out, bool& missing) {
    const uint32_t dir = fs.direct_opendir(directory);
    if (!dir) { missing = true; return true; }
    for (uint32_t guard = 0; guard != 256; ++guard) {
        const uint8_t* entry = fs.direct_readdir(dir);
        if (!entry) break;
        char name[64];
        uint32_t n = 0;
        for (; n != sizeof(name) - 1 && entry[dirent_name + n]; ++n) name[n] = char(entry[dirent_name + n]);
        name[n] = 0;
        if (entry[dirent_type] != dirent_regular) add_entry(out, name);
    }
    return fs.direct_closedir(dir) == 0;
}
}

template <class Fs> Error scan_direct(Fs& fs, Library& out, uint8_t* scratch, uint32_t scratch_bytes) {
    using namespace detail;
    reset(out);
    if (!scratch || scratch_bytes < scratch_needed) return Error::capacity;
    char p[path_capacity];
    uint32_t length = 0;
    // Power-safe A/B slots first; legacy state.txt only if neither is valid.
    static const char* const names[3] = {"/state-a.txt", "/state-b.txt", "/state.txt"};
    static const state::Source sources[3] = {state::source_a, state::source_b, state::source_legacy};
    for (uint32_t k = 0; k != 3; ++k)
        if (path(p, root, names[k]) && read_text(fs, p, scratch, store::max_text, length))
            apply_state(out, sources[k], scratch, length);
    content::read_state(fs, out.content);           // before the entries (content_installed)
    if (!path(p, root, "/firmware")) return Error::path;
    bool missing = false;
    if (!list(fs, p, out, missing)) { out.count = 0; return Error::directory; }
    if (missing) return Error::ok;                 // no firmware folder yet: empty library
    sort(out);
    for (uint32_t i = 0; i != out.count; ++i) {
        Entry& e = out.entries[i];
        if (path(p, root, "/firmware/", e.id, "/info.txt") && read_text(fs, p, scratch, store::max_text, length))
            apply_info(e, scratch, length);
        e.image_present = path(p, root, "/firmware/", e.id, "/os.ldr") && present(fs, p);
        e.content_missing = !content_installed(out.content, e);
        finish(out, e);
    }
    return Error::ok;
}
} }
#endif

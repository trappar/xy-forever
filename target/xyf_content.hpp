#ifndef XYF_CONTENT_HPP
#define XYF_CONTENT_HPP
#include <stdint.h>
// What /yaffs2/content/ currently matches (the content state record, docs/storage/state.md "Content
// state"), and the one path rule for every write the content switch makes (allowed). Freestanding, header
// only: processor A reads the record's head in the library scan (xyf_library.hpp), processor B runs the
// switch (xyf_content_job.hpp). Functions are static: an inline (weak) function is a register call on Blackfin,
// which the builder's call audit rejects.
//
// The record, /yaffs2/xy-forever/content-state.txt, written as .part and renamed over:
//   state=official                           the official content (also: no record at all)
//   state=installed  entry=<id>              content/ matches library entry <id> (an assembled mod set)
//   state=switching  target=<id>             a switch started and has not finished: nothing boots
// then one `file=<path>` line per file XY Forever installed in /yaffs2/content/ (relative to it, any order).
// A's engine never renames onto an existing file, so the record is replaced in steps: record -> .old,
// .part -> record, .old removed. Only a .old (a cut between) reads as switching, never as official.
namespace xyf { namespace content {
static const char record_path[] = "/yaffs2/xy-forever/content-state.txt";
static const char record_old[] = "/yaffs2/xy-forever/content-state.old";     // the record while it is replaced
static const char saved_folder[] = "/yaffs2/xy-forever/content-official";
static const char saved_list[] = "/yaffs2/xy-forever/content-official/content.sha256";
static const char saved_catalog[] = "/yaffs2/xy-forever/content-official/op1_factory.json";
static const char root[] = "/yaffs2/content/";         // the OS's content folder
static const char catalog[] = "op1_factory.json";       // relative to root
static const uint32_t root_bytes = sizeof(root) - 1;
static const uint32_t max_path = 240;                   // bytes with the NUL (file_engine::max_path)
static const uint32_t max_rel = 160;                    // a listed path (docs/storage/packages.md files.sha256)
static const uint32_t head_bytes = 64;                  // A reads only this much of the record

enum Kind : uint32_t { official = 0, installed = 1, switching = 2 };
struct State { uint32_t kind; char entry[32]; };        // entry: for installed

static inline bool id_char(uint8_t c) { return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '_'; }
static inline bool starts(const uint8_t* t, uint32_t n, const char* s, uint32_t& at) {
    for (at = 0; s[at]; ++at) if (at >= n || t[at] != uint8_t(s[at])) return false;
    return true;
}
// The record's head. Anything but the two exact forms above is "switching" (needs repair: nothing boots).
static inline void parse(const uint8_t* t, uint32_t n, State& s) {
    s.kind = switching; s.entry[0] = 0;
    uint32_t at = 0;
    if (starts(t, n, "state=official\n", at)) { s.kind = official; return; }
    if (!starts(t, n, "state=installed\nentry=", at)) return;
    uint32_t k = 0;
    for (; at < n && k != 31 && id_char(t[at]); ++at, ++k) s.entry[k] = char(t[at]);
    s.entry[k] = 0;
    if (!k || at >= n || t[at] != '\n') { s.entry[0] = 0; return; }
    s.kind = installed;
}
// Processor A (library scan, read-only direct API): no record (and no .old) = official; only a .old, or a
// record it cannot read whole enough = switching.
template <class Fs> static void read_state(Fs& fs, State& s) {
    s.kind = official; s.entry[0] = 0;
    const int fd = fs.direct_open(record_path);
    if (fd < 0) {
        const int old = fs.direct_open(record_old);
        if (old >= 0) { fs.direct_close(old); s.kind = switching; }
        return;
    }
    uint8_t head[head_bytes];
    uint32_t n = 0;
    for (uint32_t guard = 0; guard != 8 && n != head_bytes; ++guard) {
        const int got = fs.direct_read(fd, head + n, head_bytes - n);
        if (got <= 0 || uint32_t(got) > head_bytes - n) break;
        n += uint32_t(got);
    }
    fs.direct_close(fd);
    parse(head, n, s);
}
static inline bool same(const char* a, const char* b) { while (*a && *a == *b) ++a, ++b; return *a == *b; }
// THE rule (xyf_library content_installed): an assembled mod set's content is installed when the state is
// installed for it; every other entry runs on the official content. Switching: nothing is installed.
static inline bool matches(const State& s, const char* id, bool mod_set) {
    if (s.kind == switching) return false;
    return mod_set ? s.kind == installed && same(s.entry, id) : s.kind == official;
}

// ---- the path rule: every path the content switch writes, renames onto, removes or makes
enum class Use : uint32_t {
    record,          // the record (or its .part)
    saved,           // the saved official catalog and list (or their .part), their folder
    catalog,         // /yaffs2/content/op1_factory.json (or its .part)
    install,         // a file of the target entry's files.sha256 (or its .part)
    make_folder,     // a folder for an install path
    remove_file,     // a file the record says XY Forever installed (or its .part)
    remove_folder    // a folder left empty by remove_file
};
static inline bool prefix(const char* s, const char* p) { for (; *p; ++p, ++s) if (*s != *p) return false; return true; }
static inline bool with_part(const char* path, const char* name) {      // name, or name + ".part"
    while (*name && *path == *name) ++path, ++name;
    return !*name && (!*path || same(path, ".part"));
}
// /yaffs2/..., below max_path, components neither empty nor . or .., printable ASCII without \ and :, no name
// holding "bootloader" (any case: /yaffs2/bootloader.dev is never touched).
static inline bool well_formed(const char* path) {
    if (!prefix(path, "/yaffs2/")) return false;
    uint32_t component = 8, i = 8;
    for (;; ++i) {
        if (i == max_path) return false;
        const char c = path[i];
        if (c == '/' || !c) {
            const uint32_t n = i - component;
            if (!n || (n == 1 && path[component] == '.') || (n == 2 && path[component] == '.' && path[component + 1] == '.'))
                return false;
            if (!c) break;
            component = i + 1;
        } else if (c < 0x20 || c > 0x7e || c == '\\' || c == ':') return false;
    }
    for (uint32_t k = 8; path[k]; ++k) {
        static const char word[] = "bootloader";
        uint32_t m = 0;
        while (word[m] && (path[k + m] | 0x20) == word[m]) ++m;
        if (!word[m]) return false;
    }
    return true;
}
// official: the path (relative to root, without ".part") is listed in the official content.sha256 (install,
// remove_file) or is a folder of a listed path (remove_folder). Writes: only the record, the saved official
// files and, in /yaffs2/content/, the catalog and non-official paths outside dlc/. Never user/, the boot
// partition or bootloader.dev (not under these roots, or refused by well_formed).
static inline bool allowed(Use use, const char* path, bool official) {
    if (!path || !well_formed(path)) return false;
    if (use == Use::record) return with_part(path, record_path) || same(path, record_old);
    if (use == Use::saved) return same(path, saved_folder) || with_part(path, saved_list) || with_part(path, saved_catalog);
    if (!prefix(path, root) || !path[root_bytes]) return false;
    const char* rel = path + root_bytes;
    if (use == Use::catalog) return with_part(rel, catalog);
    if (with_part(rel, catalog)) return false;
    const bool dlc = (rel[0] | 0x20) == 'd' && (rel[1] | 0x20) == 'l' && (rel[2] | 0x20) == 'c' && (!rel[3] || rel[3] == '/');
    if (dlc) return false;
    if (use == Use::make_folder) return true;           // making a folder that exists changes nothing
    return !official;
}

// ---- shasum lists (files.sha256, content.sha256): `<64 hex><space><space or *><path>` LF
struct Line { uint32_t hash, path, bytes, next; };      // offsets into the text
static inline bool hex_digit(uint8_t c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); }
// The line at `at`. False at the end (bad = false) or on a malformed line (bad = true).
static inline bool line(const uint8_t* t, uint32_t n, uint32_t at, Line& l, bool& bad) {
    bad = false;
    if (at >= n) return false;
    bad = true;
    if (n - at < 68) return false;
    for (uint32_t i = 0; i != 64; ++i) if (!hex_digit(t[at + i])) return false;
    if (t[at + 64] != ' ' || (t[at + 65] != ' ' && t[at + 65] != '*')) return false;
    uint32_t e = at + 66;
    while (e < n && t[e] != '\n') { if (t[e] < 0x20 || t[e] > 0x7e) return false; ++e; }
    if (e >= n || e == at + 66 || e - at - 66 > max_rel) return false;
    l.hash = at; l.path = at + 66; l.bytes = e - at - 66; l.next = e + 1;
    bad = false;
    return true;
}
// Bytewise order of two byte strings (LC_ALL=C sort).
static inline int order(const uint8_t* a, uint32_t an, const uint8_t* b, uint32_t bn) {
    for (uint32_t i = 0; i != an && i != bn; ++i) if (a[i] != b[i]) return a[i] < b[i] ? -1 : 1;
    return an == bn ? 0 : an < bn ? -1 : 1;
}
static inline uint32_t unhex(uint8_t c) { return c <= '9' ? uint32_t(c - '0') : uint32_t(c - 'a' + 10); }
static inline bool hash_equal(const uint8_t* hex, const uint8_t* digest) {
    uint32_t d = 0;
    for (uint32_t i = 0; i != 32; ++i) d |= (unhex(hex[2 * i]) << 4 | unhex(hex[2 * i + 1])) ^ digest[i];
    return !d;
}
} }
#endif

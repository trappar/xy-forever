#ifndef XYF_SAVE_LOG_HPP
#define XYF_SAVE_LOG_HPP
#include <stdint.h>
// 8.8 step 0: the save-time log, /yaffs2/xy-forever/log/save-times.txt. B keeps one Times while a Save (the
// Mods or Settings page), a USB build or a USB content switch runs: the wall time between laps goes to the
// part that was running (lap). At the end one line is appended inside the last write window of the sequence
// (a Save: the configuration record's; a USB build: its commit's; a switch: its own); the file keeps the last
// max_records lines. A failed log write never fails the operation. Freestanding, header only (a host test:
// tests/test_save_log.py). Line: `kind=save result=ok mods=30 memory=expanded added=1 removed=0 total=12345`
// then `<part>=<ms>` for every part (0 included, the same order every time), then the content switch's counts.
namespace xyf { namespace save_log {
static const char folder[] = "/yaffs2/xy-forever/log";
static const char path[] = "/yaffs2/xy-forever/log/save-times.txt";
static const char part_path[] = "/yaffs2/xy-forever/log/save-times.txt.part";
static const uint32_t max_records = 20, max_line = 1024, max_text = max_records * max_line;
// The parts, in the order of a Save. job_* follow native/mod_job.hpp's TimingPart (job_base + part).
enum Part : uint32_t {
    p_list = 0,       // the page's package list and saved toggles (before the build is asked for)
    p_check,          // the save's checks: base, the slot's stamp, the request
    p_cset_list,      // #183 the base's content set: the state and the listing
    p_cset_open,      // its files opened and closed
    p_cset_read,      // waiting for A's reads (each half read while the one before is hashed)
    p_cset_sha,       // hashing (and the list's lines)
    p_cset_write,     // the compare, the window, the two files written and read back, the rename
    p_job_base,       // mod job: base list, info, edits (TimingPart order from here)
    p_job_packages,
    p_job_catalog,
    p_job_stock,
    p_job_patch1,
    p_job_link1,
    p_job_zero,
    p_job_link2,
    p_job_set_module,
    p_job_patch2,
    p_job_crc,
    p_job_content,
    p_job_write,
    p_job_verify,
    p_job_check,
    p_job_finish,
    p_away,           // commit: the old entry into tmp/
    p_in,             // commit: the new entry renamed in
    p_clean,          // commit: the old entry's tree removed (or a failed job's output)
    p_close,          // the commit's write window closing (A rescans)
    p_stamp,          // the new entry's info.txt read back
    p_switch,         // the content switch's job
    p_switch_close,   // its write window closing
    p_config,         // the configuration record (and this log)
    p_count
};
static const char* const part_names[p_count] = {
    "list",     "check",   "cset_list", "cset_open", "cset_read", "cset_sha",     "cset_write", "base",
    "packages", "catalog", "stock",     "patch1",    "link1",     "zero",         "link2",      "set_module",
    "patch2",   "crc",     "content",   "write",     "verify",    "image_check",  "finish",     "away",
    "in",       "clean",   "close",     "stamp",     "switch",    "switch_close", "config"};
enum Kind : uint32_t { k_none = 0, k_save, k_build, k_switch };
struct Times {
    uint32_t kind, ok, mods, expanded, added, removed;
    uint32_t begin_ms, mark_ms, current;           // current: the part the time since mark_ms goes to
    uint32_t entry_files;                          // files the build wrote into the entry (content + 4)
    uint32_t compared, read, written, removed_files, bytes;   // the content switch (xyf_content_job.hpp)
    uint32_t ms[p_count];
};
static inline void begin(Times& t, uint32_t kind, uint32_t now, uint32_t part) {
    uint8_t* b = reinterpret_cast<uint8_t*>(&t);
    for (uint32_t i = 0; i != sizeof(Times); ++i) b[i] = 0;
    t.kind = kind;
    t.begin_ms = t.mark_ms = now;
    t.current = part;
}
static inline void lap(Times& t, uint32_t now, uint32_t part) {
    if (!t.kind) return;
    if (t.current < p_count) t.ms[t.current] += now - t.mark_ms;
    t.mark_ms = now;
    t.current = part;
}
static inline bool put(char* out, uint32_t room, uint32_t& n, const char* s) {
    for (; *s; ++s) {
        if (n + 1 >= room) return false;
        out[n++] = *s;
    }
    return true;
}
static inline bool put(char* out, uint32_t room, uint32_t& n, uint32_t v) {   // no division (no libgcc on B)
    static const uint32_t powers[10] = {1000000000u, 100000000u, 10000000u, 1000000u, 100000u,
                                        10000u,      1000u,      100u,      10u,      1u};
    bool lead = true;
    for (uint32_t i = 0; i != 10; ++i) {
        char d = '0';
        while (v >= powers[i]) {
            v -= powers[i];
            ++d;
        }
        if (d != '0' || i == 9) lead = false;
        if (!lead) {
            if (n + 1 >= room) return false;
            out[n++] = d;
        }
    }
    return true;
}
static inline bool field(char* out, uint32_t room, uint32_t& n, const char* key, uint32_t v) {
    return put(out, room, n, " ") && put(out, room, n, key) && put(out, room, n, "=") && put(out, room, n, v);
}
// The record's line (with its LF) into out; its length, 0 if it does not fit.
static inline uint32_t format(char* out, uint32_t room, const Times& t, uint32_t now) {
    static const char* const kinds[4] = {"none", "save", "build", "switch"};
    uint32_t n = 0;
    bool ok = put(out, room, n, "kind=") && put(out, room, n, kinds[t.kind < 4 ? t.kind : 0]) &&
              put(out, room, n, t.ok ? " result=ok" : " result=failed") && field(out, room, n, "mods", t.mods) &&
              put(out, room, n, t.expanded ? " memory=expanded" : " memory=standard") &&
              field(out, room, n, "added", t.added) && field(out, room, n, "removed", t.removed) &&
              field(out, room, n, "total", now - t.begin_ms);
    for (uint32_t i = 0; ok && i != p_count; ++i)
        ok = field(out, room, n, part_names[i], t.ms[i] + (i == t.current ? now - t.mark_ms : 0u));
    ok = ok && field(out, room, n, "entry_files", t.entry_files) && field(out, room, n, "compared", t.compared) &&
         field(out, room, n, "read", t.read) && field(out, room, n, "written", t.written) &&
         field(out, room, n, "removed_files", t.removed_files) && field(out, room, n, "bytes", t.bytes) && n + 1 < room;
    if (!ok) return 0;
    out[n++] = '\n';
    return n;
}
// The new file: the last max_records - 1 whole lines of old (n bytes; a text that is not lines is dropped),
// then line. In place: text holds old (old_n bytes) with room for max_text; returns the new length.
static inline uint32_t merge(char* text, uint32_t old_n, const char* line, uint32_t line_n) {
    if (old_n > max_text || (old_n && text[old_n - 1] != '\n')) old_n = 0;
    uint32_t lines = 0;
    for (uint32_t i = 0; i != old_n; ++i) lines += text[i] == '\n';
    uint32_t from = 0;
    while (lines + 1 > max_records || old_n - from + line_n > max_text) {   // drop the oldest
        while (text[from] != '\n') ++from;
        ++from;
        --lines;
    }
    for (uint32_t i = 0; i != old_n - from; ++i) text[i] = text[from + i];
    uint32_t n = old_n - from;
    for (uint32_t i = 0; i != line_n; ++i) text[n++] = line[i];
    return n;
}
}}
#endif

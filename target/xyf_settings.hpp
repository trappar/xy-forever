#ifndef XYF_SETTINGS_HPP
#define XYF_SETTINGS_HPP
#include <stdint.h>
// 4.8: device settings (the Settings page) and the "Confirm on device" gate (not built: no prompt opens it yet).
// Freestanding, header only, pure.
// A sibling of config.txt (xyf_config.hpp), not part of it: config.txt is the pending/saved mod selection
// written only by Save (its mods= line last), while a setting changes on its own page.
//
// The record, /yaffs2/xy-forever/settings.txt (key=value lines, LF; unknown keys ignored):
//   format=1
//   confirm=0|1            Confirm on device: a host command that changes which code runs next waits for PLAY
// (4.8m2: the demo "knob=normal|reversed" left; a record that still has it reads as before, the key ignored.)
// Replaced as config.txt is: settings.txt.part, settings.txt -> settings.old, .part -> settings.txt, old removed.
// No record (absent or unreadable): the defaults, confirm=1 (unused until the gate); tests seed confirm=0.
// The confirm setting is to change only on the device, never over USB: with its gate, file.write must refuse
// settings.txt (not done yet: today a host may write it, and the gate is not built).
namespace xyf { namespace settings {
static const char path[] = "/yaffs2/xy-forever/settings.txt";
static const char old_path[] = "/yaffs2/xy-forever/settings.old";
static const char part_path[] = "/yaffs2/xy-forever/settings.txt.part";
static const uint32_t max_text = 256;
struct Record { uint32_t confirm; };
static inline void defaults(Record& r) { r.confirm = 1; }
static inline bool equal(const char* v, uint32_t n, const char* s) {
    uint32_t i = 0;
    for (; i != n && s[i] && v[i] == s[i]; ++i) {}
    return i == n && !s[i];
}
// The value of key on its own line (key=value), or 0; length in n.
static inline const char* value(const char* t, uint32_t length, const char* key, uint32_t& n) {
    for (uint32_t at = 0; at < length;) {
        uint32_t k = 0;
        while (key[k] && at + k < length && t[at + k] == key[k]) ++k;
        uint32_t end = at;
        while (end < length && t[end] != '\n') ++end;
        if (!key[k] && at + k < length && t[at + k] == '=') { n = end - at - k - 1; return t + at + k + 1; }
        at = end + 1;
    }
    n = 0;
    return 0;
}
// A record's text over the defaults: only exact values count (confirm=0 turns it off; anything else keeps it on).
static inline void parse(const char* t, uint32_t length, Record& r) {
    defaults(r);
    uint32_t n = 0;
    const char* v = value(t, length, "confirm", n);
    if (v && equal(v, n, "0")) r.confirm = 0;
}
// The whole record; bytes, 0 when it does not fit.
static inline uint32_t format(char* out, uint32_t capacity, const Record& r) {
    const char* parts[] = {"format=1\nconfirm=", r.confirm ? "1" : "0", "\n"};
    uint32_t n = 0;
    for (uint32_t p = 0; p != 3; ++p)
        for (const char* s = parts[p]; *s; ++s) { if (n == capacity) return 0; out[n++] = *s; }
    return n;
}

// The Settings page: one row per setting that works; PLAY or a knob push acts on the selected one. Confirm on
// device is stored (confirm=) but has no row until its gate is built.
// 4.8m2: Mod memory, part of the saved configuration (config.txt memory=, xyf_config.hpp), not of this record: like
// the Mods page's toggles it is pending until boot (Continue asks Save / Discard / Back). xyf_mods_io keeps it and
// says which of these the row shows (menu::State memory, memory_pending).
enum Row : uint32_t { row_memory = 0, rows = 1 };
enum Memory : uint32_t { memory_reading = 0, memory_unavailable, memory_standard, memory_expanded };
static inline const char* label(uint32_t) { return "Mod memory"; }
// The row's value; pending: the Mods page's mark (": applied when you boot").
static inline const char* memory_text(uint32_t memory, bool pending) {
    if (memory == memory_reading) return "Checking the mod packages...";
    if (memory == memory_unavailable) return "Not available with these mod packages";
    if (memory == memory_expanded) return pending ? "4 MiB (experimental): applied when you boot" : "4 MiB (experimental)";
    return pending ? "Standard: applied when you boot" : "Standard";
}

// The confirm gate: "Allow <what>? PLAY = yes, STOP = no", bounded. Only a real key event answers: an injected
// one (the host's test key command) is ignored, so the host can never approve its own command.
static const uint32_t timeout_ms = 30000;
static const uint32_t key_play = 51, key_stop = 52;     // xyf_menu.hpp Key (KPlay, KStop)
enum Answer : uint32_t { pending = 0, yes = 1, no = 2, timed_out = 3, idle = 4 };
struct Gate { uint32_t state, started_ms; const char* what; };
static inline void gate_clear(Gate& g) { g.state = idle; g.started_ms = 0; g.what = ""; }
static inline void gate_open(Gate& g, const char* what, uint32_t now_ms) { g.state = pending; g.started_ms = now_ms; g.what = what; }
static inline void gate_key(Gate& g, uint32_t key, uint32_t pressed, bool injected) {
    if (g.state != pending || injected || !pressed) return;
    if (key == key_play) g.state = yes;
    else if (key == key_stop) g.state = no;
}
// The gate's answer at now_ms: pending until a real PLAY / STOP or the bound (wrap-safe).
static inline uint32_t gate_poll(Gate& g, uint32_t now_ms) {
    if (g.state == pending && now_ms - g.started_ms >= timeout_ms) g.state = timed_out;
    return g.state;
}
} }
#endif

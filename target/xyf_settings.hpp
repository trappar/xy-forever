#ifndef XYF_SETTINGS_HPP
#define XYF_SETTINGS_HPP
#include <stdint.h>
// Device settings (the Settings page) and the "Ask before USB control" question (#65: usb_service_trust.ipp asks it).
// Freestanding, header only, pure.
// A sibling of config.txt (xyf_config.hpp), not part of it: config.txt is the pending/saved mod selection
// written only by Save (its mods= line last), while a setting changes on its own page.
//
// The record, /yaffs2/xy-forever/settings.txt (key=value lines, LF; unknown keys ignored, a missing key its default):
//   format=1
//   confirm=0|1            Ask before USB control (#65; the key keeps its first name): the first serial command of a
//                          connection makes the device ask whether to trust the computer (usb_service_trust.ipp)
//   power_on=play|always   Menu at power-on (#21): with PLAY held (default) or always
//   usb=mtp_serial|mtp_only|serial   USB mode in the menu (#21, #191): the menu always starts MTP + serial as one
//                          composite device and never changes it within a boot; the mode turns each on or off in our own
//                          code, live: both (default), MTP (every serial request but hello answers serial_off: no host
//                          tool, no web app) or Serial (MTP operations answer Access_Denied): usb_service_settings.ipp.
//                          usb=mtp (written before MTP alone existed) reads as mtp_serial; anything else: the default.
// (The demo "knob=normal|reversed" left; a record that still has it reads as before, the key ignored.)
// format: 1 (or none: a record before the key) reads; a higher number is a newer XY Forever's record: it reads as
// the defaults with newer set (the page says so and changes nothing); anything else: the defaults.
// Replaced as config.txt is: settings.txt.part, settings.txt -> settings.old, .part -> settings.txt, old removed.
// No record (absent or unreadable): the defaults, confirm=1 (#56: on); the simulator and tests seed confirm=0.
// Readers: B's menu (usb_service settings_load, through A) and A at each check of the default (file_boot a_power_on:
// settings.txt, else settings.old; bounded, read-only), whose answer tells B to open the menu without PLAY.
// No USB command changes the confirm setting, only the Settings row with the OP-XY's own keys (injected keys may not:
// usb_service settings_open). File transfers (MTP, the serial file commands) stay open: they may replace or delete
// settings.txt like any file (the question covers serial commands, not files).
namespace xyf { namespace settings {
static const char path[] = "/yaffs2/xy-forever/settings.txt";
static const char old_path[] = "/yaffs2/xy-forever/settings.old";
static const char part_path[] = "/yaffs2/xy-forever/settings.txt.part";
static const uint32_t max_text = 256, version = 1;
enum PowerOn : uint32_t { power_play = 0, power_always = 1 };
enum Usb : uint32_t { usb_mtp_serial = 0, usb_mtp_only = 1, usb_serial = 2, usb_modes };
struct Record {
    uint32_t confirm, power_on, usb, newer;
};
static inline void defaults(Record& r) {
    r.confirm = 1;
    r.power_on = power_play;
    r.usb = usb_mtp_serial;
    r.newer = 0;
}
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
        if (!key[k] && at + k < length && t[at + k] == '=') {
            n = end - at - k - 1;
            return t + at + k + 1;
        }
        at = end + 1;
    }
    n = 0;
    return 0;
}
// A format value above version (decimal digits, at most 9: no overflow).
static inline bool newer_format(const char* v, uint32_t n) {
    if (!n || n > 9) return false;
    uint32_t number = 0;
    for (uint32_t i = 0; i != n; ++i) {
        if (v[i] < '0' || v[i] > '9') return false;
        number = number * 10 + uint32_t(v[i] - '0');
    }
    return number > version;
}
// A record's text over the defaults: only exact values count (confirm=0 turns it off; anything else keeps it on;
// an unknown power_on or usb value keeps its default).
static inline void parse(const char* t, uint32_t length, Record& r) {
    defaults(r);
    uint32_t n = 0;
    const char* v = value(t, length, "format", n);
    if (v && !equal(v, n, "1")) {
        r.newer = newer_format(v, n) ? 1u : 0u;
        return;
    }
    v = value(t, length, "confirm", n);
    if (v && equal(v, n, "0")) r.confirm = 0;
    v = value(t, length, "power_on", n);
    if (v && equal(v, n, "always")) r.power_on = power_always;
    v = value(t, length, "usb", n);
    if (v && equal(v, n, "serial")) r.usb = usb_serial;
    else if (v && equal(v, n, "mtp_only")) r.usb = usb_mtp_only;
}
static inline const char* usb_value(uint32_t u) {
    return u == usb_serial ? "serial" : u == usb_mtp_only ? "mtp_only" : "mtp_serial";
}
// The whole record; bytes, 0 when it does not fit.
static inline uint32_t format(char* out, uint32_t capacity, const Record& r) {
    const char* parts[] = {"format=1\nconfirm=",
                           r.confirm ? "1" : "0",
                           "\npower_on=",
                           r.power_on == power_always ? "always" : "play",
                           "\nusb=",
                           usb_value(r.usb),
                           "\n"};
    uint32_t n = 0;
    for (uint32_t p = 0; p != sizeof(parts) / sizeof(parts[0]); ++p)
        for (const char* s = parts[p]; *s; ++s) {
            if (n == capacity) return 0;
            out[n++] = *s;
        }
    return n;
}

// The Settings page: one row per setting that works, then About; PLAY or a knob push acts on the selected one
// (xyf_menu.cpp: About opens the About page, once the home menu's Info row). #65: Ask before USB control (confirm=), On
// or Off; the offline stage-2 modes (XYF_SMALL_CRC: no settings.txt, no USB service) have no such row.
// #21: PLAY on a setting opens its option list (xyf_menu Screen::options: - / + choose, PLAY picks, STOP leaves it
// unchanged); a value index is its option's index (memory_options, power_on_options, usb_options, confirm_options). #191:
// a settings.txt row's pick takes effect at once (B's copy; marked until written) and saves on its own at a quiet
// moment, quiet_ms after the last pick (quick picks: one write), or at once when the page is left (usb_service
// settings_view: the config job, so settings.txt or settings.old always holds a whole record). A power cut before
// that loses only that change, never half-written.
// Mod memory, part of the saved configuration (config.txt memory=, xyf_config.hpp), not of this record: saved the same
// way as the Mods page's toggles (#197, xyf_mods_io pick_step), and built at the next boot (Continue, or a power-on:
// the fingerprint hashes memory=). xyf_mods_io says what the row shows (menu::State memory, memory_pending: not saved yet).
#if !defined(XYF_SMALL_CRC)
enum Row : uint32_t { row_memory = 0, row_power_on, row_usb, row_confirm, row_about, rows, row_packs = rows };
static const char* const labels[rows] = {"Mod memory", "Menu at power-on", "USB mode", "Ask before USB control",
                                         "About"};
#else
enum Row : uint32_t { row_memory = 0, row_power_on, row_usb, row_about, rows, row_packs = rows };
static const char* const labels[rows] = {"Mod memory", "Menu at power-on", "USB mode", "About"};
#endif
enum Memory : uint32_t { memory_reading = 0, memory_unavailable, memory_standard, memory_expanded };
static inline const char* label(uint32_t row) { return row < rows ? labels[row] : ""; }
// The options, in value order (Memory - memory_standard; PowerOn; Usb; confirm 0 off, 1 on).
// Mod memory: the base's reserved region, 2 MiB (reserved_bytes 0x200000, mod_known_bases.inc), or its 4 MiB layout.
static const char* const memory_options[] = {"2 MiB", "4 MiB (experimental)"};
static const char* const power_on_options[] = {"PLAY held", "Always"};
static const char* const usb_options[usb_modes] = {"MTP + Serial", "MTP", "Serial"};
static const char* const confirm_options[] = {"Off", "On"};
static const uint32_t power_on_option_count = 2, usb_option_count = usb_modes, confirm_option_count = 2;
// The row's value (short: the page draws it at the label's size); pending: the Mods page's mark (": applied when
// you boot"; the simulator's log; the page marks the value instead).
static inline const char* memory_text(uint32_t memory, bool pending) {
    if (memory == memory_reading) return "Checking...";
    if (memory == memory_unavailable) return "Not available";
    if (memory == memory_expanded)
        return pending ? "4 MiB (experimental): applied when you boot" : "4 MiB (experimental)";
    return pending ? "2 MiB: applied when you boot" : "2 MiB";
}
static inline const char* power_on_text(uint32_t p) { return power_on_options[p == power_always ? 1 : 0]; }
// MTP alone: the row says what it costs (the host tool and the web app need the serial interface).
static inline const char* usb_text(uint32_t u) {
    return u == usb_mtp_only ? "MTP (no web app)" : usb_options[u == usb_serial ? 2 : 0];
}
static inline const char* confirm_text(uint32_t c) { return confirm_options[c ? 1 : 0]; }
// The page's notes, and the lines about a save (#191: quiet_ms after the last pick, as xyf_mods_io.hpp quiet_ms).
static const uint32_t quiet_ms = 1000;
static const char note_newer[] = "settings.txt is from a newer XY Forever: kept unchanged.";
static const char note_loading[] = "Settings are still being read.";
static const char note_saved[] = "Settings saved.";
static const char note_failed[] = "Settings not saved: pick the value again, or leave Settings, to try again.";
static const char note_link[] = "Settings not saved: storage link lost.";

// The question (#65, usb_service_trust.ipp): "Allow this computer to control your OP-XY?", PLAY = yes, STOP = no,
// with no time limit. Only a real key event answers: an injected one (the host's diag.key) is ignored, so the host
// can never answer for the user.
static const uint32_t key_play = 51, key_stop = 52;     // xyf_menu.hpp Key (KPlay, KStop)
static const char note_on_device[] = "Ask before USB control changes only with the OP-XY's own keys.";
enum Answer : uint32_t { pending = 0, yes = 1, no = 2, idle = 4 };
struct Gate {
    uint32_t state;
};
static inline void gate_clear(Gate& g) { g.state = idle; }
static inline void gate_open(Gate& g) { g.state = pending; }
static inline void gate_key(Gate& g, uint32_t key, uint32_t pressed, bool injected) {
    if (g.state != pending || injected || !pressed) return;
    if (key == key_play) g.state = yes;
    else if (key == key_stop) g.state = no;
}
}}
#endif

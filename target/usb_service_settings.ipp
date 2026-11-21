// Part of usb_service.cpp: settings.txt and the Settings page's stored rows (xyf_settings.hpp; usb_service.hpp).
#if defined(XYF_CONTENT_SWITCH)
// settings.txt (xyf_settings.hpp; usb_service.hpp).
namespace xs = xyf::settings;
#include "usb_service_pack_list.ipp"
#include "usb_service_pack_change.ipp"
xs::Record settings_record __attribute__((section(".data"))) = {1, xs::power_play, xs::usb_mtp_serial, 0};
U32 settings_loaded __attribute__((section(".data"))) = 0;
// #21: what settings.txt holds (as read or last saved): the page marks a row whose value differs from it.
U32 stored_power_on __attribute__((section(".data"))) = xs::power_play;
U32 stored_usb __attribute__((section(".data"))) = xs::usb_mtp_serial;
U32 stored_confirm __attribute__((section(".data"))) = 1;
bool settings_load() {
    if (settings_loaded) return true;
    if (!menu_link::usb_idle()) return false;                           // 191: not while A's catalog is on its way
    if (!a_serving() || job_running() || closing) return false;         // later: once A serves, the slot is free
    char text[xs::max_text];
    uint32_t bytes = 0;
    U32 code = file_client::mount_ready(mounted);
    if (code == P::s_ok) code = file_client::read_file(xs::path, 0, false, reinterpret_cast<uint8_t*>(text), sizeof(text), bytes, mounted);
    if (code == P::s_not_found) code = file_client::read_file(xs::old_path, 0, false, reinterpret_cast<uint8_t*>(text), sizeof(text), bytes, mounted);
    if (code == P::s_ok) xs::parse(text, bytes, settings_record);
    else xs::defaults(settings_record);                                 // absent or unreadable: the defaults
    stored_power_on = settings_record.power_on; stored_usb = settings_record.usb;   // nothing marked
    stored_confirm = settings_record.confirm;
    settings_loaded = 1;                                                // one try: bounded
    return true;
}
xs::Record& settings() { return settings_record; }
xs::Gate confirm_gate __attribute__((section(".data"))) = {xs::idle};
U32 key_from_host __attribute__((section(".data"))) = 0;               // the last key event was injected (diag.key)
bool confirm_key(uint32_t key, uint32_t pressed, bool injected) {
    key_from_host = injected;
    if (confirm_gate.state != xs::pending) return false;
    xs::gate_key(confirm_gate, key, pressed, injected);
    return true;
}
// #191: a pick saves on its own (xyf_settings.hpp; as the Mods page's flips, xyf_mods_save.ipp pick_step): the whole
// record, quiet_ms after the last pick (quick picks: one write), or the pass after the page is left. due: 1 a pick
// waits for its write, 2 its write failed (the next pick or leaving tries again); since: the last pick; job: the write's
// config job (0: none); written: the record it writes (stored_* once it is done); left: 1 the page was just left.
U32 settings_due __attribute__((section(".data"))) = 0;
U32 settings_since __attribute__((section(".data"))) = 0;
U32 settings_job __attribute__((section(".data"))) = 0;
U32 settings_left __attribute__((section(".data"))) = 0;
xs::Record settings_written __attribute__((section(".data"))) = {1, xs::power_play, xs::usb_mtp_serial, 0};
uint32_t settings_store() {
    char text[xs::max_text];
    const U32 n = xs::format(text, sizeof(text), settings_record);
    const U32 code = config_begin(text, n, "", xs::path, xs::old_path, xs::part_path);
    if (code == P::s_ok) settings_written = settings_record;
    return code;
}
U32 settings_marks() {
    const xs::Record& r = settings_record;
    return (r.power_on != stored_power_on ? 1u << xs::row_power_on : 0u) | (r.usb != stored_usb ? 1u << xs::row_usb : 0u) |
           (r.confirm != stored_confirm ? 1u << xs::row_confirm : 0u);
}
// #191: USB mode as B's copy holds it: a pick applies at once (usb_service.hpp serial_off, mtp_off).
bool serial_off() { return settings_record.usb == xs::usb_mtp_only; }
bool mtp_off() { return settings_record.usb == xs::usb_serial; }
const char* settings_open(xyf::menu::State& s, uint32_t row) {
    if (row == xs::row_packs) {
        s.screen = xyf::menu::Screen::packs;
        s.pack_page = s.pack_selected = 0;
        s.pack_inbox = s.pack_confirm = false;
        s.pack_result = 0;
        packs.page = ~0u;
        s.pack_ready = false;
        return 0;
    }
    if (!settings_loaded) return xs::note_loading;
    const xs::Record& r = settings_record;
    if (r.newer) return xs::note_newer;                                 // a newer record: never rewritten here
    if (row == xs::row_confirm && key_from_host) return xs::note_on_device;   // #65: never from the host
    if (row == xs::row_power_on) xyf::menu::open_options(s, xs::power_on_options, xs::power_on_option_count, r.power_on);
    else if (row == xs::row_usb) xyf::menu::open_options(s, xs::usb_options, xs::usb_option_count, r.usb);
    else if (row == xs::row_confirm) xyf::menu::open_options(s, xs::confirm_options, xs::confirm_option_count, r.confirm);
    return 0;
}
void settings_pick(const xyf::menu::State& s) {
    xs::Record& r = settings_record;
    if (r.newer || s.option_selected >= s.option_count) return;
    if (s.entry == xs::row_power_on) r.power_on = s.option_selected;
    else if (s.entry == xs::row_usb) r.usb = s.option_selected;
    else if (s.entry == xs::row_confirm && !key_from_host) r.confirm = s.option_selected;
    else return;
    settings_due = 1; settings_since = now();                           // saved once quiet
}
// The write once due (now: the page was left), or the end of the one running: a line about it, or 0.
const char* settings_step(bool now_due) {
    if (settings_job) {
        JobView v;
        job_view(v);
        if (v.id == settings_job && v.state == 1) return 0;              // still running
        const bool ok = v.id == settings_job && v.state == 2 && v.phase == U32(xc::phase_done);
        settings_job = 0;
        if (!ok) { if (!settings_due) settings_due = 2; return xs::note_failed; }   // still marked
        stored_power_on = settings_written.power_on; stored_usb = settings_written.usb;
        stored_confirm = settings_written.confirm;
        return xs::note_saved;
    }
    if (!settings_due) return 0;
    if (!settings_marks()) { settings_due = 0; return 0; }               // picked back to what is saved
    if (!now_due && (settings_due == 2 || now() - settings_since < xs::quiet_ms)) return 0;
    const U32 code = settings_store();
    if (code == P::s_busy) return 0;                                     // a job (or the Mods page's write): next pass
    if (code != P::s_ok) { settings_due = 2; return code == P::s_link ? xs::note_link : xs::note_failed; }
    JobView v;
    job_view(v);
    settings_job = v.id; settings_due = 0;
    return 0;
}
bool settings_view(xyf::menu::State& s, const char*& line) {
    using xyf::menu::Screen;
    settings_load();                                                     // once A serves (one bounded read)
    const xs::Record& r = settings_record;
    const char* const power_on = xs::power_on_text(r.power_on);
    const char* const usb = xs::usb_text(r.usb);
    const char* const confirm = xs::confirm_text(r.confirm);
    bool changed = false;
    if (!s.pack_names) { s.pack_names = packs.names; changed = true; }
    if (pack_change_step(s)) changed = true;
    if (s.screen == Screen::packs && !menu_pack_active && pack_list_step(s)) changed = true;
    if (r.newer && !s.settings_note) { s.settings_note = xs::note_newer; changed = true; }   // the page says so
    // Left (not the page, its option list or the About page opened from it): a waiting write starts the pass after
    // (the home menu is drawn first: the write's first request waits for A's window).
    const bool on_page = s.screen == Screen::settings || s.screen == Screen::options || s.screen == Screen::packs ||
                         (s.screen == Screen::info && s.info_return == Screen::settings);
    const bool now_due = settings_left == 1;
    settings_left = on_page ? 0u : settings_left ? 2u : 1u;
    if (const char* note = settings_step(now_due)) {
        line = note; changed = true;
        if (note != xs::note_saved && s.screen == Screen::settings) s.settings_note = note;   // on the page too
    }
    const U32 marks = settings_marks();
    if (s.power_on_text != power_on || s.usb_text != usb || s.confirm_text != confirm || s.settings_marked != marks) changed = true;
    s.power_on_text = power_on; s.usb_text = usb; s.confirm_text = confirm; s.settings_marked = marks;
    return changed;
}
#endif

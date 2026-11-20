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
xs::Gate confirm_gate __attribute__((section(".data"))) = {xs::idle, 0, ""};
bool confirm_key(uint32_t key, uint32_t pressed, bool injected) {
    if (confirm_gate.state != xs::pending) return false;
    xs::gate_key(confirm_gate, key, pressed, injected);
    return true;
}
uint32_t settings_store() {
    char text[xs::max_text];
    const U32 n = xs::format(text, sizeof(text), settings_record);
    return config_begin(text, n, "", xs::path, xs::old_path, xs::part_path);
}
// The save the page's leaving started (its config job id; 0: none), and whether this leaving was handled (0: on
// the page, or a change came since: one try per leaving, so a refused save never repeats every pass).
U32 settings_job __attribute__((section(".data"))) = 0;
U32 settings_left __attribute__((section(".data"))) = 0;
U32 settings_marks() {
    const xs::Record& r = settings_record;
    return (r.power_on != stored_power_on ? 1u << xs::row_power_on : 0u) | (r.usb != stored_usb ? 1u << xs::row_usb : 0u) |
           (r.confirm != stored_confirm ? 1u << xs::row_confirm : 0u);
}
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
    if (settings_job) return xs::note_saving;                            // the last leaving's write is still running
    const xs::Record& r = settings_record;
    if (r.newer) return xs::note_newer;                                 // a newer record: never rewritten here
    if (row == xs::row_power_on) xyf::menu::open_options(s, xs::power_on_options, xs::power_on_option_count, r.power_on);
    else if (row == xs::row_usb) xyf::menu::open_options(s, xs::usb_options, xs::usb_option_count, r.usb);
    else if (row == xs::row_confirm) xyf::menu::open_options(s, xs::confirm_options, xs::confirm_option_count, r.confirm);
    return 0;
}
void settings_pick(const xyf::menu::State& s) {
    xs::Record& r = settings_record;
    if (r.newer || settings_job || s.option_selected >= s.option_count) return;
    if (s.entry == xs::row_power_on) r.power_on = s.option_selected;
    else if (s.entry == xs::row_usb) r.usb = s.option_selected;
    else if (s.entry == xs::row_confirm) r.confirm = s.option_selected;
}
// The leaving's write (the whole record as the page left it, settings_store): its line, or why it did not start.
const char* settings_save() {
    const U32 code = settings_store();
    if (code != P::s_ok) return code == P::s_busy ? xs::note_busy : code == P::s_link ? xs::note_link : xs::note_failed;
    JobView v;
    job_view(v);
    settings_job = v.id;
    return xs::note_saving;
}
const char* settings_done() {
    if (!settings_job) return 0;
    JobView v;
    job_view(v);
    if (v.id == settings_job && v.state == 1) return 0;                  // still running
    const bool ok = v.id == settings_job && v.state == 2 && v.phase == U32(xc::phase_done);
    settings_job = 0;
    if (!ok) return xs::note_failed;                                    // still marked: the next leaving tries again
    const bool usb = settings_record.usb != stored_usb;
    stored_power_on = settings_record.power_on; stored_usb = settings_record.usb;   // as written (no change while it ran)
    stored_confirm = settings_record.confirm;
    return usb ? xs::note_saved_usb : xs::note_saved;
}
bool settings_view(xyf::menu::State& s, const char*& line) {
    using xyf::menu::Screen;
    settings_load();                                                     // once A serves (one bounded read)
    const xs::Record& r = settings_record;
    const char* const power_on = xs::power_on_text(r.power_on);
    const char* const usb = xs::usb_text(r.usb);
    const char* const confirm = xs::confirm_text(r.confirm);
    const U32 marks = settings_marks();
    bool changed = s.power_on_text != power_on || s.usb_text != usb || s.confirm_text != confirm || s.settings_marked != marks;
    if (!s.pack_names) { s.pack_names = packs.names; changed = true; }
    if (pack_change_step(s)) changed = true;
    if (s.screen == Screen::packs && !menu_pack_active && pack_list_step(s)) changed = true;
    s.power_on_text = power_on; s.usb_text = usb; s.confirm_text = confirm; s.settings_marked = marks;
    if (r.newer && !s.settings_note) { s.settings_note = xs::note_newer; changed = true; }   // the page says so
    // Left (not the page, its option list or the About page opened from it) with a change: one write, started a
    // pass after the leaving (the home menu is drawn first: the write's first request waits for A's window).
    const bool on_page = s.screen == Screen::settings || s.screen == Screen::options || s.screen == Screen::packs ||
                         (s.screen == Screen::info && s.info_return == Screen::settings);
    if (on_page || !marks) settings_left = 0;
    else if (settings_left == 1 && !settings_job) { settings_left = 2; line = settings_save(); }
    else if (!settings_left) { settings_left = 1; line = xs::note_saving; }
    if (const char* note = settings_done()) { line = note; changed = true; }
    return changed;
}
#endif

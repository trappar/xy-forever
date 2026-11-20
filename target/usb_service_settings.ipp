// Part of usb_service.cpp: settings.txt and the Settings page's stored rows (xyf_settings.hpp; usb_service.hpp).
#if defined(XYF_CONTENT_SWITCH)
// settings.txt (xyf_settings.hpp; usb_service.hpp).
namespace xs = xyf::settings;
xs::Record settings_record __attribute__((section(".data"))) = {1, xs::power_play, xs::usb_mtp, 0};
U32 settings_loaded __attribute__((section(".data"))) = 0;
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
// #21: the write a row started (its config job id; 0: none) and that row, for the note at its end.
U32 settings_job __attribute__((section(".data"))) = 0;
U32 settings_row __attribute__((section(".data"))) = 0;
const char* settings_change(uint32_t row) {
    if (!settings_loaded) return xs::note_loading;
    if (settings_job) return xs::note_saving;                            // one write at a time
    xs::Record& r = settings_record;
    if (r.newer) return xs::note_newer;                                 // a newer record: never rewritten here
    const U32 power_on = r.power_on, usb = r.usb, confirm = r.confirm, newer = r.newer;   // field by field: no memcpy
    if (row == xs::row_power_on) r.power_on = xs::next_power_on(r.power_on);
    else if (row == xs::row_usb) r.usb = xs::next_usb(r.usb);
    else return 0;
    const U32 code = settings_store();
    if (code != P::s_ok) {
        r.power_on = power_on; r.usb = usb; r.confirm = confirm; r.newer = newer;
        return code == P::s_busy ? xs::note_busy : code == P::s_link ? xs::note_link : xs::note_failed;
    }
    JobView v;
    job_view(v);
    settings_job = v.id; settings_row = row;
    return xs::note_saving;
}
const char* settings_done() {
    if (!settings_job) return 0;
    JobView v;
    job_view(v);
    if (v.id == settings_job && v.state == 1) return 0;                  // still running
    const bool ok = v.id == settings_job && v.state == 2 && v.phase == U32(xc::phase_done);
    settings_job = 0;
    if (!ok) { settings_loaded = 0; return xs::note_failed; }            // read again: the page shows what is stored
    return settings_row == xs::row_usb ? xs::note_saved_usb : xs::note_saved;
}
bool settings_view(xyf::menu::State& s) {
    settings_load();                                                     // once A serves (one bounded read)
    const xs::Record& r = settings_record;
    const char* const power_on = xs::power_on_text(r.power_on);
    const char* const usb = xs::usb_text(r.usb);
    bool changed = s.power_on_text != power_on || s.usb_text != usb;
    s.power_on_text = power_on; s.usb_text = usb;
    if (r.newer && !s.settings_note) { s.settings_note = xs::note_newer; changed = true; }   // the page says so
    if (const char* note = settings_done()) { s.settings_note = note; changed = true; }
    return changed;
}
#endif

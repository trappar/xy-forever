// Part of usb_service.cpp, included there at this point: not compiled alone.
uint32_t page_serving() { return a_serving() ? uint32_t(P::s_ok) : uint32_t(P::s_link); }
volatile uint32_t& page_mount() { return mounted; }
bool page_busy() { stop_idle_gc(); return boot_probe_active() || recovery_pending || job_running() || closing XYF_OR_MTP_BUSY; }
void save_log_begin() { xl::begin(save_times, xl::k_save, now(), xl::p_list); save_active = 1; }
void save_log_selection(uint32_t mods, bool expanded, uint32_t added, uint32_t removed) {
    if (!save_active) return;
    save_times.mods = mods; save_times.expanded = expanded ? 1u : 0u; save_times.added = added; save_times.removed = removed;
}
void save_log_lap(uint32_t part) { if (save_active) save_lap(part); }
void save_log_drop() { if (save_active) { save_times.kind = xl::k_none; save_active = 0; } }
uint32_t mods_start(const uint8_t* payload, uint32_t length, uint32_t& job, uint32_t& missing) {
    U32 id = 0, why = 0, taken = 0;
    job = 0; missing = 0;
    if (job_blocks(P::t_mods_apply)) return P::s_busy;                  // as mods.apply (a job, the close)
    const U32 code = mods_begin(payload, length, id, why, taken);
    job = id; missing = taken ? mods_ids_taken : why;                 // the page says why
    return code;
}
void job_view(JobView& v) {                                             // as job_status (the commit too)
#if defined(XYF_CONTENT_SWITCH)
    if (job_kind == 2) {                                                 // the config job
        const ConfigSlot& g = gslot();
        v.state = job_state; v.id = g.id; v.phase = g.step != cg_ended ? 0u : g.error ? U32(xc::phase_failed) : U32(xc::phase_done);
        v.done = g.step; v.total = cg_ended; v.error = g.step == cg_ended ? g.error : 0u; v.status = g.code; v.debris = 0; v.commit = 0;
        return;
    }
    if (job_kind) {                                                      // a content switch (its own id)
        const ContentSlot& c = cslot();
        v.state = job_state; v.id = c.id; v.phase = content_phase(c); v.done = c.job.done; v.total = c.job.total;
        v.error = c.stage == cs_ended ? c.job.error : 0u; v.status = c.job.code; v.debris = 0; v.commit = 0;
        return;
    }
#endif
    JobSlot& s = slot();
    mj::Progress g;
    mj::progress(s.job, g);
    const bool committing = job_running() && s.stage >= js_away && s.stage != js_wipe;
    v.state = job_state; v.id = s.id;
    v.phase = s.commit_error ? U32(mj::Phase::failed) : committing ? U32(mj::Phase::finish) : U32(g.phase);
    v.done = committing ? s.stage - js_away + 1 : g.done; v.total = committing ? 3u : g.total;
    v.error = s.commit_error ? s.commit_error : s.link && g.phase == mj::Phase::failed ? job_link_error : U32(g.error);
    v.status = s.commit_error ? s.commit_code : g.status;
    v.debris = s.debris; v.commit = committing ? 1u : 0u;
}
uint32_t job_stop() {                                                   // as job.cancel (not in the commit)
    if (!job_running() || job_kind) return P::s_not_found;              // a content switch is not a mod job
    if (slot().stage >= js_away) return P::s_state;
    mj::cancel(slot().job);
    job_finish(slot());
    return P::s_ok;
}
#if defined(XYF_CONTENT_SWITCH)
const char* content_default(uint32_t entry) {                            // the menu's SHIFT+PLAY
    const xyf::catalog::Catalog* catalog = xyf_boot::catalog();
    if (!catalog || entry >= catalog->count || (!(catalog->items[entry].flags & xyf::catalog::flag_content_missing) && !content_pending(catalog->items[entry].id)))
        return 0;
    if (!menu_usb) return "Content switch: needs USB on in the menu.";
    const U32 code = content_begin(catalog->items[entry].id, true);
    return code == P::s_ok ? "Installing content..." : code == P::s_busy ? "Busy: a job is running." :
           code == P::s_link ? "Storage link lost." : "Content switch: could not start.";
}
uint32_t content_switch(const char* id) { return content_begin(id, true, false); }
uint32_t config_store(const char* text, uint32_t length, const char* default_id) { return config_begin(text, length, default_id); }
// settings.txt (xyf_settings.hpp; usb_service.hpp).
namespace xs = xyf::settings;
xs::Record settings_record __attribute__((section(".data"))) = {1};
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
const char* job_target() { return slot().target; }
const char* content_line() {
    ContentSlot& c = cslot();
    static U32 offer_shown __attribute__((section(".data"))) = 0;
    if (!recovery_offer) offer_shown = 0;
    if (recovery_offer && offer_shown != recovery_offer && !job_running() && !closing) {
        const char* message = recovery_offer == 3 ? "Content journal: newer version; kept unchanged." :
                              recovery_offer == 2 ? "Content journal invalid; kept unchanged." : "Source missing. Set default to undo: ";
        content_text(c, message, recovery_offer == 1 ? recovery_record.previous : "");
        offer_shown = recovery_offer;
        return c.line;
    }
    if (job_kind != 1 || c.line_seq == content_line_shown) return 0;
    content_line_shown = c.line_seq;
    return c.line;
}
#endif

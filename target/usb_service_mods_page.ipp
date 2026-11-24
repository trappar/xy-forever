// Part of usb_service.cpp, included there at this point: not compiled alone.
uint32_t page_serving() { return a_serving() ? uint32_t(P::s_ok) : uint32_t(P::s_link); }
volatile uint32_t& page_mount() { return mounted; }
bool page_busy() {
    stop_idle_gc();
    return boot_probe_active() || rehearsal_active() || recovery_pending || job_running() || closing XYF_OR_MTP_BUSY;
}
void* mods_metadata_work() { return reinterpret_cast<void*>(job_work_end - mods_metadata_bytes); }
void save_log_begin() {
    xl::begin(save_times, xl::k_save, now(), xl::p_list);
    save_active = 1;
}
void save_log_selection(uint32_t mods, bool expanded, uint32_t added, uint32_t removed) {
    if (!save_active) return;
    save_times.mods = mods;
    save_times.expanded = expanded ? 1u : 0u;
    save_times.added = added;
    save_times.removed = removed;
}
void save_log_lap(uint32_t part) {
    if (save_active) save_lap(part);
}
void save_log_drop() {
    if (save_active) {
        save_times.kind = xl::k_none;
        save_active = 0;
    }
}
uint32_t mods_start(const uint8_t* payload, uint32_t length, uint32_t& job, uint32_t& missing) {
    U32 id = 0, why = 0, taken = 0;
    job = 0;
    missing = 0;
    if (job_blocks(P::t_mods_apply)) return P::s_busy;                  // as mods.apply (a job, the close)
    const U32 code = mods_begin(payload, length, id, why, taken);
    job = id;
    missing = taken ? mods_ids_taken : why;                 // the page says why
    return code;
}
void job_view(JobView& v) {                                             // as job_status (the commit too)
#if defined(XYF_CONTENT_SWITCH)
    if (job_kind == 2) {                                                 // the config job
        const ConfigSlot& g = gslot();
        v.state = job_state;
        v.id = g.id;
        v.phase = g.step != cg_ended ? 0u : g.error ? U32(xc::phase_failed) : U32(xc::phase_done);
        v.done = g.step;
        v.total = cg_ended;
        v.error = g.step == cg_ended ? g.error : 0u;
        v.status = g.code;
        v.debris = 0;
        v.commit = 0;
        return;
    }
    if (job_kind == 3) return adopt_view(v);                             // #178: the stock entry's adoption
    if (job_kind) {                                                      // a content switch (its own id)
        const ContentSlot& c = cslot();
        v.state = job_state;
        v.id = c.id;
        v.phase = content_phase(c);
        v.done = c.job.done;
        v.total = c.job.total;
        v.error = c.stage == cs_ended ? c.job.error : 0u;
        v.status = c.job.code;
        v.debris = 0;
        v.commit = 0;
        return;
    }
#endif
    JobSlot& s = slot();
    mj::Progress g;
    mj::progress(s.job, g);
    const bool committing = job_state == job_busy && s.stage >= js_away && s.stage != js_wipe;
    v.state = job_state;
    v.id = s.id;
    v.phase = s.commit_error ? U32(mj::Phase::failed) : committing ? U32(mj::Phase::finish) : U32(g.phase);
    v.done = committing ? s.stage - js_away + 1 : g.done;
    v.total = committing ? 3u : g.total;
    v.error = s.commit_error ? s.commit_error : s.link && g.phase == mj::Phase::failed ? job_link_error : U32(g.error);
    v.status = s.commit_error ? s.commit_code : g.status;
    v.debris = s.debris;
    v.commit = committing ? 1u : 0u;
}
uint32_t job_stop() {                                                   // as job.cancel (not in the commit)
    if (job_state != job_busy || job_kind) return P::s_not_found;              // a content switch is not a mod job
    if (slot().stage >= js_away) return P::s_state;
    mj::cancel(slot().job);
    job_finish(slot());
    return P::s_ok;
}
#if defined(XYF_CONTENT_SWITCH)
bool content_offer() { return recovery_offer == 1; }
const char* content_undo() {                                             // the menu's PLAY on the first row
    if (recovery_offer != 1) return 0;
    if (!menu_usb) return "Content switch: needs USB on in the menu.";
    const U32 code = content_begin(recovery_record.previous, true);    // the previous entry back, as the default
    return code == P::s_ok   ? "Installing content..." :
           code == P::s_busy ? "Busy: a job is running." :
           code == P::s_link ? "Storage link lost." :
                               "Content switch: could not start.";
}
uint32_t content_switch(const char* id) { return content_begin(id, true, false); }
uint32_t adopt_start(const char* id, uint32_t mode) { return adopt_begin(id, mode); }
uint32_t content_set_check(const char* id, const char*& why) {
    why = 0;
    if (job_running() || closing XYF_OR_MTP_BUSY || uploading()) return P::s_busy;
    if (!a_serving()) return P::s_link;
    U32 code = file_client::mount_ready(mounted);
    if (code != P::s_ok) return code;
    bool found = false;
    if (!official_121(id, found)) {
        why = "The base is not official OS 1.2.1, and has no content set.";
        return found ? P::s_refused : P::s_not_found;
    }
    char path[file_engine::max_path];
    join(path, xa::firmware, "/", id);
    join(path, path, "/content.sha256");
    const char* const probes[2] = {path, xa::state_old};
    for (U32 k = 0; k != 2; ++k) {
        uint8_t record[file_client::record_bytes];
        const file_client::Reply r = file_client::stat(probes[k], record);
        if (r.outcome) {
            if (r.outcome == file_client::link) mounted = file_client::mounted_stale;
            return file_client::status(r.outcome);
        }
        if (k == 0 && r.value == 0) return P::s_ok;                        // the base has its content set
        if (k == 1 && r.value == 0) why = "Mods' content is installed, and the base has no content set.";
    }
    ContentSetWork& w = cwork();                                         // no job: its work area is free
    uint32_t got = 0;
    code = file_client::read_file(xyf::content::record_path, 0, true, w.page, xyf::content::head_bytes, got, mounted);
    if (code == P::s_ok) {
        xyf::content::State st;
        xyf::content::parse(w.page, got, st);
        if (st.kind != xyf::content::official) why = "Mods' content is installed, and the base has no content set.";
    } else if (code != P::s_not_found) return code;
    if (why) return P::s_refused;
    // The live catalog: the official 1.2.1 one (the build reads every file; this is its quick part).
    join(path, xa::content, "/", xyf::content::catalog);
    xyf_core::hash::Sha256 h;
    for (U32 at = 0;; at += got) {
        code = file_client::read_file(path, at, true, w.page, file_client::piece_max, got, mounted);
        if (code != P::s_ok) break;
        h.update(w.page, got);
        if (got < file_client::piece_max) break;
    }
    xyf_core::hash::Digest d;
    if (code == P::s_not_found || (code == P::s_ok && (!h.finish(d) || !sha_equal(d.bytes, official_digest(true)))))
        why = "The OP-XY's content is not official OS 1.2.1, and the base has no content set.";
    return why ? P::s_refused : code;
}
uint32_t config_store(const char* text, uint32_t length, const char* default_id) {
    return config_begin(text, length, default_id);
}
uint32_t mark_store(const char* text, uint32_t length) {
    return config_begin(text, length, "", xyf::converge::mark_path, xyf::converge::mark_old, xyf::converge::mark_part);
}
const char* job_target() { return slot().target; }
const char* content_line() {
    ContentSlot& c = cslot();
    static U32 offer_shown __attribute__((section(".data"))) = 0;
    if (!recovery_offer) offer_shown = 0;
    if (recovery_offer && offer_shown != recovery_offer && !job_running() && !closing) {
        const char* message = recovery_offer == 3 ? "Content journal: newer version; kept unchanged." :
                              recovery_offer == 2 ? "Content journal invalid; kept unchanged." :
                                                    "Source missing. PLAY on the first row undoes: ";
        content_text(c, message, recovery_offer == 1 ? recovery_record.previous : "");
        offer_shown = recovery_offer;
        return c.line;
    }
    if (job_kind != 1 || c.line_seq == content_line_shown) return 0;
    content_line_shown = c.line_seq;
    return c.line;
}
#endif

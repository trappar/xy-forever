// Part of usb_service.cpp, included there at this point: not compiled alone.
// ---- The save-time log (xyf_save_log.hpp): the times of the running Save, USB build or switch ----------
namespace xl = xyf::save_log;
xl::Times save_times __attribute__((section(".data"))) = {};
volatile U32 save_active __attribute__((section(".data"))) = 0;   // a menu Save holds save_times
void save_lap(U32 part) { xl::lap(save_times, now(), part); }
void save_log_write(bool ok);                                           // after the content switch's slot
#if !defined(XYF_CONTENT_SWITCH)
void save_log_write(bool) { save_times.kind = xl::k_none; save_active = 0; }
#endif
void job_text(JobSlot& s, const char* a, const char* b = "") {          // the job's detail (job.status)
    U32 n = 0;
    for (const char* t = a; *t && n + 1 < mj::detail_bytes; ++t) s.job.detail[n++] = *t;
    for (const char* t = b; *t && n + 1 < mj::detail_bytes; ++t) s.job.detail[n++] = *t;
    s.job.detail[n] = 0;
}
void job_finish(JobSlot& s);
// A commit step failed: error rename (17, code A's error number) or link; nothing more is sent after a link.
// Past the first rename (the old entry in tmp/), B renames it back, so the old entry stays usable.
void commit_fail(JobSlot& s, U32 code, uint32_t error, const char* step) {
    if (code == P::s_link) s.link = 1;
    s.commit_error = s.link ? job_link_error : U32(s.stage == js_cache ? mj::Error::verify :
        s.stage == js_gc ? mj::Error::list : mj::Error::rename);
    s.commit_code = error ? error : code;
    job_text(s, "commit ", step);
    if (s.stage == js_in && s.had_old && !s.link) {
        uint32_t e = 0;
        if (file_client::rename(s.old, s.entry, e, mounted) == P::s_ok) s.had_old = 0;
    }
    job_finish(s);
}
void commit_done(JobSlot& s) {
    job_last_target = s.target[5] == 'a' ? 1u : 2u;
    xyf::blob::collect_begin(s.garbage, reinterpret_cast<uint8_t*>(job_work_end - xyf::blob::index_bytes), xyf::blob::index_bytes);
    s.stage = js_gc;
}
// One commit step per loop pass (js_away, js_in, js_clean).
void commit_step(JobSlot& s) {
    uint32_t error = 0;
    U32 code = P::s_ok;
    ++s.extra_steps;
    if (s.stage == js_away) {                                            // the old entry into tmp/, if any
        xyf::state::Choice choice;                                       // still neither default nor once?
        if ((code = job_choice(s, choice)) != P::s_ok) return commit_fail(s, code, 0, "state");
        if (same_text(s.target, choice.state.default_id) || same_text(s.target, choice.state.once))
            return commit_fail(s, P::s_state, 0, "taken");
        uint8_t record[file_client::record_bytes];
        const file_client::Reply r = file_client::stat(s.entry, record);
        if (r.outcome) { if (r.outcome == file_client::link) mounted = file_client::mounted_stale; code = file_client::status(r.outcome); }
        else if (r.value != 0) { s.stage = js_in; save_lap(xl::p_in); return; }
        else if ((code = file_client::rename(s.entry, s.old, error, mounted)) == P::s_ok) { s.had_old = 1; s.stage = js_in; save_lap(xl::p_in); return; }
        return commit_fail(s, code, error, "away");
    }
    if (s.stage == js_in) {                                              // the new entry onto the target
        if ((code = file_client::rename(s.out, s.entry, error, mounted)) != P::s_ok) return commit_fail(s, code, error, "in");
        writes_ms = now();
        if (!s.had_old) return commit_done(s);
        s.stage = js_clean; save_lap(xl::p_clean);
        if (!tree_begin(s.tree, s.old)) { s.debris = 1; return commit_done(s); }
        return;
    }
    const U32 t = tree_step(s.tree, s.page);                             // js_clean: the old tree
    if (t == tree_more && ++s.clean_calls < clean_cap) return;
    if (t != tree_done) s.debris = 1;                                    // left for the next sweep
    commit_done(s);
}
const char* job_phase_text(U32 phase) { return mj::phase_name(mj::Phase(phase)); }
void job_say(JobSlot& s, bool force) {
    mj::Progress p;
    mj::progress(s.job, p);
    const U32 t = now();
    if (!force && U32(p.phase) == s.said_phase && t - s.said_ms < job_say_ms) return;
    s.said_phase = U32(p.phase); s.said_ms = t;
    U32 n = 0;
    for (const char* a = job_phase_text(U32(p.phase)); *a && n < 20; ++a) s.line[n++] = *a;
    s.line[n++] = ' ';
    n += decimal_text(s.line + n, p.done);
    if (p.total) { s.line[n++] = '/'; n += decimal_text(s.line + n, p.total); }
    s.line[n] = 0;
    say("Assembling mod firmware...", s.line, "Do not unplug or power off.");
}
// The job ended (done, failed or cancelled): a failed one's folders removed, A's window closed (A rescans the
// library: a committed entry is listed).
// a failed one's output is removed by job_pump, one tree_step per loop pass (stage
// js_wipe, "cleaning up": the job still owns the slot, so storage commands answer busy), then job_end.
void job_end(JobSlot& s) {
    const bool done = s.job.phase == mj::Phase::done && !s.commit_error;
    job_state = job_ended; boot_sweep = 3; gc_defer_ms = now();
    save_lap(xl::p_close);
    if (!save_active && !s.link) save_log_write(done);                  // a USB build's line, in its window
    settle_begin();                                                      // op 9 (B's loop stays live), catalog
    if (done) say("Mod firmware added to the library.", s.entry + 19, "Its content is not installed yet.");
    else say(s.job.phase == mj::Phase::cancelled ? "Mod firmware: cancelled." : "Mod firmware assembly FAILED.",
             s.out + 19, s.debris ? "Leftovers stay in tmp/." : "");
}
void job_finish(JobSlot& s) {
    s.ended_ms = now();
    if (!s.link && file_client::close_name_scan(s.metadata_scan, mounted) == P::s_link) s.link = 1;
    if (s.job.phase == mj::Phase::done && !s.commit_error) return job_end(s);
    if (s.link || !tree_begin(s.tree, s.out)) { s.debris = 1; return job_end(s); }   // nothing sent after a link
    s.stage = js_wipe; s.clean_calls = 0; s.debris = 0; save_lap(xl::p_clean);
    say("Mod firmware: cleaning up...", s.out + 19, "Do not unplug or power off.");
}
void wipe_step(JobSlot& s) {                                             // js_wipe: one step of the output's tree
    ++s.extra_steps;
    const U32 t = tree_step(s.tree, s.page);
    if (t == tree_more && ++s.clean_calls < wipe_cap) return;
    s.debris = t == tree_done ? 0u : 1u;
    job_end(s);
}
#if defined(XYF_CONTENT_SWITCH)
void content_pump();
void config_pump();
#endif
void job_pump() {
    if (package_active || blob_collecting || !job_running()) return;
#if defined(XYF_CONTENT_SWITCH)
    if (job_kind == 2) return config_pump();                             // the config job
    if (job_kind) return content_pump();
#endif
    JobSlot& s = slot();
    const U32 start = now();
    if (s.stage == js_sweep) {                                           // leftovers first
        ++s.extra_steps;
        if (!sweep_step(s.leftovers, s.page)) s.stage = js_assemble;
    } else if (s.stage == js_assemble) {
        mj::Phase p;
        do {
            p = mj::step(s.job);
            const U32 part = xl::p_job_base + mj::timing_part(s.job);
            if (save_times.kind && part != save_times.current) save_lap(part);
        } while (p != mj::Phase::done && p != mj::Phase::failed && p != mj::Phase::cancelled && now() - start < job_slice_ms);
        if (p == mj::Phase::done) {
            s.stage = js_cache; s.cache_index = s.cache_active = 0; job_text(s, "commit"); say("Adding to the library...", s.target);
            save_lap(xl::p_away); save_times.entry_files = s.job.out_count;
        }
        else if (p == mj::Phase::failed || p == mj::Phase::cancelled) job_finish(s);
        else job_say(s, false);
    } else if (s.stage == js_gc) {
        ContentIo io = {s.link};
        const int collected = journal_present ? 1 : xyf::blob::collect_step(s.garbage, io);
        s.link = io.link; ++s.extra_steps;
        if (collected < 0) {
            if (s.garbage.fd >= 0) io.close(s.garbage.fd);
            s.debris = 1; return commit_fail(s, s.link ? P::s_link : P::s_storage, 0, "blob gc");
        }
        if (collected) { job_text(s, s.target); job_finish(s); }
    } else if (s.stage == js_cache) {
        if (s.cache_index == s.job.builder.listed_count) s.stage = js_away;
        else {
            ContentIo io = {s.link};
            if (!s.cache_active) {
                const xyf_core::mod_content::Listed& f = s.job.builder.listed[s.cache_index];
                char source[file_engine::max_path], temporary[file_engine::max_path];
                if (!join(source, s.out, "/", f.path) || !join(temporary, s.out, "/blob.part") ||
                    !xyf::blob::begin(s.cache, source, temporary, f.sha256->bytes))
                    return commit_fail(s, P::s_refused, 0, "blob path");
                s.cache_active = 1;
            }
            const int cached = xyf::blob::step(s.cache, io);
            s.link = io.link;
            if (cached < 0) {
                xyf::blob::abandon(s.cache, io);
                return commit_fail(s, s.link ? P::s_link : P::s_storage, 0, "blob verify");
            }
            if (cached) { ++s.cache_index; s.cache_active = 0; }
            ++s.extra_steps;
        }
    } else if (s.stage == js_wipe) wipe_step(s);                         // a failed job's output
    else commit_step(s);                                                 // into the library
    const U32 pass = now() - start;
    if (pass > s.longest_ms) s.longest_ms = pass;
}

// Part of usb_service.cpp, included there at this point: not compiled alone.
// JOB_STATUS: u32 job (0: the running or last). Reply: u32 status, then the job_status fields.
void job_status(uint8_t seq, const uint8_t* payload, U32 length) {
    if (menu_pack_active || package_active) { status_only(P::t_job_status, seq, P::s_busy); return; }
    JobSlot& s = slot();
    const U32 want = length == 4 ? P::get32(payload) : 0;
#if defined(XYF_CONTENT_SWITCH)
    if (length == 4 && job_kind == 2) return config_status(seq, want);
    if (length == 4 && job_kind == 3) return adopt_status(seq, want);
    if (length == 4 && job_kind) return content_status(seq, want);
#endif
    uint8_t* p = files;
    U32 code = length != 4 ? P::s_bad_request : job_state == job_none || (want && want != s.id) ? P::s_not_found : P::s_ok;
    P::put32(p, code);
    if (code != P::s_ok) { result.last_status = code; send(P::t_job_status, seq, p, 4); return; }
    mj::Progress g;
    mj::progress(s.job, g);
    const bool failed_link = s.link && g.phase == mj::Phase::failed;
    // The commit is phase finish (done = its step, of 3) until it ends; a failed commit is failed.
    const bool committing = job_state == job_busy && s.stage >= js_away && s.stage != js_wipe;
    const bool caching = committing && s.stage >= js_cache;
    const U32 phase = s.commit_error ? U32(mj::Phase::failed) : committing ? U32(mj::Phase::finish) : U32(g.phase);
    const U32 values[11] = {s.id, phase, caching ? (s.stage == js_gc ? 0 : s.cache_index) : committing ? s.stage - js_away + 1 : g.done,
                            caching ? (s.stage == js_gc ? 0 : s.job.builder.listed_count) : committing ? 3u : g.total,
                            s.commit_error ? s.commit_error : failed_link ? job_link_error : U32(g.error),
                            s.commit_error ? s.commit_code : g.status,
                            (job_state == job_busy ? now() : s.ended_ms) - s.started_ms, s.job.steps + s.extra_steps,
                            s.longest_ms, s.job.peak, s.debris};
    for (U32 i = 0; i != 11; ++i) P::put32(p + 4 + 4 * i, values[i]);
    for (U32 i = 0; i != 32; ++i) p[48 + i] = i + 1 < mj::detail_bytes ? uint8_t(s.job.detail[i]) : 0;
    bool end = false;
    for (U32 i = 0; i != 32; ++i) { if (!p[48 + i]) end = true; if (end) p[48 + i] = 0; }
    result.last_status = code;
    send(P::t_job_status, seq, p, 80);
}
// JOB_CANCEL: u32 job (0: the running one). mod_job closes its handle and removes its files,
// job_finish its folders and the window. Reply: u32 status, u32 debris.
void job_cancel(uint8_t seq, const uint8_t* payload, U32 length) {
    if (menu_pack_active || package_active) { status_only(P::t_job_cancel, seq, P::s_state); return; }
    JobSlot& s = slot();
    const U32 want = length == 4 ? P::get32(payload) : 0;
#if defined(XYF_CONTENT_SWITCH)
    if (length == 4 && job_kind) {                                       // a switch is repaired, not cancelled
        const U32 code = want && want != (job_kind == 3 ? aslot().id : cslot().id) ? P::s_not_found : P::s_state;
        uint8_t p[8]; P::put32(p, code); P::put32(p + 4, 0);
        result.last_status = code;
        send(P::t_job_cancel, seq, p, 8);
        return;
    }
#endif
    U32 code = length != 4 ? P::s_bad_request : job_state == job_none || (want && want != s.id) ? P::s_not_found :
               !job_running() || s.stage >= js_away ? P::s_state : P::s_ok;   // not in the commit
    if (code == P::s_ok) { mj::cancel(s.job); job_finish(s); }
    uint8_t p[8]; P::put32(p, code); P::put32(p + 4, code == P::s_bad_request || code == P::s_not_found ? 0 : s.debris);
    result.last_status = code;
    send(P::t_job_cancel, seq, p, 8);
}

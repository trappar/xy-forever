// Part of usb_service.cpp. Recovery runs only in the menu/service loop, never on the plain boot path.
#if defined(XYF_CONTENT_SWITCH)
void intent_failure(ContentSlot& c, const char* why) {
    if (c.source.fd >= 0 && !c.io.lost()) c.io.close(c.source.fd);
    c.source.fd = -1;
    if (c.intent_file.fd >= 0 && !c.io.lost()) c.io.close(c.intent_file.fd);
    c.intent_file.fd = -1;
    xc::fail(c.job, c.io.lost() ? xc::e_link : xj::same(why, "journal write") ? xc::e_write : xc::e_content, 0, why);
    if (journal_present) recovery_offer = 1;
    c.stage = cs_switch; // ordinary failure/close path keeps USB and job.status alive
}
void intent_pump(ContentSlot& c) {
    if (c.stage == cs_validate) {
        const int r = xj::source_step(c.source, c.io);
        if (!r) return;
        const xj::Identity& expected = c.intent.undo ? c.intent.from : c.intent.to;
        if (r < 0 || !xj::equal(c.source.identity, expected)) { intent_failure(c, "source missing or changed"); return; }
        if (c.prepare_step == 4) { c.stage = cs_prepare; return; }
        c.prepared = 1; c.stage = cs_switch; return;
    }
    if (!c.prepare_step) {
        if (c.replay) { c.prepared = 1; c.stage = cs_switch; return; }
        xj::copy(c.intent.target, c.job.target);
        const char* previous = c.job.state.kind == xc::installed ? c.job.state.entry :
                               c.job.state.kind == xc::official && c.job.candidate < c.job.candidate_count ? c.job.candidates[c.job.candidate] : "";
        if (!xyf::store::valid_id(previous)) {
            const xyf::catalog::Catalog* cat = xyf_boot::catalog();
            if (cat && cat->default_index < cat->count) previous = cat->items[cat->default_index].id;
        }
        if (!xyf::store::valid_id(previous)) { intent_failure(c, "no previous entry"); return; }
        xj::copy(c.intent.previous, previous); c.intent.write_default = 1; // Save also commits this default after its configuration record
        c.intent.undo = 0;
        xj::source_begin(c.source, c.intent.target); c.prepare_step = 1; return;
    }
    if (c.prepare_step < 3) {
        const int r = xj::source_step(c.source, c.io); if (!r) return;
        if (r < 0) { intent_failure(c, "source missing or changed"); return; }
        if (c.prepare_step == 1) { c.intent.to = c.source.identity; xj::source_begin(c.source, c.intent.previous); c.prepare_step = 2; return; }
        c.intent.from = c.source.identity; xj::begin(c.intent_file); c.intent_file.n = xj::format(c.intent, c.intent_file.text); c.prepare_step = 3; return;
    }
    const int r = xj::write_step(c.intent_file, c.io); if (!r) return;
    if (r < 0) { intent_failure(c, "journal write"); return; }
    recovery_record = c.intent; journal_present = 1; c.prepared = 1; c.stage = cs_switch;
}
bool content_pending(const char* id) { return journal_present && (xj::same(id, recovery_record.previous) || xj::same(id, recovery_record.target)); }
U32 content_begin(const char* id, bool menu, bool write_default) {
    if (recovery_pending || recovery_offer >= 2) return P::s_busy;
    if (!journal_present) return content_start(id, menu, write_default, false);
    if (job_running() || closing XYF_OR_MTP_BUSY || uploading()) return P::s_busy;
    if (!xj::same(id, recovery_record.previous) && !xj::same(id, recovery_record.target)) return P::s_state;
    // Persist the undo direction before its first live change. Interrupted undo replays undo.
    const bool undo = xj::same(id, recovery_record.previous);
    const U32 code = content_start(id, menu, undo ? true : recovery_record.write_default != 0, true);
    if (code != P::s_ok) return code;
    ContentSlot& c = cslot(); c.intent.undo = undo ? 1u : 0u;
    if (c.intent.undo != recovery_record.undo) {
        // source validation first, then publication, then the existing content job
        c.prepared = 0; c.replay = 0; c.prepare_step = 4;
        xj::begin(c.intent_file); c.intent_file.n = xj::format(c.intent, c.intent_file.text);
    }
    recovery_offer = 0; return P::s_ok;
}
volatile U32 recovery_scan __attribute__((section(".data"))) = 0;
void recovery_turn() {
    if (!recovery_pending) return;
    if (closing || uploading() XYF_OR_MTP_BUSY || !menu_link::usb_idle() ||
        xyf_boot::storage_view() != xyf::menu::storage_ready) return;
    ContentSlot& c = cslot();
    if (!recovery_scan) {
        if (!a_serving() || file_client::mount_ready(mounted) != P::s_ok) return;
        c.io.link = 0; xj::begin(c.intent_file); recovery_scan = 1; return;
    }
    const int r = xj::read_step(c.intent_file, c.io); if (!r) return;
    recovery_pending = 0;
    if (r == 2) return;
    journal_present = 1;
    const int parsed = r < 0 ? -1 : xj::parse(c.intent_file.text, c.intent_file.n, recovery_record);
    if (parsed < 0) { recovery_offer = parsed == -2 ? 3u : 2u; say("Content journal needs attention", parsed == -2 ? "Newer journal version" : "Invalid journal; kept unchanged"); return; }
    const char* id = recovery_record.undo ? recovery_record.previous : recovery_record.target;
    const U32 code = content_start(id, true, recovery_record.undo || recovery_record.write_default, true);
    if (code != P::s_ok) { recovery_offer = 1; return; }
    cslot().prepared = 1;
}
#else
bool content_pending(const char*) { return false; }
void recovery_turn() { recovery_pending = 0; }
#endif

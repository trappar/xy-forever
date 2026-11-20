// Part of xyf_mods_io.cpp: the saved configuration (xyf_config.hpp) and the save sequence, included in place (in
// its anonymous namespace).
namespace xg = xyf::config;
enum SaveStage : uint32_t {
    sv_idle = 0, sv_reading, sv_check, sv_build, sv_stamp, sv_switch, sv_switching, sv_record, sv_recording, sv_boot,
    sv_failed
};
struct Save {
    uint32_t stage, job, serial, unverified, count;
    char target[32], base[32], built[100], slot[8], stamp[65], reason[80];
};
xg::Record rec XYF_DATA = {};
Save sv XYF_DATA = {};
char label[48] XYF_DATA = {};
static const char firmware[] = "/yaffs2/xy-forever/firmware/";
uint32_t index_of(const char* id) {
    const xyf::catalog::Catalog* c = xyf_boot::catalog();
    for (uint32_t i = 0; c && id[0] && i != c->count; ++i) if (xg::same(c->items[i].id, id)) return i;
    return ~0u;
}
// The base of the configuration: the record's when it is a stock entry of the library, else base_entry().
const char* config_base() {
    const xyf::catalog::Catalog* c = xyf_boot::catalog();
    const uint32_t i = rec.state == xg::present ? index_of(rec.base) : ~0u;
    if (c && i < c->count && c->items[i].kind == xyf::store::kind_official &&
        (c->items[i].flags & (xyf::catalog::flag_usable | xyf::catalog::flag_content_missing)))
        return c->items[i].id;
    return base_entry();
}
// The record (config.txt, else config.old, else none) into rec; flag_saved on the listed packages it names (and
// flag_on too: toggles, when the list was just read). A status but s_ok: rec unchanged.
uint32_t read_record(bool toggles) {
    uint32_t bytes = 0;
    uint32_t code = fc::mount_ready(usb_service::page_mount());
    if (code == s_ok) code = fc::read_file(xg::path, 0, false, io.buffer, read_bytes, bytes, usb_service::page_mount());
    if (code == xyf::usbp::s_not_found)
        code = fc::read_file(xg::old_path, 0, false, io.buffer, read_bytes, bytes, usb_service::page_mount());
    if (code == xyf::usbp::s_not_found) { xg::clear(rec, xg::none); bytes = 0; }
    else if (code != s_ok) return code;
    else xg::parse(reinterpret_cast<const char*>(io.buffer), bytes, rec);
    m::Page& p = the_page;
    uint32_t marked = 0;
    for (uint32_t r = 0; r != p.shown; ++r) {
        m::Package& k = p.packages[p.rows[r]];
        const bool on = rec.state == xg::present &&
                        xg::listed(reinterpret_cast<const char*>(io.buffer), bytes, k.kind == m::kind_extension ? "ext-" : "",
                                   m::text_at(p, k.id));
        k.flags = uint8_t((k.flags & ~m::flag_saved) | (on ? m::flag_saved : 0));
        if (toggles) k.flags = uint8_t((k.flags & ~m::flag_on) | (on ? m::flag_on : 0));
        marked += on ? 1u : 0u;
    }
    p.saved_missing = rec.count > marked ? rec.count - marked : 0u;
    // The saved memory layout; the pending choice follows it unless the user changed it (Settings may
    // come before the list is read)
    const bool kept = p.expanded != p.expanded_saved;
    p.expanded_saved = rec.state == xg::present && rec.expanded;
    if (!kept) p.expanded = p.expanded_saved;
    return s_ok;
}
// The stamp (info.txt sha256=) of library entry id into out (65 bytes); "" when it cannot be read.
void entry_stamp(const char* id, char* out) {
    uint32_t bytes = 0;
    const char* parts[3] = {firmware, id, "/info.txt"};
    out[0] = 0;
    if ((io.why = usb_service::page_serving()) != s_ok || (io.why = fc::mount_ready(usb_service::page_mount())) != s_ok) return;
    if (path_of(parts, 3) && read_text(io.path, bytes)) xg::stamp_of(reinterpret_cast<const char*>(io.buffer), bytes, out);
}
uint32_t fail(const char* reason) {
    m::Page& p = the_page;
    copy(sv.reason, reason, sizeof(sv.reason));
    sv.stage = sv_failed;
    usb_service::save_log_drop();               // a failed Save writes no line
    p.view = m::View::list; p.save = p.save_stage = 0; p.request = m::request_none;
    m::say(p, m::line_error, sv.reason);
    rec.state = xg::unread;                     // read again: the record or the default may have changed
    return 1;
}
// The building view's bar every progress_ms (2), or a redraw (1) on a stage or phase change.
uint32_t moved(bool phase) {
    m::Page& p = the_page;
    const uint32_t now = units();
    m::progress(p, xyf::clock::ms_from_units(now - io.job_units));
    if (phase) return 1;
    return now - io.drawn >= progress_units && (p.bar != io.drawn_bar || p.job_done != io.drawn_done) ? 2u : 0u;
}
uint32_t check() {                              // what the save needs: the build, or the slot as it is
    m::Page& p = the_page;
    usb_service::save_log_lap(xyf::save_log::p_check);
    if (const char* why = m::save_refusal(p)) return fail(why);
    sv.count = m::chosen(p, false) + m::chosen(p, true);
    const char* base = config_base();
    if (!base) return fail("Save failed: no stock OP-XY firmware in the library.");
    copy(sv.base, base, sizeof(sv.base));
    uint32_t set = 0;
    for (uint32_t r = 0; r != p.shown; ++r) {
        const m::Package& k = p.packages[p.rows[r]];
        if (!(k.flags & m::flag_on)) continue;
        const char* id = m::text_at(p, k.id);
        uint32_t n = 0;
        while (id[n]) ++n;
        const uint32_t crc = xyf::crc::update(k.kind == m::kind_extension ? xyf::crc::update(0, reinterpret_cast<const uint8_t*>("ext-"), 4) : 0u,
                                              reinterpret_cast<const uint8_t*>(id), n);
        set += crc;
    }
    uint32_t added = 0, removed = 0;            // against the saved selection
    for (uint32_t r = 0; r != p.shown; ++r) {
        const uint8_t f = p.packages[p.rows[r]].flags;
        added += (f & m::flag_on) && !(f & m::flag_saved);
        removed += (f & m::flag_saved) && !(f & m::flag_on);
    }
    usb_service::save_log_selection(sv.count, p.expanded, added, removed);
    xg::built_text(sv.built, sizeof(sv.built), base, p.generation, set, sv.count, p.expanded);
    sv.unverified = p.unknown_ok || (rec.state == xg::present && rec.unverified && xg::same(rec.base, base)) ? 1u : 0u;
    sv.slot[0] = sv.stamp[0] = 0;
    if (!sv.count) { copy(sv.target, base, sizeof(sv.target)); sv.stage = sv_switch; return 1; }
    // The rule: no rebuild when the slot the record names is in the library, was built from this base, package
    // set and selection, and still holds that firmware (its info.txt sha256= is the record's stamp).
    if (rec.state == xg::present && rec.slot[0] && rec.stamp[0] && xg::same(rec.built, sv.built) && index_of(rec.slot) != ~0u) {
        entry_stamp(rec.slot, sv.stamp);
        if (io.why == xyf::usbp::s_link) return fail("Save failed: no answer from storage.");
        if (xg::same(sv.stamp, rec.stamp)) {
            copy(sv.slot, rec.slot, sizeof(sv.slot)); copy(sv.target, rec.slot, sizeof(sv.target));
            sv.stage = sv_switch;
            return 1;
        }
    }
    if (!usb_service::home_usb()) return fail("Save failed: the USB service is off (it runs the build).");
    const uint32_t n = m::request(p, base, sv.unverified != 0, p.job_name, sizeof(p.job_name), io.buffer, read_bytes - 32);
    if (!n) return fail("Save failed: too many mods for one request.");
    xyf_core::hash::Sha256 h;
    xyf_core::hash::Digest d;
    if (!h.update(io.buffer, n) || !h.finish(d)) return fail("Save failed: the request could not be made.");
    for (uint32_t i = 0; i != 32; ++i) io.buffer[n + i] = d.bytes[i];
    uint32_t job = 0, missing = 0;
    const uint32_t code = usb_service::mods_start(io.buffer, n + 32, job, missing);
    if (code != s_ok) { m::start_refused(p, code, missing); return fail(p.text); }
    sv.job = job; sv.stage = sv_build;
    p.save |= m::save_build;
    p.job_phase = p.job_done = p.job_total = p.job_commit = 0;
    return 1;
}
uint32_t building() {                           // the mod job into the spare slot (STOP: job.cancel's stop)
    m::Page& p = the_page;
    usb_service::JobView v;
    usb_service::job_view(v);
    bool refused = false;
    if (p.request == m::request_cancel && v.state == 1 && v.id == sv.job) {
        if (usb_service::job_stop() != s_ok) { m::refuse_cancel(p); refused = true; }   // in the commit
        usb_service::job_view(v);
    }
    if (v.state != 1 || v.id != sv.job) {
        if (v.state == 2 && v.id == sv.job && v.phase == 8) {
            copy(sv.slot, usb_service::job_target(), sizeof(sv.slot));
            sv.stage = sv_stamp;
            return 1;
        }
        if (v.state == 2 && v.id == sv.job) m::job_ended(p, v.phase, v.error, v.status, v.debris);
        else m::say(p, m::line_error, "The build ended.");
        return fail(p.text);
    }
    const bool phase = v.phase != p.job_phase || v.commit != p.job_commit;
    p.job_phase = v.phase; p.job_done = v.done; p.job_total = v.total; p.job_commit = v.commit;
    return moved(refused || phase);
}
// One step of the save sequence (as pump; every storage request bounded by the file client, jobs in slices).
uint32_t save_follow() {
    m::Page& p = the_page;
    switch (sv.stage) {
    case sv_reading:                            // the list (and the saved toggles) first
        if (io.step != s_done) return 0;
        if (p.line == m::line_error) return fail(p.text);
        p.view = m::View::building; p.save = m::save_on; p.save_stage = 0;
        p.job = 0x10000u + sv.serial; p.job_phase = p.job_done = p.job_total = p.job_commit = 0;
        p.bar = p.bar_step = p.bar_since = 0; p.request = m::request_none; p.line = m::line_none;
        io.job_units = units();
        sv.stage = sv_check;
        return 1;
    case sv_check:
        if (p.request == m::request_cancel) return fail("Cancelled. Nothing was saved.");
        return check();
    case sv_build:
        return building();
    case sv_stamp:
        if (usb_service::page_busy()) return moved(false);   // the commit's window closes
        usb_service::save_log_lap(xyf::save_log::p_stamp);
        entry_stamp(sv.slot, sv.stamp);
        if (!sv.stamp[0]) {
            char d[12];
            m::decimal(d, io.why);
            m::say(p, m::line_error, "Save failed: the new Open-XY firmware could not be read back (", d, ").");
            return fail(p.text);
        }
        copy(sv.target, sv.slot, sizeof(sv.target));
        sv.stage = sv_switch;
        return 1;
    case sv_switch: {
        if (usb_service::page_busy()) return moved(false);
        const xyf::catalog::Catalog* c = xyf_boot::catalog();
        const uint32_t i = index_of(sv.target);
        if (!c || i >= c->count) return fail("Save failed: the firmware is not in the library.");
        if ((c->items[i].flags & xyf::catalog::flag_usable) && !(c->items[i].flags & xyf::catalog::flag_content_missing)) {
            sv.stage = sv_record;                // its content is installed already
            return 1;
        }
        if (usb_service::content_switch(sv.target) != s_ok) return fail("Save failed: installing content could not start.");
        usb_service::JobView v;
        usb_service::job_view(v);
        sv.job = v.id; sv.stage = sv_switching;
        p.save_stage = 1; p.job_done = p.job_total = 0; p.request = m::request_none;
        return 1;
    }
    case sv_switching: {
        usb_service::JobView v;
        usb_service::job_view(v);
        if (v.state == 1 && v.id == sv.job) {
            const bool phase = v.done != p.job_done && !p.job_total;
            p.job_done = v.done; p.job_total = v.total;
            return moved(phase);
        }
        if (v.id == sv.job && v.state == 2 && v.phase == 8) { sv.stage = sv_record; return 1; }
        return fail("Installing content failed: Continue and Save again to repair it.");
    }
    case sv_record: {
        if (usb_service::page_busy()) return moved(false);
        xg::Record r;
        xg::clear(r, xg::present);
        copy(r.base, sv.base, sizeof(r.base)); copy(r.profile, p.profile, sizeof(r.profile));
        copy(r.generation, p.generation, sizeof(r.generation));
        r.unverified = sv.unverified;
        r.expanded = p.expanded ? 1u : 0u;
        const bool built = sv.count != 0;       // all off: the Open-XY slot stays as the record had it (spare)
        copy(r.built, built ? sv.built : rec.built, sizeof(r.built));
        copy(r.slot, built ? sv.slot : rec.slot, sizeof(r.slot));
        copy(r.stamp, built ? sv.stamp : rec.stamp, sizeof(r.stamp));
        char* const text = reinterpret_cast<char*>(io.buffer);
        uint32_t n = xg::format(text, read_bytes, r);
        bool first = true, fits = n != 0;
        for (uint32_t pass = 0; pass != 2; ++pass)
            for (uint32_t k = 0; k != p.shown; ++k) {
                const m::Package& q = p.packages[p.rows[k]];
                if (!(q.flags & m::flag_on) || (q.kind == m::kind_extension) != (pass == 1)) continue;
                const char* parts[3] = {first ? "" : ",", pass ? "ext-" : "", m::text_at(p, q.id)};
                for (uint32_t j = 0; j != 3; ++j)
                    for (const char* t = parts[j]; *t; ++t) { if (n + 2 >= read_bytes) fits = false; else text[n++] = *t; }
                first = false;
            }
        text[n++] = '\n';
        if (!fits) return fail("Save failed: the settings do not fit.");
        if (usb_service::config_store(text, n, sv.target) != s_ok) return fail("Save failed: storage is busy.");
        usb_service::JobView v;
        usb_service::job_view(v);
        sv.job = v.id; sv.stage = sv_recording;
        p.save_stage = 2; p.job_done = p.job_total = 0;
        return 1;
    }
    case sv_recording: {
        usb_service::JobView v;
        usb_service::job_view(v);
        if (v.state == 1 && v.id == sv.job) return moved(false);
        if (v.id == sv.job && v.state == 2 && v.phase == 8) { sv.stage = sv_boot; return 1; }
        return fail("Saving the settings failed: the previous setup is kept.");
    }
    default:
        return 0;
    }
}

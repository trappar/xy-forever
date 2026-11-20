// Part of xyf_mods_io.cpp: the saved configuration (xyf_config.hpp), the Mods page's own write of it and the converge
// sequence (#197, xyf_converge.hpp), included in place (in its anonymous namespace).
namespace xg = xyf::config;
namespace xc = xyf::converge;
enum SaveStage : uint32_t {
    sv_idle = 0, sv_reading, sv_check, sv_listing, sv_build, sv_stamp, sv_switch, sv_switching, sv_record, sv_recording,
    sv_mark, sv_marking, sv_boot, sv_failed
};
// The sequence: why (converge_begin's: which inputs it set, for its texts only), the inputs (count, expanded, safe,
// unverified, base, built), the plan (ops, target), the slot and stamp the record names (a build's new ones), the
// record made for the record step (length bytes in io.buffer).
struct Save {
    uint32_t stage, job, serial, why, ops, unverified, count, expanded, safe, length;
    char target[32], base[32], built[100], slot[8], stamp[65], reason[80];
};
xg::Record rec XYF_DATA = {};
Save sv XYF_DATA = {};
m::Progress run XYF_DATA = {};                   // the progress screen
char label[48] XYF_DATA = {};
char safe_text[32] XYF_DATA = {};                // the safe-mode row
uint32_t rec_set XYF_DATA = 0;                   // the record's mods= names: their order-free CRC-32 sum (built=)
// The page's own write (#197): a flip (a toggle, Mod memory, unknown bases) is saved to config.txt at a quiet moment,
// quiet_ms after the last flip (quick flips: one write), by the config job (the default unchanged). flipped: 1, a flip
// not written yet; 2, its write failed (the next flip or a converge writes it); since: the last flip; stage 1: the
// job (job) runs; 2: written, the record not read back yet (rec keeps the old one meanwhile: no pending=0 between).
struct Pick { uint32_t flipped, since, stage, job; };
Pick pick XYF_DATA = {};
static const char firmware[] = "/yaffs2/xy-forever/firmware/";
uint32_t index_of(const char* id) {
    const xyf::catalog::Catalog* c = xyf_boot::catalog();
    for (uint32_t i = 0; c && id[0] && i != c->count; ++i) if (xg::same(c->items[i].id, id)) return i;
    return ~0u;
}
// Library entry i's content is installed (it is usable and nothing is missing).
bool installed(uint32_t i) {
    const xyf::catalog::Catalog* c = xyf_boot::catalog();
    return c && i < c->count && (c->items[i].flags & xyf::catalog::flag_usable) &&
           !(c->items[i].flags & xyf::catalog::flag_content_missing);
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
// The record (config.txt, else config.old, else none) into rec, its text left in io.buffer (bytes); flag_saved on the
// listed packages it names (and flag_on too: toggles, when the list was just read). A status but s_ok: rec unchanged.
uint32_t read_record(bool toggles, uint32_t& bytes) {
    bytes = 0;
    uint32_t code = fc::mount_ready(usb_service::page_mount());
    if (code == s_ok) code = fc::read_file(xg::path, 0, false, io.buffer, read_bytes, bytes, usb_service::page_mount());
    if (code == xyf::usbp::s_not_found)
        code = fc::read_file(xg::old_path, 0, false, io.buffer, read_bytes, bytes, usb_service::page_mount());
    if (code == xyf::usbp::s_not_found) { xg::clear(rec, xg::none); bytes = 0; }
    else if (code != s_ok) return code;
    else xg::parse(reinterpret_cast<const char*>(io.buffer), bytes, rec);
    uint32_t n = 0;                             // built= of the saved selection takes its names' CRC-32 sum
    const char* list = rec.state == xg::present ? xg::value(reinterpret_cast<const char*>(io.buffer), bytes, "mods", n) : 0;
    rec_set = 0;
    for (uint32_t at = 0; list && at < n;) {
        uint32_t end = at;
        while (end < n && list[end] != ',') ++end;
        if (end > at) rec_set += xyf::crc::update(0, reinterpret_cast<const uint8_t*>(list + at), end - at);
        at = end + 1;
    }
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
    if (pick.stage == 2) pick.stage = 0;          // the page's write read back
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
// The sequence ends without booting: the reason for the home menu's status line (converge_reason).
uint32_t fail(const char* a, const char* b = "", const char* c = "") {
    xg::Out o = {sv.reason, sv.reason + sizeof(sv.reason) - 1, true};
    xg::put(o, a); xg::put(o, b); xg::put(o, c);
    *o.at = 0;
    sv.stage = sv_failed;
    usb_service::save_log_drop();               // a failed sequence writes no line
    rec.state = xg::unread;                     // read again: the record or the default may have changed
    return 1;
}
const char* prefix() { return "Failed: "; }
const char* again() { return sv.why == why_safe ? "Boot without mods" : sv.why == why_restore ? "Restore Open-XY" : "Continue"; }
// The progress bar every progress_ms (2), or a redraw (1) on a stage or phase change.
uint32_t moved(bool phase) {
    const uint32_t now = units();
    m::progress(run, xyf::clock::ms_from_units(now - io.job_units));
    if (phase) return 1;
    return now - io.drawn >= progress_units && (run.bar != io.drawn_bar || run.job_done != io.drawn_done) ? 2u : 0u;
}
// The page's selection (which: flag_saved, the saved marks): how many, and built= for it (the order-free sum of each name's CRC-32, as xg::built_text takes it) into sv.built.
uint32_t selection(uint8_t which, const char* base) {
    const m::Page& p = the_page;
    uint32_t set = 0, count = 0;
    for (uint32_t r = 0; r != p.shown; ++r) {
        const m::Package& k = p.packages[p.rows[r]];
        if (!(k.flags & which)) continue;
        const char* id = m::text_at(p, k.id);
        uint32_t n = 0;
        while (id[n]) ++n;
        set += xyf::crc::update(k.kind == m::kind_extension ? xyf::crc::update(0, reinterpret_cast<const uint8_t*>("ext-"), 4) : 0u,
                                reinterpret_cast<const uint8_t*>(id), n);
        ++count;
    }
    xg::built_text(sv.built, sizeof(sv.built), base, p.generation, set, count, sv.expanded != 0);
    return count;
}
// The saved inputs (the record just read): built= from its mods= line (rec_set).
void saved_inputs(const char* base) {
    sv.count = rec.count; sv.expanded = rec.expanded; sv.unverified = rec.unverified;
    sv.safe = sv.why == why_safe ? 1u : sv.why == why_restore ? 0u : rec.safe;
    xg::built_text(sv.built, sizeof(sv.built), base, rec.generation, rec_set, sv.count, sv.expanded != 0);
}
// The build of the page's selection (which) into the spare slot: mods.apply's own start.
uint32_t start_build(uint8_t which) {
    m::Page& p = the_page;
    if (!usb_service::home_usb()) return fail(prefix(), "the USB service is off (it runs the build).");
    const uint32_t n = m::request(p, sv.base, sv.unverified != 0, sv.expanded != 0, which, run.job_name,
                                  sizeof(run.job_name), io.buffer, read_bytes - 32);
    if (!n) return fail(prefix(), "too many mods for one request.");
    xyf_core::hash::Sha256 h;
    xyf_core::hash::Digest d;
    if (!h.update(io.buffer, n) || !h.finish(d)) return fail(prefix(), "the request could not be made.");
    for (uint32_t i = 0; i != 32; ++i) io.buffer[n + i] = d.bytes[i];
    uint32_t job = 0, missing = 0;
    const uint32_t code = usb_service::mods_start(io.buffer, n + 32, job, missing);
    if (code != s_ok) { m::start_refused(p, code, missing); return fail(p.text); }
    sv.job = job; sv.stage = sv_build;
    run.save |= m::save_build;
    run.job_phase = run.job_done = run.job_total = run.job_commit = 0;
    return 1;
}
// The inputs (the saved record, with safe= as asked), the actual state, the plan.
uint32_t check() {
    usb_service::save_log_lap(xyf::save_log::p_check);
    uint32_t bytes = 0;
    const uint32_t code = read_record(false, bytes);
    if (code != s_ok) { char d[12]; m::decimal(d, code); return fail("Failed: the settings could not be read (", d, ")."); }
    const bool have = rec.state == xg::present;
    if (sv.why == why_safe && !(have && rec.count && rec.slot[0])) return fail("No Open-XY setup with mods is saved.");
    if (sv.why == why_restore && !(have && rec.count)) return fail("No Open-XY setup to restore.");
    const char* base = config_base();
    if (!base) return fail(prefix(), "no stock OP-XY firmware in the library.");
    saved_inputs(base);
    copy(sv.base, base, sizeof(sv.base));
    // The actual state: the slot's stamp read now (the record's slot), the default, the installed content.
    copy(sv.slot, have ? rec.slot : "", sizeof(sv.slot)); copy(sv.stamp, "", sizeof(sv.stamp));
    if (have && rec.slot[0] && rec.stamp[0] && index_of(rec.slot) != ~0u) {
        entry_stamp(rec.slot, sv.stamp);
        if (io.why == xyf::usbp::s_link) return fail(prefix(), "no answer from storage.");
    }
    const xyf::catalog::Catalog* c = xyf_boot::catalog();
    const xc::Inputs in = {sv.base, sv.built, sv.count, sv.expanded, sv.safe};
    const xc::Actual a = {&rec, sv.stamp, c && c->default_index < c->count ? c->items[c->default_index].id : "",
                          installed(index_of(sv.base)), installed(have ? index_of(rec.slot) : ~0u)};
    const xc::Plan plan = xc::plan(in, a);
    sv.ops = plan.ops;
    copy(sv.target, plan.target ? plan.target : "", sizeof(sv.target));
    copy(sv.stamp, have ? rec.stamp : "", sizeof(sv.stamp));   // the record's slot and stamp, unless a build replaces them
    if (!sv.ops) { sv.stage = sv_mark; return 1; }    // nothing differs: it boots (the fingerprint written if it differs)
    if (!(sv.ops & xc::op_build)) { sv.stage = sv_switch; return 1; }
    sv.stage = sv_listing;                      // the saved selection's build needs the package list
    return 1;
}
uint32_t building() {                           // the mod job into the spare slot (STOP: job.cancel's stop)
    m::Page& p = the_page;
    usb_service::JobView v;
    usb_service::job_view(v);
    bool refused = false;
    if (run.cancel == 1 && v.state == 1 && v.id == sv.job) {
        if (usb_service::job_stop() != s_ok) { m::refuse_cancel(run); refused = true; }   // in the commit
        else run.cancel = 2;                    // asked once
        usb_service::job_view(v);
    }
    if (v.state != 1 || v.id != sv.job) {
        if (v.state == 2 && v.id == sv.job && v.phase == 8) {
            copy(sv.slot, usb_service::job_target(), sizeof(sv.slot));
            sv.stage = sv_stamp;
            return 1;
        }
        if (v.state == 2 && v.id == sv.job) m::job_ended(p, run.job_name, v.phase, v.error, v.status, v.debris);
        else m::say(p, m::line_error, "The build ended.");
        return fail(p.text);
    }
    const bool phase = v.phase != run.job_phase || v.commit != run.job_commit;
    run.job_phase = v.phase; run.job_done = v.done; run.job_total = v.total; run.job_commit = v.commit;
    return moved(refused || phase);
}
// Record r and the mods= line of the page's packages with which (flag_on: the toggles; flag_saved: the saved marks)
// into io.buffer: bytes, 0 when it does not fit.
uint32_t record_text(const xg::Record& r, uint8_t which) {
    const m::Page& p = the_page;
    char* const text = reinterpret_cast<char*>(io.buffer);
    uint32_t n = xg::format(text, read_bytes, r);
    bool first = true, fits = n != 0;
    for (uint32_t pass = 0; pass != 2; ++pass)
        for (uint32_t k = 0; k != p.shown; ++k) {
            const m::Package& q = p.packages[p.rows[k]];
            if (!(q.flags & which) || (q.kind == m::kind_extension) != (pass == 1)) continue;
            const char* parts[3] = {first ? "" : ",", pass ? "ext-" : "", m::text_at(p, q.id)};
            for (uint32_t j = 0; j != 3; ++j)
                for (const char* t = parts[j]; *t; ++t) { if (n + 2 >= read_bytes) fits = false; else text[n++] = *t; }
            first = false;
        }
    text[n++] = '\n';
    return fits ? n : 0u;
}
// The record the plan writes, into io.buffer (sv.length bytes; 0: fail said why): the inputs, the slot and stamp (the
// build's, else the record's), and the mods= line: the saved marks after a build of the saved selection, else as
// saved (config.txt read again, else config.old).
uint32_t compose() {
    const m::Page& p = the_page;
    const bool listed = (sv.ops & xc::op_build) != 0;
    xg::Record r;                               // field by field: a struct copy is a memcpy call on Blackfin
    xg::clear(r, xg::present);
    copy(r.base, sv.base, sizeof(r.base));
    copy(r.profile, listed ? p.profile : rec.profile, sizeof(r.profile));
    copy(r.generation, listed ? p.generation : rec.generation, sizeof(r.generation));
    copy(r.built, listed ? sv.built : rec.built, sizeof(r.built));
    copy(r.slot, sv.slot, sizeof(r.slot)); copy(r.stamp, sv.stamp, sizeof(r.stamp));
    r.unverified = sv.unverified; r.expanded = sv.expanded; r.count = sv.count;
    r.safe = sv.safe && sv.count && r.slot[0] ? 1u : 0u;    // as xg::parse reads it back
    char* const text = reinterpret_cast<char*>(io.buffer);
    uint32_t n = 0;
    if (listed) {
        if (!(sv.length = record_text(r, m::flag_saved))) return fail(prefix(), "the settings do not fit.");
        return 1;
    }
    uint32_t bytes = 0;
    uint32_t code = fc::mount_ready(usb_service::page_mount());
    if (code == s_ok) code = fc::read_file(xg::path, 0, false, io.buffer, read_bytes, bytes, usb_service::page_mount());
    if (code == xyf::usbp::s_not_found)
        code = fc::read_file(xg::old_path, 0, false, io.buffer, read_bytes, bytes, usb_service::page_mount());
    const char* saved = code == s_ok ? xg::value(text, bytes, "mods", n) : 0;
    char head[400];
    const uint32_t h = saved ? xg::format(head, sizeof(head), r) : 0u;
    if (!h || h + n + 1 > xg::max_text) {
        char d[12];
        m::decimal(d, code == s_ok ? 1000u : code);
        return fail("Failed: the settings could not be read (", d, ").");
    }
    uint8_t* const b = io.buffer;               // the mods= value moved behind the new head, then the head
    const uint32_t from = uint32_t(reinterpret_cast<const uint8_t*>(saved) - b);
    if (h > from) for (uint32_t i = n; i; --i) b[h + i - 1] = b[from + i - 1];
    else for (uint32_t i = 0; i != n; ++i) b[h + i] = b[from + i];
    for (uint32_t i = 0; i != h; ++i) b[i] = uint8_t(head[i]);
    b[h + n] = '\n';
    sv.length = h + n + 1;
    return 1;
}
// One step of the page's own write (Pick). now: without waiting for the quiet moment (a converge writes a flip first).
// 0: nothing left to write; 1: waiting or writing; 2: it could not be written (pick.flipped stays).
uint32_t pick_step(bool now) {
    if (pick.stage == 1) {                      // the config job
        usb_service::JobView v;
        usb_service::job_view(v);
        if (v.state == 1 && v.id == pick.job) return 1;
        const bool done = v.id == pick.job && v.state == 2 && v.phase == 8;
        pick.stage = done ? 2u : 0u;            // read back (pump, or a converge's check): the saved marks follow
        if (!done) { if (!pick.flipped) pick.flipped = 2; return 2; }
    }
    if (!pick.flipped) return 0;
    if (!now && (pick.flipped == 2 || units() - pick.since < quiet_units)) return 1;
    const m::Page& p = the_page;
    if (io.step != s_done || usb_service::page_busy() || usb_service::page_serving() != s_ok) return 1;   // the toggles
    if (!p.limit || io.incomplete) { pick.flipped = 2; return 2; }   // no complete list: nothing to save from
    uint32_t bytes = 0;
    if (rec.state != xg::present && rec.state != xg::none && read_record(false, bytes) != s_ok) { pick.flipped = 2; return 2; }
    const char* base = config_base();
    if (!base) { pick.flipped = 2; return 2; }
    // The selection and the inputs beside it; what is built (built=, slot=, stamp=) and safe mode stay as saved.
    const bool have = rec.state == xg::present;
    xg::Record r;
    xg::clear(r, xg::present);
    copy(r.base, base, sizeof(r.base));
    copy(r.profile, p.profile, sizeof(r.profile)); copy(r.generation, p.generation, sizeof(r.generation));
    copy(r.built, have ? rec.built : "", sizeof(r.built));
    copy(r.slot, have ? rec.slot : "", sizeof(r.slot)); copy(r.stamp, have ? rec.stamp : "", sizeof(r.stamp));
    r.unverified = p.unknown_ok || (have && rec.unverified && xg::same(rec.base, base)) ? 1u : 0u;
    r.expanded = p.expanded ? 1u : 0u;
    r.count = m::chosen(p, false) + m::chosen(p, true);
    r.safe = have && rec.safe && r.count && r.slot[0] ? 1u : 0u;
    const uint32_t n = record_text(r, m::flag_on);
    const uint32_t code = n ? usb_service::config_store(reinterpret_cast<const char*>(io.buffer), n, "") : 1u;
    if (code == xyf::usbp::s_busy) return 1;
    if (code != s_ok) { pick.flipped = 2; return 2; }
    usb_service::JobView v;
    usb_service::job_view(v);
    pick.job = v.id; pick.stage = 1; pick.flipped = 0;
    return 1;
}
// The fingerprint (xyf_converge.hpp, #197 step B): the record's bytes as A reads them at power-on (config.txt, else
// config.old, else none) hashed, with the default it converged to (sv.target); the config job writes it (quiet: no
// catalog fetch) only when the stored text differs. Unreadable: nothing stored (the next power-on converges again).
uint32_t mark() {
    uint32_t bytes = 0, got = 0, n = 0;
    uint32_t code = usb_service::page_serving();         // A answers file requests again (the catalog's item ended that)
    if (code == s_ok) code = fc::mount_ready(usb_service::page_mount());
    if (code == s_ok) code = fc::read_file(xg::path, 0, false, io.buffer, read_bytes, bytes, usb_service::page_mount());
    if (code == xyf::usbp::s_not_found)
        code = fc::read_file(xg::old_path, 0, false, io.buffer, read_bytes, bytes, usb_service::page_mount());
    if (code == xyf::usbp::s_not_found) { code = s_ok; bytes = 0; }
    xyf_core::hash::Sha256 h;
    xyf_core::hash::Digest d;
    char want[xc::mark_max];
    uint8_t stored[xc::mark_max + 1];
    if (code == s_ok && h.update(io.buffer, bytes) && h.finish(d)) n = xc::mark_text(want, sizeof(want), d.bytes, sv.target);
    sv.stage = sv_boot;
    if (!n) return 1;
    code = fc::read_file(xc::mark_path, 0, false, stored, sizeof(stored), got, usb_service::page_mount());
    if (code == s_ok && xc::mark_same(want, n, stored, got)) return 1;   // stored already: nothing written
    if (usb_service::mark_store(want, n) != s_ok) return 1;
    usb_service::JobView v;
    usb_service::job_view(v);
    sv.job = v.id; sv.stage = sv_marking;
    return 1;
}
// One step of the converge sequence (as pump; every storage request bounded by the file client, jobs in slices): the
// plan's operations in order (build, switch, record with the default), each a job that already skips what is done,
// on the progress screen, never on the Mods page. Nonzero: draw again (as pump).
uint32_t converge_follow() {
    m::Page& p = the_page;
    switch (sv.stage) {
    case sv_reading:                            // a flip not written yet: its list (the toggles) first, page hidden
        if (run.cancel) return fail("Cancelled. Nothing changed.");
        if (io.step == s_idle) open();
        if (io.step != s_done) return moved(false);
        sv.stage = sv_check;
        return 1;
    case sv_check: {                            // a reading left behind closes first (pump), a job ends
        if (run.cancel) return fail("Cancelled. Nothing changed.");
        if (pick.flipped && io.step != s_done) { sv.stage = sv_reading; return 1; }
        if (reading() || usb_service::page_busy()) return moved(false);
        if (usb_service::page_serving() != s_ok) return fail(prefix(), "no answer from storage.");
        const uint32_t write = pick_step(true);  // the page's flip written first: the plan is of what is saved
        if (write == 2) return fail(prefix(), "the mods could not be saved.");
        return write ? moved(false) : check();
    }
    case sv_listing:                            // the saved selection's build: the package list, page hidden
        if (run.cancel) return fail("Cancelled. Nothing changed.");
        if (io.step == s_idle) open();
        if (io.step != s_done) return moved(false);
        if (p.line == m::line_error) return fail(p.text);
        if (p.saved_missing || selection(m::flag_saved, sv.base) != sv.count)
            return fail("Failed: saved mods are missing from the mod packages: see the Mods page.");
        if (const char* why = m::save_refusal(p)) return fail(why);   // the toggles are the saved marks here
        usb_service::save_log_selection(sv.count, sv.expanded != 0, 0, 0);
        return start_build(m::flag_saved);
    case sv_build:
        return building();
    case sv_stamp:
        if (usb_service::page_busy()) return moved(false);   // the commit's window closes
        usb_service::save_log_lap(xyf::save_log::p_stamp);
        entry_stamp(sv.slot, sv.stamp);
        if (!sv.stamp[0]) {
            char d[12];
            m::decimal(d, io.why);
            return fail("Failed: the new Open-XY firmware could not be read back (", d, ").");
        }
        copy(sv.target, sv.slot, sizeof(sv.target));
        sv.stage = sv_switch;
        return 1;
    case sv_switch: {                           // the record made first: the switch's write window serves no reads
        if (usb_service::page_busy()) return moved(false);
        if (!sv.length) { compose(); if (sv.stage == sv_failed) return 1; }
        if (!(sv.ops & xc::op_switch)) { sv.stage = sv_record; return 1; }
        const uint32_t i = index_of(sv.target);
        if (i == ~0u) return fail(prefix(), "the firmware is not in the library.");
        if (usb_service::content_switch(sv.target) != s_ok) return fail(prefix(), "installing content could not start.");
        usb_service::JobView v;
        usb_service::job_view(v);
        sv.job = v.id; sv.stage = sv_switching;
        run.save_stage = 1; run.job_done = run.job_total = 0; run.cancel = 0; run.note[0] = 0;
        return 1;
    }
    case sv_switching: {
        usb_service::JobView v;
        usb_service::job_view(v);
        if (v.state == 1 && v.id == sv.job) {
            const bool phase = v.done != run.job_done && !run.job_total;
            run.job_done = v.done; run.job_total = v.total;
            return moved(phase);
        }
        if (v.id == sv.job && v.state == 2 && v.phase == 8) { sv.stage = sv_record; return 1; }
        return fail("Installing content failed: ", again(), " again repairs it.");
    }
    case sv_record:                             // the record, then the default (the config job)
        if (usb_service::page_busy()) return moved(false);
        if (usb_service::config_store(reinterpret_cast<const char*>(io.buffer), sv.length, sv.target) != s_ok)
            return fail(prefix(), "storage is busy.");
        {
            usb_service::JobView v;
            usb_service::job_view(v);
            sv.job = v.id;
        }
        sv.stage = sv_recording;
        run.save_stage = 2; run.job_done = run.job_total = 0;
        return 1;
    case sv_recording: {
        usb_service::JobView v;
        usb_service::job_view(v);
        if (v.state == 1 && v.id == sv.job) return moved(false);
        if (v.id == sv.job && v.state == 2 && v.phase == 8) {
            sv.stage = sv_mark;
            rec.state = xg::unread;              // as written: read again
            return 1;
        }
        return fail("Saving the settings failed: the previous setup is kept.");
    }
    case sv_mark:                               // the record's window closed first
        if (usb_service::page_busy()) return moved(false);
        return mark();
    case sv_marking: {                          // written or not, it boots: a fingerprint not stored converges again
        usb_service::JobView v;
        usb_service::job_view(v);
        if (v.state == 1 && v.id == sv.job) return moved(false);
        sv.stage = sv_boot;
        return 1;
    }
    default:
        return 0;
    }
}

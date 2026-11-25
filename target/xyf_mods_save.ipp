// Part of xyf_mods_io.cpp: the saved configuration (xyf_config.hpp), the Mods page's own write of it and the converge
// sequence (#197, xyf_converge.hpp), included in place (in its anonymous namespace).
namespace xg = xyf::config;
namespace xc = xyf::converge;
enum SaveStage : uint32_t {
    sv_idle = 0,
    sv_reading,
    sv_check,
    sv_listing,
    sv_build,
    sv_stamp,
    sv_switch,
    sv_switching,
    sv_record,
    sv_recording,
    sv_mark,
    sv_marking,
    sv_boot,
    sv_failed,
    sv_adopting,
    sv_content_set
};
// The sequence: why (converge_begin's: which inputs it set, for its texts only), the inputs (count, expanded, safe,
// unverified, base, built), the plan (ops, target), the slot and stamp the record names (a build's new ones), the
// record made for the record step (length bytes in io.buffer).
struct Save {
    uint32_t stage, job, serial, why, ops, unverified, count, expanded, safe, length, adopted, looked, failed;
    char target[32], base[32], built[100], slot[8], stamp[65], reason[80];
};
xg::Record rec XYF_DATA = {};
Save sv XYF_DATA = {};
m::Progress run XYF_DATA = {};                   // the progress screen
char label[48] XYF_DATA = {};
char safe_text[32] XYF_DATA = {};                // the safe-mode row
uint32_t rec_set XYF_DATA = 0;                   // the record's mods= names: their order-free CRC-32 sum (built=)
// The page's own write (#197): a flip (a toggle, Mod memory, unknown bases) is saved to config.json at a quiet moment,
// quiet_ms after the last flip (quick flips: one write), by the config job (the default unchanged). flipped: 1, a flip
// not written yet; 2, its write failed (the next flip or a converge writes it); since: the last flip; stage 1: the
// job (job) runs; 2: written, the record not read back yet (rec keeps the old one meanwhile: no pending=0 between).
// safe (#26, config.set): 0 safe mode as saved; 1 off, 2 on, until that write is done.
struct Pick {
    uint32_t flipped, since, stage, job, safe;
};
Pick pick XYF_DATA = {};
static const char firmware[] = "/yaffs2/xy-forever/firmware/";
uint32_t index_of(const char* id) {
    const xyf::catalog::Catalog* c = xyf_boot::catalog();
    for (uint32_t i = 0; c && id[0] && i != c->count; ++i)
        if (xg::same(c->items[i].id, id)) return i;
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
#include "xyf_mods_config.ipp"

// The selected firmware record's stamp into out (65 bytes); "" when it cannot be read.
void entry_stamp(const char* id, char* out) {
    uint32_t bytes = 0;
    const char* parts[4] = {firmware, id, "/", record::native_name};
    out[0] = 0;
    if ((io.why = usb_service::page_serving()) != s_ok || (io.why = fc::mount_ready(usb_service::page_mount())) != s_ok)
        return;
    if (!path_of(parts, 4)) return;
    if (MetadataWork* work = load_native_metadata(bytes)) {
        const auto status = record::parse(reinterpret_cast<const char*>(work->raw), bytes, work->parse);
        const auto& manifest = work->parse.manifest;
        if (status != json::ok || manifest.status != xyf::manifest::ok ||
            manifest.type != xyf::manifest::type_firmware || !xyf::manifest::has(manifest, xyf::manifest::key_sha256)) {
            io.why = xyf::usbp::s_refused;
            return;
        }
        const char* digits = "0123456789abcdef";
        for (uint32_t i = 0; i != sizeof(manifest.sha256); ++i) {
            out[2 * i] = digits[manifest.sha256[i] >> 4];
            out[2 * i + 1] = digits[manifest.sha256[i] & 15u];
        }
        out[64] = 0;
    } else if (io.why == xyf::usbp::s_not_found) {
        parts[3] = record::legacy_name;
        if (path_of(parts, 4) && read_text(io.path, bytes))
            xg::stamp_of(reinterpret_cast<const char*>(io.buffer), bytes, out);
    }
}
// The sequence ends without booting: the reason for the home menu's status line (converge_reason).
uint32_t fail(const char* a, const char* b = "", const char* c = "") {
    xg::Out o = {sv.reason, sv.reason + sizeof(sv.reason) - 1, true};
    xg::put(o, a);
    xg::put(o, b);
    xg::put(o, c);
    *o.at = 0;
    sv.stage = sv_failed;
    sv.failed = 1; // menu.converge reports it until the next sequence (#204)
    usb_service::save_log_drop();               // a failed sequence writes no line
    rec.state = xg::unread;                     // read again: the record or the default may have changed
    return 1;
}
const char* prefix() { return "Failed: "; }
const char* again() {
    return sv.why == why_safe ? "Boot without mods" : sv.why == why_restore ? "Restore Open-XY" : "Continue";
}
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
        set += xyf::crc::update(
            k.kind == m::kind_extension ? xyf::crc::update(0, reinterpret_cast<const uint8_t*>("ext-"), 4) : 0u,
            reinterpret_cast<const uint8_t*>(id), n);
        ++count;
    }
    xg::built_text(sv.built, sizeof(sv.built), base, p.generation, set, count, sv.expanded != 0);
    return count;
}
// The saved inputs (the record just read): built= from its mods= line (rec_set).
void saved_inputs(const char* base) {
    sv.count = rec.count;
    sv.expanded = rec.expanded;
    sv.unverified = rec.unverified;
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
    if (code != s_ok) {
        m::start_refused(p, code, missing);
        return fail(p.text);
    }
    sv.job = job;
    sv.stage = sv_build;
    run.save |= m::save_build;
    run.job_phase = run.job_done = run.job_total = run.job_commit = 0;
    return 1;
}
uint32_t adopt(const char* base);
// The inputs (the saved record, with safe= as asked), the actual state, the plan.
uint32_t check() {
    usb_service::save_log_lap(xyf::save_log::p_check);
    uint32_t bytes = 0;
    const uint32_t code = read_record(false, bytes);
    if (code == xyf::usbp::s_busy) return moved(false);
    if (io.config_error) return fail(io.config_error);
    if (code != s_ok) {
        char d[12];
        m::decimal(d, code);
        return fail("Failed: the settings could not be read (", d, ").");
    }
    const bool have = rec.state == xg::present;
    if (sv.why == why_safe && !(have && rec.count && rec.slot[0])) return fail("No Open-XY setup with mods is saved.");
    if (sv.why == why_restore && !(have && rec.count)) return fail("No Open-XY setup to restore.");
    const char* base = config_base();
    const xyf::catalog::Catalog* c = xyf_boot::catalog();
    if (!base || (!have && !(c && c->default_index < c->count))) return adopt(base);   // #178: nothing to boot yet
    saved_inputs(base);
    copy(sv.base, base, sizeof(sv.base));
    // The actual state: the slot's stamp read now (the record's slot), the default, the installed content.
    copy(sv.slot, have ? rec.slot : "", sizeof(sv.slot));
    copy(sv.stamp, "", sizeof(sv.stamp));
    if (have && rec.slot[0] && rec.stamp[0] && index_of(rec.slot) != ~0u) {
        entry_stamp(rec.slot, sv.stamp);
        if (io.why == xyf::usbp::s_busy) return moved(false);
        if (io.why == xyf::usbp::s_link) return fail(prefix(), "no answer from storage.");
    }
    const xc::Inputs in = {sv.base, sv.built, sv.count, sv.expanded, sv.safe};
    const xc::Actual a = {&rec,
                          sv.stamp,
                          c && c->default_index < c->count ? c->items[c->default_index].id : "",
                          installed(index_of(sv.base)),
                          installed(have ? index_of(rec.slot) : ~0u),
                          installed(c ? c->default_index : ~0u)};
    const xc::Plan plan = xc::plan(in, a);
    sv.ops = plan.ops;
    copy(sv.target, plan.target ? plan.target : "", sizeof(sv.target));
    copy(sv.stamp, have ? rec.stamp : "",
         sizeof(sv.stamp));   // the record's slot and stamp, unless a build replaces them
    if (!sv.ops) {
        sv.stage = sv_mark;
        return 1;
    }    // nothing differs: it boots (the fingerprint written if it differs)
    if (!(sv.ops & xc::op_build)) {
        sv.stage = sv_switch;
        return 1;
    }
    sv.stage = sv_listing;                      // the saved selection's build needs the package list
    return 1;
}
// #178: no stock entry, or no record and no default (the first start after Track 5): the stock entry adopted from the
// stock updater's own os.ldr (usb_service adopt_start: hashed, copied, read back, renamed in), or only the stock
// entry there made the default; then the check again. Once per sequence (sv.adopted: 2 copied, 1 only the default).
// No catalog (A's library unread): nothing adopted; the adoption itself refuses a library A could not list (e_list).
static const char adopt_id[] = "opxy-121", reinstall[] = " Reinstall official OS 1.2.1, then start again.";
static const char unread_library[] = "Failed: the library could not be read.";
uint32_t adopt(const char* base) {
    if (sv.adopted) return fail(prefix(), "no stock OP-XY firmware in the library.");
    if (!xyf_boot::catalog()) return fail(unread_library);
    if (usb_service::adopt_start(base ? base : adopt_id, base ? usb_service::adopt_default : usb_service::adopt_copy) !=
        s_ok)
        return fail(prefix(), "OS 1.2.1 could not be added.");
    usb_service::JobView v;
    usb_service::job_view(v);
    sv.job = v.id;
    sv.adopted = base ? 1 : 2;
    sv.stage = sv_adopting;
    run.title = "Adding OS 1.2.1";
    run.job_done = run.job_total = 0;
    return 1;
}
uint32_t adopting() {
    usb_service::JobView v;
    usb_service::job_view(v);
    if (v.state == 1 && v.id == sv.job) {
        const bool phase = v.phase != run.job_phase;
        run.job_phase = v.phase;
        run.job_done = v.done;
        run.job_total = v.total;
        return moved(phase);
    }
    if (v.id == sv.job && v.state == 2 && v.phase == 8) {           // the catalog fetched again: the check
        if (usb_service::page_busy() || !xyf_boot::catalog()) return moved(false);
        sv.stage = sv_check;
        return 1;
    }
    if (v.error == 4) return fail("No official OS 1.2.1 found.", reinstall);
    if (v.error == 10) return fail("OS file is not official 1.2.1.", reinstall);
    if (v.error == 17 && v.status == xyf::usbp::s_state)
        return fail("Failed: library entry ", adopt_id, " is incomplete: remove it.");
    if (v.error == 8) return fail(unread_library);                  // firmware/ID there, but not in A's catalog
    char d[12];
    m::decimal(d, v.error);
    return fail("Failed: OS 1.2.1 could not be added to the library (", d, ").");
}
// #183: the build's base needs its content set (firmware/<base>/content.sha256, firmware.version): an OS 1.2.1 entry
// the web install adopted has none, so the adoption job's content mode makes it from the live official content first
// (a no-op when it is there); once it reads content, the screen is its own (title, the bytes read as the whole bar);
// then the build, its bar from the start.
const char* build_title XYF_DATA = 0;
uint32_t content_set() {
    if (usb_service::adopt_start(sv.base, usb_service::adopt_content) != s_ok)
        return fail(prefix(), "the base's content set could not be checked.");
    usb_service::JobView v;
    usb_service::job_view(v);
    sv.job = v.id;
    sv.stage = sv_content_set;
    build_title = run.title;
    run.job_phase = run.job_done = run.job_total = 0;
    return 1;
}
void reading_screen(bool on) { // a new job number: the screen drawn whole
    if (!run.reading == !on) return;
    run.reading = on ? 1u : 0u;
    run.title = on ? "Reading OS 1.2.1 content" : build_title;
    run.job = (on ? 0x20000u : 0x10000u) + sv.serial;
    run.bar = run.bar_step = run.bar_since = 0;
}
uint32_t content_setting() {
    usb_service::JobView v;
    usb_service::job_view(v);
    if (v.state == 1 && v.id == sv.job) {
        const bool phase = v.phase != run.job_phase || (v.total && !run.reading);
        if (v.total) reading_screen(true);
        run.job_phase = v.phase;
        run.job_done = v.done;
        run.job_total = v.total;
        return moved(phase);
    }
    if (v.id == sv.job && v.state == 2 && v.phase == 8) { // the window closed: the build
        if (usb_service::page_busy()) return moved(false);
        reading_screen(false);
        return start_build(m::flag_saved);
    }
    reading_screen(false);
    if (v.error == 9) return fail(prefix(), "the base is not official OS 1.2.1 (mods need its content).");
    if (v.error == 15) return fail(prefix(), "mods' content is installed; the base has no content set.");
    if (v.error == 10 || v.error == 7) return fail(prefix(), "the content is not official OS 1.2.1's.");
    char d[12];
    m::decimal(d, v.error);
    return fail("Failed: the base's content set could not be written (", d, ").");
}
uint32_t building() {                           // the mod job into the spare slot (STOP: job.cancel's stop)
    m::Page& p = the_page;
    usb_service::JobView v;
    usb_service::job_view(v);
    bool refused = false;
    if (run.cancel == 1 && v.state == 1 && v.id == sv.job) {
        if (usb_service::job_stop() != s_ok) {
            m::refuse_cancel(run);
            refused = true;
        }   // in the commit
        else
            run.cancel = 2;                    // asked once
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
    run.job_phase = v.phase;
    run.job_done = v.done;
    run.job_total = v.total;
    run.job_commit = v.commit;
    return moved(refused || phase);
}
// The record the plan writes, into io.buffer (sv.length bytes; 0: fail said why): the inputs, the slot and stamp (the
// build's, else the record's), and the mods= line: the saved marks after a build of the saved selection, else as
// saved (the selected record read again).
uint32_t compose() {
    const m::Page& p = the_page;
    const bool listed = (sv.ops & xc::op_build) != 0;
    xg::Record r;                               // field by field: a struct copy is a memcpy call on Blackfin
    xg::clear(r, xg::present);
    copy(r.base, sv.base, sizeof(r.base));
    copy(r.profile, listed ? p.profile : rec.profile, sizeof(r.profile));
    copy(r.generation, listed ? p.generation : rec.generation, sizeof(r.generation));
    copy(r.built, listed ? sv.built : rec.built, sizeof(r.built));
    copy(r.slot, sv.slot, sizeof(r.slot));
    copy(r.stamp, sv.stamp, sizeof(r.stamp));
    r.unverified = sv.unverified;
    r.expanded = sv.expanded;
    r.count = sv.count;
    r.safe = sv.safe && sv.count && r.slot[0] ? 1u : 0u;    // as xg::parse reads it back
    char* const text = reinterpret_cast<char*>(io.buffer);
    uint32_t n = 0;
    if (listed) {
        if (!(sv.length = record_text(r, m::flag_saved))) return fail(prefix(), "the settings do not fit.");
        return 1;
    }
    uint32_t bytes = 0;
    const uint32_t code = read_record(false, bytes);
    if (code == xyf::usbp::s_busy) return moved(false);
    if (code != s_ok) return fail(io.config_error ? io.config_error : "Failed: the settings could not be read.");
    const char* saved = rec.state == xg::present ? xg::value(text, bytes, "mods", n) : 0;
    if (saved) text[saved - text + n] = 0;
    if (!saved || !(sv.length = encode_config(r, saved, false))) return fail(prefix(), "the settings do not fit.");
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
        if (done) pick.safe = 0;
        if (!done) {
            if (!pick.flipped) pick.flipped = 2;
            return 2;
        }
    }
    if (!pick.flipped) return 0;
    if (!now && (pick.flipped == 2 || units() - pick.since < quiet_units)) return 1;
    const m::Page& p = the_page;
    if (io.step != s_done || usb_service::page_busy() || usb_service::page_serving() != s_ok) return 1; // the toggles
    if (!p.limit || io.incomplete || io.config_error) {
        pick.flipped = 2;
        return 2;
    } // no complete list: nothing to save from
    uint32_t bytes = 0;
    if (rec.state != xg::present && rec.state != xg::none) {
        const uint32_t code = read_record(false, bytes);
        if (code == xyf::usbp::s_busy) return 1;
        if (code != s_ok) {
            pick.flipped = 2;
            return 2;
        }
    }
    const char* base = config_base();
    if (!base) {
        pick.flipped = 2;
        return 2;
    }
    // The selection and the inputs beside it; what is built (built=, slot=, stamp=) and safe mode (unless pick.safe) stay.
    const bool have = rec.state == xg::present;
    xg::Record r;
    xg::clear(r, xg::present);
    copy(r.base, base, sizeof(r.base));
    copy(r.profile, p.profile, sizeof(r.profile));
    copy(r.generation, p.generation, sizeof(r.generation));
    copy(r.built, have ? rec.built : "", sizeof(r.built));
    copy(r.slot, have ? rec.slot : "", sizeof(r.slot));
    copy(r.stamp, have ? rec.stamp : "", sizeof(r.stamp));
    r.unverified = p.unknown_ok || (have && rec.unverified && xg::same(rec.base, base)) ? 1u : 0u;
    r.expanded = p.expanded ? 1u : 0u;
    r.count = m::chosen(p, false) + m::chosen(p, true);
    r.safe = (pick.safe ? pick.safe == 2 : have && rec.safe) && r.count && r.slot[0] ? 1u : 0u;
    const uint32_t n = record_text(r, m::flag_on);
    const uint32_t code = n ? usb_service::config_store(reinterpret_cast<const char*>(io.buffer), n, "") : 1u;
    if (code == xyf::usbp::s_busy) return 1;
    if (code != s_ok) {
        pick.flipped = 2;
        return 2;
    }
    usb_service::JobView v;
    usb_service::job_view(v);
    pick.job = v.id;
    pick.stage = 1;
    pick.flipped = 0;
    return 1;
}
// The fingerprint (xyf_converge.hpp): the exact selected record bytes, hashed with the default it converged to
// (sv.target). The config job writes it (quiet: no
// catalog fetch) only when the stored text differs. Unreadable: nothing stored (the next power-on converges again).
uint32_t mark() {
    uint32_t bytes = 0, got = 0, n = 0;
    auto& work = *static_cast<MetadataWork*>(usb_service::mods_metadata_work());
    bool native = false;
    uint32_t code = usb_service::page_serving();
    if (code == s_ok) code = config_io::read(io.config_scan, work.raw, bytes, native, usb_service::page_mount());
    if (code == xyf::usbp::s_busy) return moved(false);
    if (code == xyf::usbp::s_not_found) {
        code = s_ok;
        bytes = 0;
    }
    xyf_core::hash::Sha256 h;
    xyf_core::hash::Digest d;
    char want[xc::mark_max];
    uint8_t stored[xc::mark_max + 1];
    if (code == s_ok && h.update(work.raw, bytes) && h.finish(d))
        n = xc::mark_text(want, sizeof(want), d.bytes, sv.target);
    sv.stage = sv_boot;
    if (!n) return 1;
    code = fc::read_file(xc::mark_path, 0, false, stored, sizeof(stored), got, usb_service::page_mount());
    if (code == s_ok && xc::mark_same(want, n, stored, got)) return 1; // stored already: nothing written
    if (usb_service::mark_store(want, n) != s_ok) return 1;
    usb_service::JobView v;
    usb_service::job_view(v);
    sv.job = v.id;
    sv.stage = sv_marking;
    return 1;
}
// One step of the converge sequence (as pump; every storage request bounded by the file client, jobs in slices): the
// plan's operations in order (build, switch, record with the default), each a job that already skips what is done,
// on the progress screen, never on the Mods page. Nonzero: draw again (as pump).
uint32_t converge_follow() {
    m::Page& p = the_page;
    switch (sv.stage) {
    case sv_reading: // a flip not written yet: its list (the toggles) first, page hidden
        if (run.cancel) return fail("Cancelled. Nothing changed.");
        if (io.step == s_idle) open();
        if (io.step != s_done) return moved(false);
        sv.stage = sv_check;
        return 1;
    case sv_check: { // a reading left behind closes first (pump), a job ends
        if (run.cancel) return fail("Cancelled. Nothing changed.");
        if (pick.flipped && io.step != s_done) {
            sv.stage = sv_reading;
            return 1;
        }
        if (reading() || usb_service::page_busy()) return moved(false);
        if (usb_service::page_serving() != s_ok) return fail(prefix(), "no answer from storage.");
        const uint32_t write = pick_step(true); // the page's flip written first: the plan is of what is saved
        if (write == 2) return fail(io.config_error ? io.config_error : "Failed: the mods could not be saved.");
        return write ? moved(false) : check();
    }
    case sv_listing: // the saved selection's build: the package list, page hidden
        if (run.cancel) return fail("Cancelled. Nothing changed.");
        if (io.step == s_idle) open();
        if (io.step != s_done) return moved(false);
        if (p.line == m::line_error) return fail(p.text);
        if (p.saved_missing || selection(m::flag_saved, sv.base) != sv.count)
            return fail("Failed: saved mods are missing from the mod packages: see the Mods page.");
        if (const char* why = m::save_refusal(p)) return fail(why); // the toggles are the saved marks here
        usb_service::save_log_selection(sv.count, sv.expanded != 0, 0, 0);
        return content_set();
    case sv_build: return building();
    case sv_adopting: return adopting();
    case sv_content_set: return content_setting();
    case sv_stamp:
        if (usb_service::page_busy()) return moved(false); // the commit's window closes
        usb_service::save_log_lap(xyf::save_log::p_stamp);
        entry_stamp(sv.slot, sv.stamp);
        if (io.why == xyf::usbp::s_busy) return moved(false);
        if (!sv.stamp[0]) {
            char d[12];
            m::decimal(d, io.why);
            return fail("Failed: the new Open-XY firmware could not be read back (", d, ").");
        }
        copy(sv.target, sv.slot, sizeof(sv.target));
        sv.stage = sv_switch;
        return 1;
    case sv_switch: { // the record made first: the switch's write window serves no reads
        if (usb_service::page_busy()) return moved(false);
        if (!sv.length && (sv.ops & xc::op_record)) {
            const uint32_t draw = compose();
            if (!sv.length) return draw;
        }
        if (!(sv.ops & xc::op_switch)) {
            sv.stage = sv_record;
            return 1;
        }
        const uint32_t i = index_of(sv.target);
        if (i == ~0u) return fail(prefix(), "the firmware is not in the library.");
        // #204: no record follows (a boot once, or no setup saved): the switch ends its own journal.
        const uint32_t started = sv.ops & xc::op_record ? usb_service::content_switch(sv.target) :
                                                          usb_service::content_switch_once(sv.target);
        if (started != s_ok) return fail(prefix(), "installing content could not start.");
        usb_service::JobView v;
        usb_service::job_view(v);
        sv.job = v.id;
        sv.stage = sv_switching;
        run.save_stage = 1;
        run.job_done = run.job_total = 0;
        run.cancel = 0;
        run.note[0] = 0;
        return 1;
    }
    case sv_switching: {
        usb_service::JobView v;
        usb_service::job_view(v);
        if (v.state == 1 && v.id == sv.job) {
            const bool phase = v.done != run.job_done && !run.job_total;
            run.job_done = v.done;
            run.job_total = v.total;
            return moved(phase);
        }
        if (v.id == sv.job && v.state == 2 && v.phase == 8) {
            sv.stage = sv_record;
            return 1;
        }
        return fail("Installing content failed: ", again(), " again repairs it.");
    }
    case sv_record: // the record, then the default (the config job)
        if (usb_service::page_busy()) return moved(false);
        if (!(sv.ops & xc::op_record)) { // #204: no record: a boot once boots (no fingerprint), else the fingerprint
            sv.stage = sv.why == why_once ? sv_boot : sv_mark;
            return 1;
        }
        if (usb_service::config_store(reinterpret_cast<const char*>(io.buffer), sv.length, sv.target) != s_ok)
            return fail(prefix(), "storage is busy.");
        {
            usb_service::JobView v;
            usb_service::job_view(v);
            sv.job = v.id;
        }
        sv.stage = sv_recording;
        run.save_stage = 2;
        run.job_done = run.job_total = 0;
        return 1;
    case sv_recording: {
        usb_service::JobView v;
        usb_service::job_view(v);
        if (v.state == 1 && v.id == sv.job) return moved(false);
        if (v.id == sv.job && v.state == 2 && v.phase == 8) {
            sv.stage = sv_mark;
            rec.state = xg::unread; // as written: read again
            return 1;
        }
        return fail("Saving the settings failed: the previous setup is kept.");
    }
    case sv_mark: // the record's window closed first
        if (usb_service::page_busy()) return moved(false);
        return mark();
    case sv_marking: { // written or not, it boots: a fingerprint not stored converges again
        usb_service::JobView v;
        usb_service::job_view(v);
        if (v.state == 1 && v.id == sv.job) return moved(false);
        sv.stage = sv_boot;
        return 1;
    }
    default: return 0;
    }
}

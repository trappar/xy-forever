#include "xyf_mods_io.hpp"
#if defined(__bfin__) && defined(XYF_MODS_PAGE)
#include "xyf_clock.hpp"
#include "xyf_boot.hpp"
#include "xyf_store.hpp"
#include "file_client.hpp"
#include "file_engine.hpp"
#include "usb_service.hpp"
#include "usb_proto_ids.hpp"
#include "../native/hash.hpp"
#include "xyf_config.hpp"
#include "xyf_crc32.hpp"
#include "xyf_save_log.hpp"
#include "xyf_mods_store.hpp"
#include "xyf_package_record.hpp"
// See xyf_mods_io.hpp. The reading is a small state machine: one file-client call per step (a folder
// open, one entry, its close, one stat, or one whole small file), steps while the pass is under
// slice_ms. An open folder is held across passes and closed when the walk ends, fails or the page is
// left; a request past its bound marks B's mount stale (op 9 before the next open closes what A held).
namespace xy_forever { namespace mods_io {
namespace {
namespace fc = file_client;
namespace m = xyf::mods;
using xyf::usbp::s_ok;
#if defined(__bfin__)
#define XYF_DATA __attribute__((section(".data")))
#endif
enum Step : uint32_t {
    s_idle = 0, s_serving, s_mount, s_generations, s_store, s_base_list, s_base_native, s_base_info, s_services,
    s_packages, s_package_list, s_package_native, s_package_info, s_saved, s_done
};
enum Walk : uint32_t { walk_entry, walk_more, walk_done, walk_missing, walk_failed };
static const uint32_t max_candidates = 8;
struct Candidate { char name[32]; };
struct Io {
    uint32_t step, folder, why;                 // why: the status of a failed step
    uint32_t candidate_count, candidate_at, package_at;
    uint32_t drawn, longest, dirty;               // drawn: units() of the last redraw
    uint32_t job_units, drawn_bar, drawn_done;    // the job's start; the bar and done of the last redraw
    uint32_t waiting;                             // the reading waits for another job (page_busy)
    uint32_t newer, broken;                       // any unreadable native metadata while opening the list
    fc::NameScan metadata_scan;
    uint32_t config_skip, config_choice;
    const char* config_error;
    Candidate candidates[max_candidates];
    char path[240];
    uint8_t entry[fc::entry_bytes + 4];         // the last folder entry (walk_entry: its name at +11)
    uint8_t buffer[read_bytes];
};
m::Page the_page XYF_DATA = {};
Io io XYF_DATA = {};

// Inlined: a function called from several places of one function may be called through a register
// (-Os), which the builder's call audit rejects.
inline __attribute__((always_inline)) bool reading() { return io.step != s_idle && io.step != s_done; }
// The cycle counter in 1024-cycle units (390.625 per ms; ms only for the longest-pass record).
inline __attribute__((always_inline)) uint32_t units() {
    uint32_t low, high;
    __asm__ volatile("%0 = CYCLES; %1 = CYCLES2;" : "=d"(low), "=d"(high));
    return (low >> 10) | (high << 22);
}
const uint32_t slice_units = slice_ms * 390u + slice_ms * 5u / 8u, redraw_units = redraw_ms * 390u + redraw_ms * 5u / 8u;
const uint32_t progress_units = progress_ms * 390u + progress_ms * 5u / 8u;
// io.path = the parts joined; false when it does not fit (the path is then cut: never used).
bool path_of(const char* const* parts, uint32_t count) {
    uint32_t n = 0;
    bool fits = true;
    for (uint32_t k = 0; k != count; ++k)
        for (const char* s = parts[k]; *s; ++s) { if (n + 1 >= sizeof(io.path)) { fits = false; break; } io.path[n++] = *s; }
    io.path[n] = 0;
    return fits;
}
void copy(char* out, const char* s, uint32_t room) { uint32_t n = 0; while (s[n] && n + 1 < room) { out[n] = s[n]; ++n; } out[n] = 0; }
uint32_t failed(const fc::Reply& r) {
    if (r.outcome == fc::link) usb_service::page_mount() = fc::mounted_stale;
    return fc::status(r.outcome);
}
// One request of a walk over io.path (no callbacks: the builder's call audit takes direct calls only).
Walk walk() {
    if (!io.folder) {
        const fc::Reply r = fc::opendir(io.path);                         // op 0x2e
        if (r.outcome) { io.why = failed(r); return walk_failed; }
        if (!r.value || r.value == -1) { io.why = xyf::usbp::s_not_found; return walk_missing; }
        io.folder = uint32_t(r.value);
        return walk_more;
    }
    uint8_t* const entry = io.entry;
    const fc::Reply r = fc::readdir(io.folder, entry);                    // op 0x2f
    if (r.outcome) { io.folder = 0; io.why = failed(r); return walk_failed; }   // op 9 closes it at A
    const uint32_t got = fc::readdir_result(r.value);
    if (got == fc::folder_entry) {
        entry[11 + fc::max_name] = 0;
        return walk_entry;
    }
    const fc::Reply c = fc::closedir(io.folder);                          // op 0x30
    io.folder = 0;
    if (c.outcome) { io.why = failed(c); return walk_failed; }
    if (got == fc::folder_end) return walk_done;
    io.why = xyf::usbp::s_storage;
    return walk_failed;
}
void each_generation(const char* name) {
    if (!m::generation_token(name)) return;
    uint32_t at = io.candidate_count;
    if (at == max_candidates) {                  // full: replace the oldest when this one is newer
        if (m::compare_generation(name, io.candidates[at - 1].name) <= 0) return;
        --at;
    } else ++io.candidate_count;
    while (at && m::compare_generation(name, io.candidates[at - 1].name) > 0) { io.candidates[at] = io.candidates[at - 1]; --at; }
    copy(io.candidates[at].name, name, 32);
}
const char* entry_name() { return reinterpret_cast<const char*>(io.entry + 11); }
// The candidate generation's folder (mods/<generation>/, #124), or a file of one of its packages, into io.path.
bool generation_path(const char* prefix, const char* package, const char* file) {
    const Candidate& c = io.candidates[io.candidate_at];
    const char* parts[8] = {root, "/", c.name, package[0] ? "/" : "", prefix, package, file[0] ? "/" : "", file};
    return path_of(parts, 8);
}
bool package_path(uint32_t index, const char* file) {
    const m::Package& p = the_page.packages[index];
    return generation_path(p.kind == m::kind_extension ? "ext-" : "", m::text_at(the_page, p.id), file);
}
bool present(const char* path) {              // one stat; io.why set when A did not answer
    uint8_t record[fc::record_bytes];
    const fc::Reply r = fc::stat(path, record);                           // op 0x22
    if (r.outcome) { io.why = failed(r); return false; }
    io.why = s_ok;
    return r.value == 0;
}
bool read_text(const char* path, uint32_t& bytes) {   // the whole small file into io.buffer
    bytes = 0;
    io.why = fc::read_file(path, 0, false, io.buffer, read_bytes, bytes, usb_service::page_mount());
    return io.why == s_ok;
}
#include "xyf_mods_metadata.ipp"
void finish(const char* error) {              // the list is final
    if (io.folder) { fc::closedir(io.folder); io.folder = 0; }
    io.step = s_done;
    the_page.view = m::View::list;
    if (error) m::say(the_page, m::line_error, error);
    else if (io.newer || io.broken)
        m::say(the_page, m::line_info, io.newer ? "Some packages need a newer XY Forever." : "Some packages have invalid manifest.json.");
    else the_page.line = m::line_none;
    io.dirty = 1;
}
void lost() {
    finish(io.why == xyf::usbp::s_storage ? "Could not read the mod packages (storage error)." :
           "Could not read the mod packages: no answer from storage.");
}
// The next base candidate, or the end.
void next_candidate() {
    ++io.candidate_at;
    if (io.candidate_at >= io.candidate_count) {
        finish(io.newer ? "The mod packages need a newer XY Forever." : io.broken ? "The mod packages have invalid manifest.json." :
               io.candidate_count ? "No complete mod package set found." : 0);
        return;
    }
    io.step = s_store;
}
uint32_t read_record(bool toggles);
// One step of the reading.
#include "xyf_mods_read.ipp"

// Build: the default library entry as base when it is a usable official image, else the first one.
// An official image whose only lack is the official content (a mod set's content is installed) is
// a base too: mods.apply then reads the saved official catalog.
const char* base_entry() {
    const xyf::catalog::Catalog* c = xyf_boot::catalog();
    if (!c) return 0;
    const auto fits = [c](uint32_t i) {
        return i < c->count && (c->items[i].flags & (xyf::catalog::flag_usable | xyf::catalog::flag_content_missing)) &&
               c->items[i].kind == xyf::store::kind_official;
    };
    if (fits(c->default_index)) return c->items[c->default_index].id;
    for (uint32_t i = 0; i != c->count; ++i) if (fits(i)) return c->items[i].id;
    return 0;
}
// ---- The saved configuration (xyf_config.hpp) and the save sequence ------------------------------------------
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
#include "xyf_mods_config.ipp"

// The selected firmware record's stamp into out (65 bytes); "" when it cannot be read.
void entry_stamp(const char* id, char* out) {
    uint32_t bytes = 0;
    const char* parts[4] = {firmware, id, "/", record::native_name};
    out[0] = 0;
    if ((io.why = usb_service::page_serving()) != s_ok || (io.why = fc::mount_ready(usb_service::page_mount())) != s_ok) return;
    if (!path_of(parts, 4)) return;
    if (MetadataWork* work = load_native_metadata(bytes)) {
        const auto status = record::parse(reinterpret_cast<const char*>(work->raw), bytes, work->parse);
        const auto& manifest = work->parse.manifest;
        if (status != json::ok || manifest.status != xyf::manifest::ok || manifest.type != xyf::manifest::type_firmware ||
            !xyf::manifest::has(manifest, xyf::manifest::key_sha256)) { io.why = xyf::usbp::s_refused; return; }
        const char* digits = "0123456789abcdef";
        for (uint32_t i = 0; i != sizeof(manifest.sha256); ++i) {
            out[2 * i] = digits[manifest.sha256[i] >> 4];
            out[2 * i + 1] = digits[manifest.sha256[i] & 15u];
        }
        out[64] = 0;
    } else if (io.why == xyf::usbp::s_not_found) {
        parts[3] = record::legacy_name;
        if (path_of(parts, 4) && read_text(io.path, bytes)) xg::stamp_of(reinterpret_cast<const char*>(io.buffer), bytes, out);
    }
}
uint32_t fail(const char* reason) {
    m::Page& p = the_page;
    fc::close_name_scan(io.metadata_scan, usb_service::page_mount());
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
    if (usb_service::page_busy()) { io.metadata_scan.folder = 0; return moved(false); }
    if (io.config_error) return fail(io.config_error);
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
        if (io.why == xyf::usbp::s_busy) return moved(false);
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
        if (usb_service::page_busy()) { io.metadata_scan.folder = 0; return moved(false); }
        usb_service::save_log_lap(xyf::save_log::p_stamp);
        entry_stamp(sv.slot, sv.stamp);
        if (io.why == xyf::usbp::s_busy) return moved(false);
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
        r.safe = rec.safe;
        const bool built = sv.count != 0;       // all off: the Open-XY slot stays as the record had it (spare)
        copy(r.built, built ? sv.built : rec.built, sizeof(r.built));
        copy(r.slot, built ? sv.slot : rec.slot, sizeof(r.slot));
        copy(r.stamp, built ? sv.stamp : rec.stamp, sizeof(r.stamp));
        char* text;
        const uint32_t n = write_config(r, p, built, text);
        if (!n) return fail("Save failed: the configuration exceeds its format limits.");
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
}

m::Page& page() { return the_page; }

void open() {
    m::Page& p = the_page;
    if (rec.state == xg::failed) rec.state = xg::unread;   // try the record again
    if (p.view == m::View::building || (p.view == m::View::list && p.shown)) return;   // kept for this boot
    if (io.folder) return;                       // (never: pump closes it when the page is left)
    restart_config_lookup();
    m::reset(p);
    io.step = s_serving; io.candidate_count = io.package_at = io.newer = io.broken = 0; io.dirty = 1; io.waiting = 0;
    m::say(p, m::line_info, "Reading mod packages...");
}

m::Effect key(uint32_t k, bool pressed) { return m::key(the_page, k, pressed); }

void prepare() { if (io.step == s_idle && sv.stage == sv_idle) open(); }

const char* memory_flip() { return m::flip_memory(the_page, io.step == s_done); }

uint32_t pump(bool shown) {
    const uint32_t begin = units();
    uint32_t redraw = save_follow();
    // The saved configuration, once the menu runs (one bounded read; again after a failed save).
    if (rec.state == xg::unread && sv.stage == sv_idle && !reading()) {
        if (usb_service::page_busy()) restart_config_lookup();
        else if (usb_service::page_serving() == s_ok) read_record(false);
    }
    // While another job owns the slot or its window closes, the reading sends nothing (a request
    // then met a stale folder handle or waited out A's bound). A's handles end with that window: the folder
    // is forgotten, not closed, and the reading starts again from the top once the job is over.
    const bool busy = reading() && usb_service::page_busy();
    if (busy) { io.folder = io.metadata_scan.folder = 0; restart_config_lookup(); }
    if (!shown && reading()) {                   // left mid-way: close the folder, read again next time
        if (io.folder) { fc::closedir(io.folder); io.folder = 0; }
        fc::close_name_scan(io.metadata_scan, usb_service::page_mount());
        restart_config_lookup();
        io.step = s_idle;
    }
    if (shown && busy && !io.waiting) {
        m::reset(the_page);
        io.step = s_serving; io.candidate_count = io.package_at = io.newer = io.broken = 0;
        io.waiting = 1; io.dirty = 1;
        m::say(the_page, m::line_info, "Busy: a build or content change is running.");
    } else if (shown && reading() && io.waiting && !busy) {
        io.waiting = 0; io.dirty = 1;
        m::say(the_page, m::line_info, "Reading mod packages...");
    }
    uint32_t end = begin;
    if (shown && busy) {
        end = units();
        if (io.dirty) { io.dirty = 0; redraw = 1; }
    } else if (shown && reading()) {
        do { step(); end = units(); }
        while (reading() && end - begin < slice_units);
        if (io.dirty && (!reading() || end - io.drawn >= redraw_units)) { io.dirty = 0; redraw = 1; }
    } else end = units();
    const uint32_t pass = xyf::clock::ms_from_units(end - begin);
    if (pass > io.longest) io.longest = pass;
    if (redraw) { io.drawn = end; io.drawn_bar = the_page.bar; io.drawn_done = the_page.job_done; }
    return redraw;
}

uint32_t longest_ms() { return io.longest; }

bool menu_state(xyf::menu::State& a) {
    const m::Page& p = the_page;
    const xyf::catalog::Catalog* c = xyf_boot::catalog();
    const bool listed = io.step == s_done && p.view == m::View::list, have = rec.state == xg::present;
    const uint32_t def = c ? c->default_index : ~0u, slot = have ? index_of(rec.slot) : ~0u;
    // Applied: the default is usable (its content installed) and is what the record implies (no record: any).
    const char* implied = have ? (rec.count ? rec.slot : rec.base) : "";
    const bool any = c && def < c->count;      // no catalog or no default: the built-in copy, nothing to apply
    const bool usable = !any || (c->items[def].flags & xyf::catalog::flag_usable);
    const bool applied = usable && (!have || (any && implied[0] && xg::same(c->items[def].id, implied)));
    const bool changes = (listed && m::changed(p)) || p.expanded != p.expanded_saved;   // memory any time
    a.pending = changes || !applied || io.config_error;
    a.save_note = io.config_error ? io.config_error : listed ? m::save_refusal(p) : nullptr;
    a.save_blocked = a.save_note != 0;
    a.open_xy = slot; a.hidden = ~0u;
    if (slot != ~0u) {
        const uint32_t other = index_of(xg::same(rec.slot, "mods-a") ? "mods-b" : "mods-a");
        if (other != def) a.hidden = other;
    }
    char next[sizeof(label)];
    next[0] = 0;
    if (io.config_error) copy(next, "Saved configuration unavailable", sizeof(next));
    else if (have || changes) {
        const uint32_t n = listed ? m::chosen(p, false) + m::chosen(p, true) : rec.count;
        const uint32_t b = index_of(config_base() ? config_base() : "");
        xg::Out o = {next, next + sizeof(next) - 1, true};
        if (n) {
            xg::put(o, "Open-XY - "); xg::put_number(o, n); xg::put(o, n == 1 ? " mod - " : " mods - ");
            xg::put(o, c && b < c->count ? c->items[b].version : "?");
        } else xg::put(o, c && b < c->count ? c->items[b].name : "OP-XY");
        if (changes) xg::put(o, " (changed)");
        else if (!applied) xg::put(o, rec.count && slot == ~0u ? " (rebuild)" : " (to apply)");
        *o.at = 0;
    }
    a.continue_label = next[0] ? label : 0;
    const uint32_t memory = m::memory_row(p, io.step == s_done);   // the Settings row
    const bool memory_pending = p.expanded != p.expanded_saved, row_moved = memory != a.memory || memory_pending != a.memory_pending;
    a.memory = memory; a.memory_pending = memory_pending;
    bool same_text = true;
    for (uint32_t i = 0; i != sizeof(label) && same_text; ++i) { same_text = label[i] == next[i]; if (!next[i]) break; }
    if (!same_text) for (uint32_t i = 0; i != sizeof(label); ++i) label[i] = next[i];
    return !same_text || row_moved;
}

void save_begin() {
    if (sv.stage != sv_idle) return;
    ++sv.serial;
    sv.reason[0] = 0;
    sv.stage = sv_reading;
    usb_service::save_log_begin();              // the save-time log starts at the key
    if (io.step == s_done) the_page.line = m::line_none;   // a held list: the last result line no longer applies
    open();                                     // the list and the saved toggles, unless held already
}

uint32_t save_state() {
    return sv.stage == sv_idle ? uint32_t(save_idle) : sv.stage == sv_boot ? uint32_t(save_boot) :
           sv.stage == sv_failed ? uint32_t(save_failed) : uint32_t(save_running);
}

const char* save_reason() { return sv.reason; }

void save_done() { if (sv.stage == sv_boot || sv.stage == sv_failed) sv.stage = sv_idle; }
} }
#endif

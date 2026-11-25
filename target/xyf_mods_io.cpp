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
#include "xyf_config_io.hpp"
#include "xyf_converge.hpp"
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
    s_idle = 0,
    s_serving,
    s_mount,
    s_generations,
    s_store,
    s_base_list,
    s_base_native,
    s_base_info,
    s_services,
    s_packages,
    s_package_list,
    s_package_native,
    s_package_info,
    s_saved,
    s_done
};
enum Walk : uint32_t { walk_entry, walk_more, walk_done, walk_missing, walk_failed };
static const uint32_t max_candidates = 8;
struct Candidate {
    char name[32];
};
struct Io {
    uint32_t step, folder, why;                 // why: the status of a failed step
    uint32_t candidate_count, candidate_at, package_at;
    uint32_t drawn, longest, dirty;               // drawn: units() of the last redraw
    uint32_t job_units, drawn_bar, drawn_done;    // the job's start; the bar and done of the last redraw
    uint32_t waiting;                             // the reading waits for another job (page_busy)
    uint32_t newer, broken;                       // any unreadable native metadata while opening the list
    uint32_t development;                         // #204: development sets skipped (xyf dev's own)
    fc::NameScan metadata_scan;
    config_io::Scan config_scan;
    const char* config_error;
    uint32_t incomplete;                          // the reading ended with an error (the list is not all there)
    Candidate candidates[max_candidates];
    char path[240];
    uint8_t entry[fc::entry_bytes + 4];         // the last folder entry (walk_entry: its name at +11)
    uint8_t buffer[xyf_core::json::document_bytes];
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
const uint32_t slice_units = slice_ms * 390u + slice_ms * 5u / 8u,
               redraw_units = redraw_ms * 390u + redraw_ms * 5u / 8u;
const uint32_t progress_units = progress_ms * 390u + progress_ms * 5u / 8u,
               quiet_units = quiet_ms * 390u + quiet_ms * 5u / 8u;
// io.path = the parts joined; false when it does not fit (the path is then cut: never used).
bool path_of(const char* const* parts, uint32_t count) {
    uint32_t n = 0;
    bool fits = true;
    for (uint32_t k = 0; k != count; ++k)
        for (const char* s = parts[k]; *s; ++s) {
            if (n + 1 >= sizeof(io.path)) {
                fits = false;
                break;
            }
            io.path[n++] = *s;
        }
    io.path[n] = 0;
    return fits;
}
void copy(char* out, const char* s, uint32_t room) {
    uint32_t n = 0;
    while (s[n] && n + 1 < room) {
        out[n] = s[n];
        ++n;
    }
    out[n] = 0;
}
uint32_t failed(const fc::Reply& r) {
    if (r.outcome == fc::link) usb_service::page_mount() = fc::mounted_stale;
    return fc::status(r.outcome);
}
// One request of a walk over io.path (no callbacks: the builder's call audit takes direct calls only).
Walk walk() {
    if (!io.folder) {
        const fc::Reply r = fc::opendir(io.path);                         // op 0x2e
        if (r.outcome) {
            io.why = failed(r);
            return walk_failed;
        }
        if (!r.value || r.value == -1) {
            io.why = xyf::usbp::s_not_found;
            return walk_missing;
        }
        io.folder = uint32_t(r.value);
        return walk_more;
    }
    uint8_t* const entry = io.entry;
    const fc::Reply r = fc::readdir(io.folder, entry);                    // op 0x2f
    if (r.outcome) {
        io.folder = 0;
        io.why = failed(r);
        return walk_failed;
    }   // op 9 closes it at A
    const uint32_t got = fc::readdir_result(r.value);
    if (got == fc::folder_entry) {
        entry[11 + fc::max_name] = 0;
        return walk_entry;
    }
    const fc::Reply c = fc::closedir(io.folder);                          // op 0x30
    io.folder = 0;
    if (c.outcome) {
        io.why = failed(c);
        return walk_failed;
    }
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
    while (at && m::compare_generation(name, io.candidates[at - 1].name) > 0) {
        io.candidates[at] = io.candidates[at - 1];
        --at;
    }
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
    if (r.outcome) {
        io.why = failed(r);
        return false;
    }
    io.why = s_ok;
    return r.value == 0;
}
bool read_text(const char* path, uint32_t& bytes) {   // the whole small file into io.buffer
    bytes = 0;
    io.why = fc::read_file(path, 0, false, io.buffer, read_bytes, bytes, usb_service::page_mount());
    return io.why == s_ok;
}
#include "xyf_mods_metadata.ipp"
// #204: the name of an Open-XY entry whose content is installed while another entry is the default (xyf dev booted it
// once: the next plain start converges back to the saved setup), else 0.
const char* booted_once() {
    const xyf::catalog::Catalog* c = xyf_boot::catalog();
    for (uint32_t i = 0; c && i != c->count; ++i)
        if (i != c->default_index && c->items[i].kind == xyf::store::kind_open_xy &&
            (c->items[i].flags & xyf::catalog::flag_usable) &&
            !(c->items[i].flags & xyf::catalog::flag_content_missing))
            return c->items[i].name;
    return 0;
}
void finish(const char* error) {              // the list is final
    if (io.folder) {
        fc::closedir(io.folder);
        io.folder = 0;
    }
    io.step = s_done;
    io.incomplete = error ? 1u : 0u;
    the_page.view = m::View::list;
    if (error) m::say(the_page, m::line_error, error);
    else if (io.newer || io.broken)
        m::say(the_page, m::line_info,
               io.newer ? "Some packages need a newer XY Forever." : "Some packages have invalid manifest.json.");
    else if (const char* once = booted_once())
        m::say(the_page, m::line_info, "Booted once: ", once, ". The next start returns to the saved mods.");
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
        finish(io.newer                             ? "The mod packages need a newer XY Forever." :
               io.broken                            ? "The mod packages have invalid manifest.json." :
               io.candidate_count != io.development ? "No complete mod package set found." :
                                                      0);
        return;
    }
    io.step = s_store;
}
uint32_t read_record(bool toggles, uint32_t& bytes);
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
    for (uint32_t i = 0; i != c->count; ++i)
        if (fits(i)) return c->items[i].id;
    return 0;
}
#include "xyf_mods_save.ipp"              // the saved configuration and the converge sequence
#include "xyf_mods_usb.ipp"               // config.get, config.set (#26)
}

m::Page& page() { return the_page; }

void open() {
    m::Page& p = the_page;
    if (rec.state == xg::failed) rec.state = xg::unread;   // try the record again
    if (p.view == m::View::list && p.shown) return;   // kept for this boot
    if (io.folder) return;                       // (never: pump closes it when the page is left)
    restart_config_lookup();
    m::reset(p);
    io.step = s_serving;
    io.candidate_count = io.package_at = io.newer = io.broken = io.development = 0;
    io.dirty = 1;
    io.waiting = 0;
    m::say(p, m::line_info, "Reading mod packages...");
}

// The page's flips (Pick): each row's toggle, Mod memory and unknown bases, as one number that any flip changes.
uint32_t toggles() {
    const m::Page& p = the_page;
    uint32_t h = (p.expanded ? 1u : 0u) | (p.unknown_ok ? 2u : 0u);
    for (uint32_t r = 0; r != p.shown; ++r) h = h * 33u + (p.packages[p.rows[r]].flags & m::flag_on);
    return h;
}
void flipped() {
    pick.flipped = 1;
    pick.since = units();
}

m::Effect key(uint32_t k, bool pressed) {
    const uint32_t before = toggles();
    const m::Effect effect = m::key(the_page, k, pressed);
    if (toggles() != before) flipped();          // saved at the next quiet moment
    return effect;
}

void prepare() {
    if (io.step == s_idle && sv.stage == sv_idle) open();
}

const char* memory_flip() {
    const char* why = m::flip_memory(the_page, io.step == s_done);
    if (!why) flipped();
    return why;
}

bool saving() { return pick.stage == 1; }

uint32_t pump(bool shown) {
    const uint32_t begin = units();
    shown = shown || sv.stage == sv_reading || sv.stage == sv_listing || // the sequence reads the list, page hidden,
            remote.stage == 1; // as does config.set (#26)
    uint32_t redraw = converge_follow();
    // #178: once A serves the library and the record is read: check()'s own condition (no stock entry, or no record and
    // no default: PLAY at the first start after Track 5, or after a cut adoption) converges as a plain power-on would,
    // adopting the stock entry, and the menu stays. Anything else: a PLAY start writes nothing.
    if (!sv.looked && sv.stage == sv_idle && !reading() && xyf_boot::catalog() && !usb_service::page_busy() &&
        usb_service::page_serving() == s_ok && (rec.state == xg::none || rec.state == xg::present)) {
        sv.looked = 1;
        const xyf::catalog::Catalog* c = xyf_boot::catalog();
        if (!config_base() || (rec.state == xg::none && c->default_index >= c->count)) {
            converge_begin(why_adopt);
            redraw = 1;
        }
    }
    // The saved configuration, once the menu runs and again after a failed sequence.
    uint32_t bytes = 0;
    if ((rec.state == xg::unread || pick.stage == 2) && sv.stage == sv_idle && !reading() &&
        !usb_service::page_busy() && usb_service::page_serving() == s_ok) {
        const uint32_t code = read_record(false, bytes);
        if (code != s_ok && code != xyf::usbp::s_busy) {
            rec.state = xg::failed;
            pick.stage = 0;
        } else if (code == s_ok && shown && io.step == s_done && !redraw)
            redraw = 1; // the saved marks moved (the page: "saved")
    }
    if (sv.stage == sv_idle) pick_step(false); // a flip saved once quiet (the reading below may wait for it)
    // While another job owns the slot or its window closes, the reading sends nothing (a request
    // then met a stale folder handle or waited out A's bound). A's handles end with that window: the folder
    // is forgotten, not closed, and the reading starts again from the top once the job is over.
    const bool busy = reading() && usb_service::page_busy();
    if (busy) {
        io.folder = io.metadata_scan.folder = 0;
        restart_config_lookup();
    }
    if (!shown && reading()) { // left mid-way: close the folder, read again next time
        if (io.folder) {
            fc::closedir(io.folder);
            io.folder = 0;
        }
        fc::close_name_scan(io.metadata_scan, usb_service::page_mount());
        restart_config_lookup();
        io.step = s_idle;
    }
    if (shown && busy && !io.waiting) {
        m::reset(the_page);
        io.step = s_serving;
        io.candidate_count = io.package_at = io.newer = io.broken = io.development = 0;
        io.waiting = 1;
        io.dirty = 1;
        m::say(the_page, m::line_info, "Busy: a build or content change is running.");
    } else if (shown && reading() && io.waiting && !busy) {
        io.waiting = 0;
        io.dirty = 1;
        m::say(the_page, m::line_info, "Reading mod packages...");
    }
    uint32_t end = begin;
    if (shown && busy) {
        end = units();
        if (io.dirty) {
            io.dirty = 0;
            redraw = 1;
        }
    } else if (shown && reading()) {
        do {
            step();
            end = units();
        } while (reading() && end - begin < slice_units);
        if (io.dirty && (!reading() || end - io.drawn >= redraw_units)) {
            io.dirty = 0;
            redraw = 1;
        }
    } else end = units();
    const uint32_t pass = xyf::clock::ms_from_units(end - begin);
    if (pass > io.longest) io.longest = pass;
    if (redraw) {
        io.drawn = end;
        io.drawn_bar = run.bar;
        io.drawn_done = run.job_done;
    }
    return redraw;
}

uint32_t longest_ms() { return io.longest; }

bool menu_state(xyf::menu::State& a) {
    const m::Page& p = the_page;
    const xyf::catalog::Catalog* c = xyf_boot::catalog();
    const bool listed = io.step == s_done && p.view == m::View::list, have = rec.state == xg::present;
    // The plan of the saved inputs (xyf_converge.hpp) as far as the catalog tells: built= of the saved selection (its
    // mods= names), the slot's stamp as the record says while the slot is in the library (the sequence reads it).
    const char* base = config_base();
    char want[sizeof(rec.built)];
    want[0] = 0;
    if (have && base) xg::built_text(want, sizeof(want), base, rec.generation, rec_set, rec.count, rec.expanded != 0);
    const uint32_t def = c ? c->default_index : ~0u, slot = have ? index_of(rec.slot) : ~0u;
    const xc::Inputs in = {base ? base : "", want, have ? rec.count : 0u, have ? rec.expanded : 0u,
                           have ? rec.safe : 0u};
    const xc::Actual act = {&rec,
                            slot != ~0u ? rec.stamp : "",
                            c && def < c->count ? c->items[def].id : "",
                            installed(index_of(in.base)),
                            installed(slot),
                            installed(def)};
    const uint32_t ops = have || rec.state == xg::none ? xc::plan(in, act).ops : 0u;
    const bool unsaved = pick.flipped || pick.stage; // a flip not written yet: PLAY writes it, then converges
    a.pending = unsaved || ops || io.config_error;
    a.continue_note = io.config_error ? io.config_error : listed ? m::save_refusal(p) : 0;
    char next[sizeof(label)];
    next[0] = 0;
    if (io.config_error) copy(next, "Saved configuration unavailable", sizeof(next));
    else if (have || unsaved) {
        const uint32_t n = listed ? m::chosen(p, false) + m::chosen(p, true) : rec.count;
        const uint32_t b = index_of(in.base);
        xg::Out o = {next, next + sizeof(next) - 1, true};
        if (have && rec.safe) {
            xg::put(o, c && b < c->count ? c->items[b].name : "OP-XY");
            xg::put(o, " (safe mode)");
        } else if (n) {
            xg::put(o, "Open-XY - ");
            xg::put_number(o, n);
            xg::put(o, n == 1 ? " mod - " : " mods - ");
            xg::put(o, c && b < c->count ? c->items[b].version : "?");
        } else xg::put(o, c && b < c->count ? c->items[b].name : "OP-XY");
        if (ops & xc::op_build) xg::put(o, " (rebuild)");
        else if (ops || unsaved) xg::put(o, " (to apply)");
        *o.at = 0;
    }
    a.continue_label = next[0] ? label : 0;
    const uint32_t memory = m::memory_row(p, io.step == s_done); // the Settings row
    const bool memory_pending = p.expanded != p.expanded_saved,
               row_moved = memory != a.memory || memory_pending != a.memory_pending;
    a.memory = memory;
    a.memory_pending = memory_pending;
    bool same_text = true;
    for (uint32_t i = 0; i != sizeof(label) && same_text; ++i) {
        same_text = label[i] == next[i];
        if (!next[i]) break;
    }
    if (!same_text)
        for (uint32_t i = 0; i != sizeof(label); ++i) label[i] = next[i];
    // The safe-mode row while the saved setup has mods: "Boot without mods", or in safe mode the way back.
    char row[sizeof(safe_text)];
    row[0] = 0;
    if (have && rec.count && rec.slot[0]) {
        xg::Out o = {row, row + sizeof(row) - 1, true};
        if (rec.safe) {
            xg::put(o, "Restore Open-XY (");
            xg::put_number(o, rec.count);
            xg::put(o, rec.count == 1 ? " mod)" : " mods)");
        } else xg::put(o, "Boot without mods");
        *o.at = 0;
    }
    a.safe_label = row[0] ? safe_text : 0;
    a.safe_restore = have && rec.safe;
    bool same_row = true;
    for (uint32_t i = 0; i != sizeof(safe_text) && same_row; ++i) {
        same_row = safe_text[i] == row[i];
        if (!row[i]) break;
    }
    if (!same_row)
        for (uint32_t i = 0; i != sizeof(safe_text); ++i) safe_text[i] = row[i];
    return !same_text || !same_row || row_moved;
}

void converge_begin(uint32_t why) { // never the page: the list, its toggles and views stay as they are
    if (sv.stage != sv_idle) return;
    sv.why = why;
    sv.ops = sv.length = sv.adopted = sv.failed = sv.tidied = sv.restored = 0;
    sv.looked = 1;
    ++sv.serial;
    sv.reason[0] = 0;
    sv.stage = sv_check; // a flip not written yet is written first (converge_follow)
    m::Progress& r = run; // the progress screen from its first frame
    r.job = 0x10000u + sv.serial;
    r.job_phase = r.job_done = r.job_total = r.job_commit = 0;
    r.bar = r.bar_step = r.bar_since = 0;
    r.save = m::save_on;
    r.save_stage = 0;
    r.cancel = 0;
    r.reading = 0;
    r.title = why == why_safe     ? "Booting without mods" :
              why == why_restore  ? "Restoring Open-XY" :
              why == why_power_on ? "Checking the saved setup" :
              why == why_adopt    ? "Adding OS 1.2.1" :
              why == why_once     ? "Booting once" :
                                    "Applying changes";
    r.job_name[0] = 0;
    r.note[0] = 0;
    io.job_units = units();
    usb_service::save_log_begin(); // the save-time log starts at the key
    if (io.step == s_done && !io.incomplete) the_page.line = m::line_none; // a held list: the last result line goes
}

bool converging() { return sv.stage != sv_idle && sv.stage != sv_failed; }

// #204: entry id booted once with its own content: the switch (always: a rebuilt entry keeps its id), then the boot.
// #284: the stock base's content set first (#183's job, as before a build; at once when it is there): a switch from
// the official content needs it, and an OP-XY that never saved mods has none.
uint32_t converge_once(const char* id) {
    const uint32_t i = index_of(id);
    if (i == ~0u) return xyf::usbp::s_not_found;
    if (!(xyf_boot::catalog()->items[i].flags & (xyf::catalog::flag_usable | xyf::catalog::flag_content_missing)))
        return xyf::usbp::s_state; // no os.ldr
    converge_begin(why_once);
    usb_service::save_log_drop(); // no record follows: the switch writes its own line in the save-time log
    copy(sv.target, id, sizeof(sv.target));
    sv.ops = xc::op_switch;
    sv.stage = sv_switch;
    const char* base = base_entry();
    if (!base) return s_ok;
    copy(sv.base, base, sizeof(sv.base));
    content_set(); // sv_content_set, or failed with the build's reason (the menu says it; mode 0 reports it)
    return s_ok;
}

uint32_t converge_entry() { return sv.why == why_once ? index_of(sv.target) : ~0u; }

const m::Progress& progress() { return run; }

m::Effect progress_key(uint32_t k, bool pressed) { return m::progress_key(run, k, pressed); }

uint32_t converge_state() {
    if (sv.stage == sv_boot && sv.why == why_adopt) return converge_note; // #178: added; the menu stays
    return sv.stage == sv_idle   ? uint32_t(converge_idle) :
           sv.stage == sv_boot   ? uint32_t(converge_boot) :
           sv.stage == sv_failed ? uint32_t(converge_failed) :
                                   uint32_t(converge_running);
}

const char* converge_reason() { // at sv_boot only why_adopt's line (converge_note): what it did
    if (sv.stage != sv_boot) return sv.reason;
    if (sv.tidied || sv.restored) { // #334: the light check cleaned another tool's content (Open-XY's)
        char n[12];
        m::decimal(n, sv.tidied);
        xg::Out o = {sv.reason, sv.reason + sizeof(sv.reason) - 1, true};
        if (sv.tidied) {
            xg::put(o, "Removed ");
            xg::put(o, n);
            xg::put(o, sv.tidied == 1 ? " file from Open-XY" : " files from Open-XY");
            xg::put(o, sv.restored ? ", put back " : ".");
        } else xg::put(o, "Put back ");
        if (sv.restored) xg::put(o, "OS 1.2.1's op1_factory.json.");
        *o.at = 0;
        return sv.reason;
    }
    return sv.adopted == 2 ? "OS 1.2.1 is in the library and is the default." :
           sv.adopted == 1 ? "OS 1.2.1 is the default." :
                             "";
}

void converge_done() {
    if (sv.stage == sv_boot || sv.stage == sv_failed) sv.stage = sv_idle;
}

uint32_t converge_request(uint32_t mode, const char* id) {
    if (sv.stage != sv_idle || usb_service::page_busy()) return xyf::usbp::s_busy;
    if (usb_service::page_serving() != s_ok) return xyf::usbp::s_link;
    if (mode == 1) {
        converge_begin(why_apply);
        return s_ok;
    }
    if (usb_service::content_unfinished()) return xyf::usbp::s_busy; // the menu's replay first
    return converge_once(id);
}

void converge_report(ConvergeView& v) {
    usb_service::JobView j;
    usb_service::job_view(j);
    v.state = sv.stage == sv_boot && sv.why != why_adopt ? 3u : converging() ? 1u : sv.failed ? 2u : 0u;
    v.job = j.state ? j.id : 0u;
    v.reason = v.state == 2 ? sv.reason : "";
}

uint32_t config_view(ConfigView& v) { return remote_view(v); }

uint32_t config_set(uint32_t flags, const char* mods, uint32_t crc, const char*& reason) {
    return remote_set(flags, mods, crc, reason);
}
}}
#endif

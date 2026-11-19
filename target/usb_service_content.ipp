// Part of usb_service.cpp, included there at this point: not compiled alone.
#if defined(XYF_CONTENT_SWITCH)
// ---- The content switch (xyf_content_job.hpp) as the job slot's second kind (job_kind 1) ----------
// library.set_default or the menu's SHIFT+PLAY on an entry whose content is not installed starts it: the same
// single-job rule (job_state; job_blocks answers busy), job.status reports it, job.cancel answers state (a
// switch is repaired by running it again, not undone). Its state follows the mod job's slot, its work region
// after that. One file request per step, steps in slices of job_slice_ms. At the end B closes A's write window
// (A rescans: the catalog shows the new state), then, after a success, writes the default (default_commit).
// Every write passed xyf::content::allowed (the job), the USB write area (ContentIo) and A's engine.
namespace xc = xyf::content;
namespace xj = xyf::journal;
xj::Record recovery_record __attribute__((section(".data"))) = {};
volatile U32 recovery_offer __attribute__((section(".data"))) = 0; // 1 missing source, 2 invalid, 3 newer
void recovery_turn();

enum ContentStage : U32 { cs_switch = 0, cs_closing, cs_ended, cs_opening, cs_prepare, cs_validate, cs_cleanup, cs_clean_close };
// 193: the switch opens A's write window first without waiting in it (B keeps answering USB: job.status
// says "opening storage for writing"); the first read-write mount after an unclean shutdown is slow.
const char window_folder[] = "/yaffs2/xy-forever/tmp";
const char opening_text[] = "opening storage for writing";
struct ContentSlot {
    xc::Job job;
    ContentIo io;
    U32 id, started_ms, ended_ms, longest_ms, stage, menu, said_ms, said_phase, line_seq;
    U32 write_default;                                                   // 0 for the save sequence's switch
    char line[96], progress[24];
    xj::Record intent; xj::Source source; xj::File intent_file;
    U32 prepared, prepare_step, replay, cleaning;
};
const U32 content_slot_bytes = (sizeof(ContentSlot) + 63u) & ~63u;
// One line appended to the log inside A's open write window (before its close); every failure ignored.
void save_log_write(bool ok) {
    if (save_times.kind && writes_due && a_serving()) {
        save_times.ok = ok ? 1u : 0u;
        char* const text = reinterpret_cast<char*>(XYF_JOB_REGION_BEGIN + job_slot_bytes + content_slot_bytes + 0x10000u);
        char* const line = text + xl::max_text + 64;
        const U32 line_n = xl::format(line, xl::max_line, save_times, now());
        uint32_t error = 0, old_n = 0;
        file_client::change(0x26u, xl::folder, error, mounted);           // mkdir (it may exist)
        if (file_client::read_file(xl::path, 0, false, reinterpret_cast<uint8_t*>(text), xl::max_text, old_n, mounted) != P::s_ok)
            old_n = 0;
        const U32 n = line_n ? xl::merge(text, old_n, line, line_n) : 0u;
        const file_client::Reply o = n ? file_client::open(xl::part_path, storage_server::write_open_flags) : file_client::Reply();
        bool written = n && !o.outcome && o.value >= 0;
        for (U32 at = 0; written && at != n;) {
            const U32 want = n - at < file_client::chunk ? n - at : file_client::chunk;
            const file_client::Reply w = file_client::write(o.value, reinterpret_cast<const uint8_t*>(text + at), want);
            written = !w.outcome && w.value > 0 && U32(w.value) <= want;
            if (written) at += U32(w.value);
        }
        if (n && !o.outcome && o.value >= 0) written = !file_client::close(o.value).outcome && written;
        if (written) {
            file_client::change(0x25u, xl::path, error, mounted);           // unlink: A never renames onto a file
            file_client::rename(xl::part_path, xl::path, error, mounted);
        }
    }
    save_times.kind = xl::k_none; save_active = 0;
}
ContentSlot& cslot() { return *reinterpret_cast<ContentSlot*>(XYF_JOB_REGION_BEGIN + job_slot_bytes); }
volatile U32 content_line_shown __attribute__((section(".data"))) = 0;
U32 content_phase(const ContentSlot& c) {                                 // final only once the default is written
    const U32 ph = xc::phase(c.job);
    return c.stage != cs_ended && ph >= xc::phase_done ? U32(xc::phase_finish) : ph;
}
void content_text(ContentSlot& c, const char* a, const char* b = "", const char* d = "") {   // the menu's line
    U32 n = 0;
    for (const char* parts[3] = {a, b, d}, **k = parts; k != parts + 3; ++k)
        for (const char* t = *k; *t && n + 1 < sizeof(c.line); ++t) c.line[n++] = *t;
    c.line[n] = 0;
    ++c.line_seq;
}
void content_say(ContentSlot& c, bool force) {
    const U32 t = now(), ph = content_phase(c);
    if (c.stage == cs_ended) {
        if (c.job.stage == xc::st_done) {
            say("Default firmware set.", c.job.target, "Its content is installed.");
            content_text(c, "Default: ", c.job.target, " (content installed)");
        } else {
            say("Content switch FAILED.", c.job.detail, "Set the default again to repair.");
            content_text(c, "Content switch failed: ", c.job.detail);
        }
        return;
    }
    if (!force && ph == c.said_phase && t - c.said_ms < job_say_ms) return;
    c.said_phase = ph; c.said_ms = t;
    if (c.stage == cs_opening) {                                         // 193
        say("Opening storage for writing...", c.job.target, "", "Do not unplug or power off.");
        content_text(c, "Opening storage for writing: ", c.job.target);
        return;
    }
    U32 n = decimal_text(c.progress, c.job.done);
    c.progress[n++] = '/';
    decimal_text(c.progress + n, c.job.total);
    say("Installing content...", c.job.target, c.progress, "Do not unplug or power off.");
    content_text(c, "Installing content ", c.progress);
}
U32 content_start(const char* id, bool menu, bool write_default, bool replay) {
    stop_idle_gc();
    if (job_running() || closing XYF_OR_MTP_BUSY || uploading()) return P::s_busy;
    if (!a_serving()) return P::s_link;
    U32 code = file_client::mount_ready(mounted);
    if (code != P::s_ok) return code;
    ContentSlot& c = cslot();
    const xyf::catalog::Catalog* catalog = xyf_boot::catalog();         // every entry's content.sha256 is a candidate
    const char* candidates[xc::max_candidates];
    U32 n = 0;
    for (U32 i = 0; catalog && i != catalog->count && n != xc::max_candidates; ++i) candidates[n++] = catalog->items[i].id;
    uint8_t* const work = reinterpret_cast<uint8_t*>(XYF_JOB_REGION_BEGIN + job_slot_bytes + content_slot_bytes);
    if (!xc::start(c.job, id, candidates, n, work, job_work_end - XYF_JOB_REGION_BEGIN - job_slot_bytes - content_slot_bytes))
        return P::s_bad_request;
    if ((code = enter_writes()) != P::s_ok) return code;
    storage_used = 1;
    writes_due = 1; writes_ms = now();                                  // closed when the switch ends
    c.io.link = 0; c.id = ++job_counter; c.started_ms = c.ended_ms = now(); c.longest_ms = 0;
    c.stage = file_client::mkdir_start(window_folder).outcome ? cs_switch : cs_opening;   // 193: not sent: the first change opens it
    xj::begin(c.intent_file); c.source.fd = -1;
    c.prepared = 0; c.prepare_step = 0; c.replay = replay ? 1u : 0u; c.cleaning = 0;
    if (replay) { c.intent = recovery_record; c.stage = cs_validate; xj::source_begin(c.source, id); }
    c.menu = menu ? 1u : 0u; c.said_ms = 0; c.said_phase = ~0u; c.write_default = write_default ? 1u : 0u;
    if (save_active) save_lap(xl::p_switch); else xl::begin(save_times, xl::k_switch, now(), xl::p_switch);
    job_kind = 1; job_state = job_busy; job_usb = menu ? 0u : 1u;
    content_say(c, true);
    return P::s_ok;
}
void intent_pump(ContentSlot& c);
void content_pump() {
    ContentSlot& c = cslot();
    const U32 start = now();
    if (c.stage == cs_prepare || c.stage == cs_validate) { intent_pump(c); return; }
    if (c.stage == cs_opening) {                                         // 193: A opens its write window
        const file_client::Reply r = file_client::unmount_poll();
        if (r.outcome == file_client::busy) { content_say(c, false); return; }
        if (r.outcome == file_client::link) mounted = file_client::mounted_stale;   // past its bound: still out
        if (r.outcome) c.io.link = 1;                                    // the switch fails (link), then closes
        c.stage = cs_switch;
        content_say(c, true);
    }
    if (c.stage == cs_switch) {
        U32 ph;
        do {
            if (!c.prepared && (c.job.stage == xc::st_saved_list || c.job.stage == xc::st_mark)) {
                c.stage = cs_prepare; c.prepare_step = 0; ph = xc::phase_inputs; break;
            }
            ph = xc::step(c.job, c.io);
        } while (ph < xc::phase_done && now() - start < job_slice_ms);
        if (ph >= xc::phase_done) {                                      // op 9: A rescans, B fetches the catalog
            c.stage = cs_closing;
            save_times.compared = c.job.compared; save_times.read = c.job.read; save_times.written = c.job.written;
            save_times.removed_files = c.job.removed; save_times.bytes = c.job.bytes;
            save_lap(xl::p_switch_close);
            if (!save_active) save_log_write(c.job.stage == xc::st_done && !c.io.link);   // in its window
            settle_begin();
        }
        else content_say(c, false);
    } else if (c.stage == cs_closing && !closing) {
        if (c.job.stage == xc::st_done && c.write_default) {             // the default, only after a success
            const U32 code = default_commit(xyf::state_write::id_hash(c.job.target));
            if (code != P::s_ok) {
                c.job.stage = xc::st_failed; c.job.error = code == P::s_link ? xc::e_link : xc::e_write; c.job.code = code;
                xc::note(c.job, "default");
            }
        }
        if (c.job.stage != xc::st_done && journal_present) recovery_offer = 1;
        if (c.job.stage == xc::st_done && journal_present && c.write_default) { c.stage = cs_cleanup; c.cleaning = 0; }
        else { c.ended_ms = now(); c.stage = cs_ended; job_state = job_ended; boot_sweep = 3; gc_defer_ms = now(); content_say(c, true); }
    } else if (c.stage == cs_cleanup) {
        if (!c.cleaning) {
            U32 code = enter_writes();
            if (code == P::s_ok) code = file_client::mount_ready(mounted);
            if (code != P::s_ok) { xc::fail(c.job, xc::e_write, code, "journal cleanup"); c.stage = cs_closing; return; }
            storage_used = 1; writes_due = 1; writes_ms = now(); ++c.cleaning; return;
        }
        // The .old must disappear before the authoritative journal, so a cut cannot resurrect old intent.
        const char* paths[3] = {xj::old, xj::part, xj::path};
        if (c.io.unlink(paths[c.cleaning-1]) < 0) { xc::fail(c.job, xc::e_write, 0, "journal cleanup"); c.stage = cs_closing; settle_begin(); return; }
        if (++c.cleaning == 4) { journal_present = 0; recovery_offer = 0; c.stage = cs_clean_close; settle_begin(); }
    } else if (c.stage == cs_clean_close && !closing) {
        c.ended_ms = now(); c.stage = cs_ended; job_state = job_ended; boot_sweep = 3; gc_defer_ms = now(); content_say(c, true);
    }
    const U32 pass = now() - start;
    if (pass > c.longest_ms) c.longest_ms = pass;
}
U32 content_begin(const char* id, bool menu, bool write_default) ;
// The catalog mods.apply reads: the live one in the official state, else the saved official copy.
const char* content_job_catalog() {
    uint8_t head[xc::head_bytes];
    uint32_t bytes = 0;
    xc::State state;
    const U32 code = file_client::read_file(xc::record_path, 0, true, head, sizeof(head), bytes, mounted);
    if (code == P::s_not_found)                                          // only a .old: mid-switch, not official
        return file_client::read_file(xc::record_old, 0, true, head, sizeof(head), bytes, mounted) == P::s_ok ?
               xc::saved_catalog : job_catalog;
    if (code != P::s_ok) return job_catalog;
    xc::parse(head, bytes, state);
    return state.kind == xc::official ? job_catalog : xc::saved_catalog;
}
void config_status(uint8_t seq, U32 want);
// JOB_STATUS of a content switch: as the mod job's fields (phase inputs / content / finish; done, total: files).
void content_status(uint8_t seq, U32 want) {
    ContentSlot& c = cslot();
    uint8_t* p = files;
    const U32 code = want && want != c.id ? P::s_not_found : P::s_ok;
    P::put32(p, code);
    if (code != P::s_ok) { result.last_status = code; send(P::t_job_status, seq, p, 4); return; }
    const bool ended = c.stage == cs_ended;
    const U32 values[11] = {c.id, content_phase(c), c.job.done, c.job.total, ended ? c.job.error : 0u, ended ? c.job.code : 0u,
                            (ended ? c.ended_ms : now()) - c.started_ms, c.job.steps, c.longest_ms, c.job.peak, 0};
    for (U32 i = 0; i != 11; ++i) P::put32(p + 4 + 4 * i, values[i]);
    for (U32 i = 0; i != 32; ++i) p[48 + i] = 0;
    const char* detail = c.stage == cs_opening ? opening_text : c.job.detail;   // 193
    for (U32 i = 0; i != 31 && detail[i]; ++i) p[48 + i] = uint8_t(detail[i]);
    result.last_status = code;
    send(P::t_job_status, seq, p, 80);
}
#endif
#if defined(XYF_CONTENT_SWITCH)
// ---- The config job (job_kind 2): the saved configuration's record (xyf_config.hpp), then the default ---
// The save sequence's last step (xyf_mods_io): config.txt.part written, then config.txt -> config.old (A never
// renames onto a file), .part -> config.txt, config.old removed; A's window closed; then, after a success, the
// default (default_commit) unless it is that entry already. A failure or cut leaves the old record (or only its
// .old, which the reader takes) and the old default. One file request per step, as the content switch.
namespace xg = xyf::config;
struct ConfigSlot {
    ContentIo io;
    int fd;
    U32 id, step, at, length, error, code, had, started_ms, ended_ms, hash, cleaning;
    const char *path, *old_path, *part_path;     // config.txt's, or settings.txt's (xyf_settings.hpp)
    char text[xg::max_text];
};
// In the content switch's work region (one job at a time; the switch has ended when this starts).
ConfigSlot& gslot() { return *reinterpret_cast<ConfigSlot*>(XYF_JOB_REGION_BEGIN + job_slot_bytes + content_slot_bytes); }
enum ConfigStep : U32 { cg_open = 0, cg_write, cg_close, cg_stat, cg_unlink_old, cg_away, cg_in, cg_drop, cg_closing, cg_cleanup, cg_clean_close, cg_ended };
U32 config_begin(const char* text, U32 length, const char* id, const char* path = xg::path,
                 const char* old_path = xg::old_path, const char* part_path = xg::part_path) {
    stop_idle_gc();
    if (job_running() || closing XYF_OR_MTP_BUSY || uploading()) return P::s_busy;
    if (!a_serving()) return P::s_link;
    if (!length || length > xg::max_text) return P::s_bad_request;
    U32 code = file_client::mount_ready(mounted);
    if (code != P::s_ok || (code = enter_writes()) != P::s_ok) return code;
    ConfigSlot& g = gslot();
    for (U32 i = 0; i != length; ++i) g.text[i] = text[i];
    g.path = path; g.old_path = old_path; g.part_path = part_path;
    g.length = length; g.at = 0; g.fd = -1; g.step = cg_open; g.error = g.code = g.had = 0; g.io.link = 0;
    g.hash = id && *id ? xyf::state_write::id_hash(id) : 0u;
    storage_used = 1;
    writes_due = 1; writes_ms = now();                                  // closed when the record is in
    g.cleaning = 0; g.id = ++job_counter; g.started_ms = g.ended_ms = now();
    job_kind = 2; job_state = job_busy; job_usb = 0;
    if (save_active && path == xg::path) save_lap(xl::p_config);       // a Save's record
    return P::s_ok;
}
void config_step(ConfigSlot& g) {
    const U32 failed = g.step;
    switch (g.step) {
    case cg_open:
        g.fd = g.io.open(g.part_path, true);
        if (g.fd >= 0) g.step = cg_write;
        break;
    case cg_write: {
        const U32 want = g.length - g.at < file_client::chunk ? g.length - g.at : file_client::chunk;
        const int put = g.io.write(g.fd, reinterpret_cast<const uint8_t*>(g.text + g.at), want);
        if (put > 0 && U32(put) <= want) { g.at += U32(put); if (g.at == g.length) g.step = cg_close; }
        break;
    }
    case cg_close:
        if (!g.io.close(g.fd)) g.step = cg_stat;
        g.fd = -1;
        break;
    case cg_stat: {
        uint8_t record[file_client::record_bytes];
        const file_client::Reply r = file_client::stat(g.path, record);
        if (r.outcome) { g.io.fail(r); break; }
        g.had = r.value == 0 ? 1u : 0u;
        g.step = g.had ? cg_unlink_old : cg_in;
        break;
    }
    case cg_unlink_old: if (g.io.unlink(g.old_path) >= 0) g.step = cg_away; break;   // a stale .old
    case cg_away: if (!g.io.rename(g.path, g.old_path)) g.step = cg_in; break;
    case cg_in: if (!g.io.rename(g.part_path, g.path)) g.step = g.had ? cg_drop : cg_closing; break;
    case cg_drop: if (g.io.unlink(g.old_path) >= 0) g.step = cg_closing; break;
    default: return;
    }
    if (g.step == failed) {                                             // this step failed: nothing more is written
        g.error = g.io.lost() ? xc::e_link : failed == cg_away || failed == cg_in ? xc::e_rename : xc::e_write;
        g.code = g.io.lost() ? P::s_link : P::s_storage;
        g.step = cg_closing;
    }
    if (g.step == cg_closing) {                                         // op 9: A rescans, B fetches the catalog
        if (save_active && g.path == xg::path) save_log_write(!g.error && !g.io.link);   // the Save's line
        settle_begin();
    }
}
void config_pump() {
    ConfigSlot& g = gslot();
    const U32 start = now();
    while (g.step < cg_closing && now() - start < job_slice_ms) config_step(g);
    if (g.step == cg_cleanup) {
        if (!g.cleaning) {
            U32 code = enter_writes();
            if (code == P::s_ok) code = file_client::mount_ready(mounted);
            if (code != P::s_ok) { g.error = xc::e_write; g.code = code; g.step = cg_clean_close; return; }
            storage_used = 1; writes_due = 1; writes_ms = now(); ++g.cleaning; return;
        }
        const char* paths[3] = {xj::old, xj::part, xj::path};
        if (g.io.unlink(paths[g.cleaning-1]) < 0) { g.error = xc::e_write; g.step = cg_clean_close; settle_begin(); return; }
        if (++g.cleaning == 4) { journal_present = 0; recovery_offer = 0; g.step = cg_clean_close; settle_begin(); }
        return;
    }
    if (g.step == cg_clean_close && !closing) {
        g.step = cg_ended; g.ended_ms = now(); job_state = job_ended; boot_sweep = 3; gc_defer_ms = now(); return;
    }
    if (g.step != cg_closing || closing) return;
    const xyf::catalog::Catalog* catalog = xyf_boot::catalog();
    const bool already = catalog && catalog->default_index < catalog->count &&
                         xyf::state_write::id_hash(catalog->items[catalog->default_index].id) == g.hash;
    if (!g.error && g.hash && !already) {
        const U32 code = default_commit(g.hash);
        if (code != P::s_ok) { g.error = code == P::s_link ? xc::e_link : xc::e_write; g.code = code; }
    }
    if (!g.error && g.hash && journal_present && g.hash == xyf::state_write::id_hash(recovery_record.target)) {
        g.step = cg_cleanup; return; // native Save retains intent through its config + default commit
    }
    g.step = cg_ended; g.ended_ms = now();
    job_state = job_ended; boot_sweep = 3; gc_defer_ms = now();
}
// JOB_STATUS of the config job: the switch's fields (phase 0 running, done 8, failed 9; done, total: steps).
void config_status(uint8_t seq, U32 want) {
    ConfigSlot& g = gslot();
    uint8_t* p = files;
    const U32 code = want && want != g.id ? P::s_not_found : P::s_ok;
    P::put32(p, code);
    if (code != P::s_ok) { result.last_status = code; send(P::t_job_status, seq, p, 4); return; }
    const bool ended = g.step == cg_ended;
    const U32 values[11] = {g.id, ended ? (g.error ? U32(xc::phase_failed) : U32(xc::phase_done)) : 0u, g.step, cg_ended,
                            ended ? g.error : 0u, ended ? g.code : 0u, (ended ? g.ended_ms : now()) - g.started_ms, g.step, 0, 0, 0};
    for (U32 i = 0; i != 11; ++i) P::put32(p + 4 + 4 * i, values[i]);
    for (U32 i = 0; i != 32; ++i) p[48 + i] = 0;
    const char detail[] = "config";
    for (U32 i = 0; detail[i]; ++i) p[48 + i] = uint8_t(detail[i]);
    result.last_status = code;
    send(P::t_job_status, seq, p, 80);
}
#endif

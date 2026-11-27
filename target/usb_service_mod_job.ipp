// Part of usb_service.cpp, included there at this point: not compiled alone.
// ---- The mod assembly job on B (native/mod_job's flow, run on B) ----------------
// mods.apply starts it, B's menu loop runs it in slices of steps (job_pump), job.status reads it and
// job.cancel stops it. Its state and work region live in XYF_JOB_REGION (cold_layout.hpp: 8 MiB on B, the
// builder's memory map keeps everything else out). Every file call is one bounded file-client request
// (reads and writes 1 KiB: mod_job accepts short reads and writes), and every path is limited here: reads
// only under the generation folder, the base entry folder, the output folder and the one stock catalog
// file; writes, mkdir, remove and rename only under the output folder /yaffs2/xy-forever/tmp/job-<id>/
// (and through the engine's own policy, which A applies again). One job at a time; while it runs, every
// command that needs A, the staging or a boot answers busy (job_blocks). A request that runs out its bound
// (or a lost link) fails the job with error link at once: later calls send nothing. A failed or cancelled
// job removes its files (mod_job) and folders (wipe_step); debris = 1 when that could not be done. B
// closes A's write window when the job ends (settle_writes).
// Before the job's first step B sweeps leftover tmp/job-* folders (js_sweep); after the last, it commits the entry into the library:
// the target id (job_target, chosen before anything is written) is never the default,
// the boot-once entry or the saved Open-XY slot; the old entry is renamed into tmp/job-<id>-old, the new one onto the target, then
// the old tree is removed. One request (or one list page and one change) per loop pass; A rescans the
// library when B closes the window (settle_begin, as after a put).
namespace mj = xyf_core::mod_job;
const U32 job_slot_bytes = 0x10000u, job_slice_ms = 20, job_ids = 32, job_say_ms = 1000;
#if defined(XYF_MTP_SESSION)
const U32 mtp_slot_bytes = 0x10000u, job_work_end = XYF_JOB_REGION_END - mtp_slot_bytes; // the MTP session's slot above
#else
const U32 job_work_end = XYF_JOB_REGION_END;
#endif
const U32 max_job_machines = 64, max_job_extensions = 8, job_link_error = 21;
const char job_firmware[] = "/yaffs2/xy-forever/firmware/", job_mods[] = "/yaffs2/xy-forever/mods/";
const char job_tmp[] = "/yaffs2/xy-forever/tmp", job_catalog[] = "/yaffs2/content/op1_factory.json";
const U32 uninstall_kind = 5;
enum JobState : U32 { job_none = 0, job_busy, job_ended };
// Removing one folder tree under tmp/ in bounded steps (tree_step), and the sweep of leftovers.
struct Tree {
    char path[file_engine::max_path];
    U32 root;
};
struct Sweep {
    Tree tree;
    U32 removing, skip, calls;
    char keep[file_engine::max_path];
};
enum JobStage : U32 { js_sweep = 0, js_assemble, js_away, js_in, js_clean, js_wipe, js_cache, js_order }; // js_wipe
struct JobSlot {
    mj::Job job;
    mj::Input input;
    mj::Files calls;
    const char* machines[max_job_machines];
    const char* extensions[max_job_extensions];
    char text[1536]; // the request's ids and name
    char base[file_engine::max_path], stock[file_engine::max_path], official[file_engine::max_path];
    char generation[file_engine::max_path], out[file_engine::max_path], sweep[file_engine::max_path];
    char line[48];
    U32 id, started_ms, ended_ms, longest_ms, link, debris, said_ms, said_phase;
    uint8_t page[read_range_max]; // the tree steps' listing
    file_client::NameScan metadata_scan;
    // The commit and the sweep.
    char target[8], entry[file_engine::max_path], old[file_engine::max_path];
    U32 stage, had_old, commit_error, commit_code, extra_steps, clean_calls, build_order;
    xyf::blob::Collect garbage;
    xyf::managed_asset::Asset cache;
    uint8_t cache_hash[64];
    U32 cache_index, cache_active;
    Tree tree; // the old entry's tree in tmp/
    Sweep leftovers;
    char state_text[xyf::store::max_text];
};
static_assert(sizeof(JobSlot) <= job_slot_bytes, "the job's state fits before its work region");
volatile U32 job_state __attribute__((section(".data"))) = job_none;
volatile U32 job_counter __attribute__((section(".data"))) = 0;
volatile U32 job_kind __attribute__((section(".data"))) =
    0; // 0 mods.apply, 1 content switch, 2 config, 3 adoption, 4 MTP session
volatile U32 job_usb __attribute__((section(".data"))) = 0; // the job was started over USB
JobSlot& slot() { return *reinterpret_cast<JobSlot*>(XYF_JOB_REGION_BEGIN); }
volatile U32 blob_collecting __attribute__((section(".data"))) = 0;
volatile U32 package_active __attribute__((section(".data"))) = 0;
volatile U32 package_file __attribute__((section(".data"))) = 0;
volatile U32 package_ms __attribute__((section(".data"))) = 0;
uint8_t package_token[32] __attribute__((section(".data"))) = {0};
volatile U32 recovery_pending __attribute__((section(".data"))) = 1;
volatile U32 journal_present __attribute__((section(".data"))) = 0;
volatile U32 reporting __attribute__((section(".data"))) = 0;
bool journal_kept() {
    if (journal_present || recovery_pending) return true;
    uint8_t record[file_client::record_bytes];
    const char* paths[2] = {"/yaffs2/xy-forever/job.txt", "/yaffs2/xy-forever/job.old"};
    for (U32 i = 0; i != 2; ++i) {
        const file_client::Reply r = file_client::stat(paths[i], record);
        if (r.outcome || r.value == 0 || r.error != file_client::enoent) return true;
    }
    return false;
}
volatile U32 menu_pack_active __attribute__((section(".data"))) = 0;
#if defined(XYF_TFW_CHECK)
volatile U32 pack_import_active __attribute__((section(".data"))) = 0;
#else
constexpr U32 pack_import_active = 0;
#endif
bool job_running() {
    return pack_import_active || menu_pack_active || job_state == job_busy || package_active || blob_collecting ||
           reporting;
}
// The file client held by a transfer: an MTP operation in flight, or a serial read stream's open file
// (usb_service_read_stream.ipp). job_blocks takes only MTP's: a stream request continues its own stream, and every other
// request ends it before it runs (home_receive).
#if defined(XYF_MTP_SESSION)
bool mtp_busy(); // an MTP operation in flight
#define XYF_OR_MTP_BUSY || mtp_busy() || read_stream_held()
#define XYF_AND_NOT_MTP_BUSY &&!mtp_busy() && !read_stream_held()
#define XYF_AND_NOT_MTP_ONLY &&!mtp_busy()
#else
#define XYF_OR_MTP_BUSY || read_stream_held()
#define XYF_AND_NOT_MTP_BUSY &&!read_stream_held()
#define XYF_AND_NOT_MTP_ONLY
#endif
void stop_idle_gc();
bool job_blocks(U32 type) { // also while settle_begin's op 9 is out
    if (blob_collecting && type != P::t_status && type != P::t_screen && type != P::t_peek && type != P::t_job_status &&
        type != P::t_job_cancel)
        stop_idle_gc();
    if (package_active &&
        (type == P::t_package_list || type == P::t_state_get || type == P::t_storage_space || type == P::t_read_file ||
         type == P::t_hash_file || type == P::t_read_stream || type == P::t_list_dir || type == P::t_delete_file ||
         type == P::t_remove_dir || type == P::t_make_dir || type == P::t_rename_file || type == P::t_write_begin ||
         type == P::t_write_data || type == P::t_write_end)) {
        package_ms = now();
        package_file = 1;
        return false;
    }
    if (recovery_pending && !boot_probe_active() && !rehearsal_active() && !job_running() &&
        !closing XYF_AND_NOT_MTP_ONLY) {
        if (type == P::t_read_file || type == P::t_hash_file || type == P::t_read_stream || type == P::t_list_dir)
            return false;
        // Only the readonly scan: preserve every write-window, held-request and original boot-path check.
        if (!writes_due && mounted != file_client::mounted_stale && !file_client::left_out() && menu_link::usb_idle() &&
            (type == P::t_reboot || type == P::t_continue || type == P::t_library_boot))
            return false;
    }
    if (!boot_probe_active() && !rehearsal_active() && !job_running() && !recovery_pending &&
        !closing XYF_AND_NOT_MTP_ONLY)
        return false;
    return type == P::t_package_list || type == P::t_state_get || type == P::t_storage_space ||
           type == P::t_read_file || type == P::t_hash_file || type == P::t_read_stream || type == P::t_put_begin ||
           type == P::t_put_data || type == P::t_put_end || type == P::t_file_write || type == P::t_list_dir ||
           type == P::t_delete_file || type == P::t_remove_dir || type == P::t_make_dir || type == P::t_rename_file ||
           type == P::t_copy_file || type == P::t_install || type == P::t_write_begin || type == P::t_write_data ||
           type == P::t_write_end || type == P::t_uninstall || type == P::t_reboot || type == P::t_continue ||
           type == P::t_library_boot || type == P::t_mods_apply || type == P::t_mtp_test || type == P::t_set_default ||
           type == P::t_tfw_check;
}
// String helpers (no library calls).
U32 text_bytes(const char* s) {
    U32 n = 0;
    while (s[n]) ++n;
    return n;
}
bool join(char* out, const char* a, const char* b, const char* c = "") {
    const char* parts[3] = {a, b, c};
    U32 n = 0;
    for (U32 k = 0; k != 3; ++k)
        for (const char* s = parts[k]; *s; ++s) {
            if (n + 1 >= file_engine::max_path) return false;
            out[n++] = *s;
        }
    out[n] = 0;
    return true;
}
U32 decimal_text(char* at, U32 v) { // no division (no libgcc)
    static const U32 tens[10] = {1000000000u, 100000000u, 10000000u, 1000000u, 100000u, 10000u, 1000u, 100u, 10u, 1u};
    U32 n = 0;
    for (U32 k = 0; k != 10; ++k) {
        char d = '0';
        while (v >= tens[k]) {
            v -= tens[k];
            ++d;
        }
        if (d != '0' || n || k == 9) at[n++] = d;
    }
    at[n] = 0;
    return n;
}
bool inside(const char* path, const char* folder) { // folder/... (folder without its '/')
    U32 i = 0;
    for (; folder[i]; ++i)
        if (path[i] != folder[i]) return false;
    return path[i] == '/' && path[i + 1];
}
bool job_read_ok(const JobSlot& s, const char* path) {
    if (!storage_server::valid_path(path)) return false;
    if (inside(path, s.generation) || inside(path, s.base) || inside(path, s.out)) return true;
    const char* catalog = s.input.catalog; // or the saved official copy
    U32 i = 0;
    while (catalog[i] && path[i] == catalog[i]) ++i;
    return !catalog[i] && !path[i];
}
bool job_write_ok(const JobSlot& s, const char* path) {
    return storage_server::valid_path(path) && inside(path, s.out) && storage_server::valid_write_path(path);
}
// One printable token: 1-31 of a-z 0-9 _ - (and '.' when dot), a letter or digit first.
bool job_token(const char* t, bool dot) {
    U32 n = 0;
    for (; t[n]; ++n) {
        const char c = t[n];
        const bool alnum = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9');
        if (n >= 31 || !(alnum || c == '_' || c == '-' || (dot && c == '.')) || (!n && !alnum)) return false;
    }
    return n != 0;
}
// A comma-separated id list (text, NUL-terminated) split in place: ids into out, false when one is not a
// library id or there are more than max (an empty text: none).
bool job_ids_split(char* text, const char** out, U32 max, U32& count) {
    count = 0;
    if (!*text) return true;
    for (char* at = text;;) {
        if (count == max) return false;
        out[count++] = at;
        while (*at && *at != ',') ++at;
        const bool more = *at == ',';
        *at = 0;
        if (!xyf::store::valid_id(out[count - 1])) return false;
        if (!more) return true;
        ++at;
    }
}
// ---- the file calls (mod_job::Files), one request each
JobSlot& of(void* c) { return *static_cast<JobSlot*>(c); }
int job_fail(JobSlot& s, const file_client::Reply& r) {
    if (r.outcome == file_client::link) mounted = file_client::mounted_stale;
    if (r.outcome != file_client::refused) s.link = 1; // nothing more is sent
    return -1;
}
int job_size(void* c, const char* path, xyf_core::U32* bytes) {
    JobSlot& s = of(c);
    if (s.link || !job_read_ok(s, path)) return -1;
    uint8_t record[file_client::record_bytes];
    const file_client::Reply r = file_client::stat(path, record); // op 0x22
    if (r.outcome) return job_fail(s, r);
    if (r.value != 0) return r.error == file_client::enoent ? mj::missing_file : -1;
    if ((P::get32(record + 8) & 0xf000u) != 0x8000u) return -1;
    *bytes = P::get32(record + 0x1c);
    return 0;
}
int job_probe_metadata(void* c, const char* path) {
    JobSlot& s = of(c);
    if (!s.metadata_scan.folder) {
        xyf_core::U32 size;
        const int result = job_size(c, path, &size);
        if (result != mj::missing_file) return result;
    }
    const U32 code = file_client::scan_name(path, s.metadata_scan, mounted);
    if (code == file_client::scan_again) return mj::probe_again;
    if (code == P::s_not_found) return mj::missing_file;
    if (code == P::s_link) s.link = 1;
    return -1; // the native name exists but stat could not read a regular file
}
int job_open(void* c, const char* path, bool write) {
    JobSlot& s = of(c);
    if (s.link || !(write ? job_write_ok(s, path) : job_read_ok(s, path))) return -1;
    const file_client::Reply r = file_client::open(path, write ? storage_server::write_open_flags : 0u); // op 0x29
    if (r.outcome) return job_fail(s, r);
    return r.value < 0 ? -1 : r.value;
}
int job_read(void* c, int handle, xyf_core::Byte* data, xyf_core::U32 bytes) {
    JobSlot& s = of(c);
    if (s.link || handle < 0) return -1;
    const U32 n = bytes < file_client::chunk ? bytes : file_client::chunk;
    if (!n) return 0;
    const file_client::Reply r = file_client::read(handle, data, n); // op 0x2a
    if (r.outcome) return job_fail(s, r);
    return r.value < 0 ? -1 : U32(r.value) > n ? int(n) : r.value;
}
int job_write(void* c, int handle, const xyf_core::Byte* data, xyf_core::U32 bytes) {
    JobSlot& s = of(c);
    if (s.link || handle < 0) return -1;
    const U32 n = bytes < file_client::chunk ? bytes : file_client::chunk;
    if (!n) return 0;
    const file_client::Reply r = file_client::write(handle, data, n); // op 0x2b
    if (r.outcome) return job_fail(s, r);
    return r.value < 0 || U32(r.value) > n ? -1 : r.value;
}
int job_close(void* c, int handle) {
    JobSlot& s = of(c);
    if (s.link || handle < 0) return -1;
    const file_client::Reply r = file_client::close(handle); // op 0x2d
    if (r.outcome) return job_fail(s, r);
    return r.value ? -1 : 0;
}
int job_rename(void* c, const char* from, const char* to) {
    JobSlot& s = of(c);
    if (s.link || !job_write_ok(s, from) || !job_write_ok(s, to)) return -1;
    uint32_t error = 0;
    const uint32_t code = file_client::rename(from, to, error, mounted); // op 0x21 twice
    if (code == P::s_link || code == P::s_state) s.link = 1;
    return code == P::s_ok ? 0 : -1;
}
int job_remove(void* c, const char* path) {
    JobSlot& s = of(c);
    if (s.link || !job_write_ok(s, path)) return -1;
    const file_client::Reply r = file_client::unlink(path); // op 0x25
    if (r.outcome) return job_fail(s, r);
    return r.value < 0 ? -1 : 0;
}
int job_mkdir(void* c, const char* path) {
    JobSlot& s = of(c);
    if (s.link || !job_write_ok(s, path)) return -1;
    const file_client::Reply r = file_client::mkdir(path); // op 0x26
    if (r.outcome) return job_fail(s, r);
    if (r.value >= 0) return 0;
    if (r.error != file_client::eexist) return -1;
    uint8_t record[file_client::record_bytes]; // there already: a folder?
    const file_client::Reply q = file_client::stat(path, record);
    if (q.outcome) return job_fail(s, q);
    return q.value == 0 && (P::get32(record + 8) & 0xf000u) == 0x4000u ? 0 : -1;
}
// ---- Bounded tree removal under tmp/, the leftover sweep, the target id and the commit
const U32 one_entry = 6 + file_client::max_name; // a file.list page of one entry
const U32 sweep_cap = 600, clean_cap = 600, wipe_cap = 1024; // calls per sweep, old tree, output
enum TreeStep : U32 { tree_more = 0, tree_done, tree_failed };
bool same_text(const char* a, const char* b) {
    while (*a && *a == *b) ++a, ++b;
    return *a == *b;
}
bool tmp_ok(const char* path) {
    return storage_server::valid_path(path) && inside(path, job_tmp) && storage_server::valid_write_path(path);
}
bool tree_begin(Tree& t, const char* folder) {
    if (!tmp_ok(folder) || !join(t.path, folder, "")) return false;
    t.root = text_bytes(t.path);
    return true;
}
// One step, depth first, the root last: a page of one entry, then its unlink, the empty folder's rmdir or
// nothing (a folder: into it). At most one change per call; only paths under tmp/.
U32 tree_change(U32 op, const char* path) {
    uint32_t error = 0;
    U32 code = enter_writes();
    if (code == P::s_ok) {
        storage_used = 1;
        code = file_client::change(op, path, error, mounted);
    }
    writes_due = 1;
    writes_ms = now();
    return code;
}
U32 tree_step(Tree& t, uint8_t* page, bool uninstall = false) {
    uint32_t count = 0, more = 0, bytes = 0;
    if (file_client::list(t.path, 0, page, one_entry, count, more, bytes, mounted) != P::s_ok) return tree_failed;
    U32 n = text_bytes(t.path);
    if (!count) {
        if (tree_change(0x27u, t.path) != P::s_ok) return tree_failed; // rmdir
        if (n == t.root) return tree_done;
        while (n > t.root && t.path[n] != '/') --n;
        t.path[n] = 0;
        return tree_more;
    }
    const U32 name = page[5];
    if (!name || n + 1 + name + 1 > file_engine::max_path) return tree_failed;
    t.path[n] = '/';
    for (U32 i = 0; i != name; ++i) t.path[n + 1 + i] = char(page[6 + i]);
    t.path[n + 1 + name] = 0;
    if (uninstall) {
        if (!inside(t.path, "/yaffs2/xy-forever") || !storage_server::valid_write_path(t.path)) return tree_failed;
    } else if (!tmp_ok(t.path)) return tree_failed;
    if (page[0] == 1) return tree_more; // a folder: into it
    const U32 code = tree_change(0x25u, t.path); // unlink
    t.path[n] = 0;
    return code == P::s_ok ? tree_more : tree_failed;
}
// The leftover sweep: tmp/'s entries one per call; a folder named job-* (not keep, the running job's output)
// is removed by tree_step, an upload's file put-* (file_engine::upload_size) unlinked. False: the sweep ended (nothing left, a failure, or sweep_cap calls).
bool sweep_step(Sweep& w, uint8_t* page) {
    if (++w.calls > sweep_cap) return false;
    if (w.removing) {
        const U32 t = tree_step(w.tree, page);
        if (t == tree_done) w.removing = 0;
        return t != tree_failed;
    }
    uint32_t count = 0, more = 0, bytes = 0;
    if (file_client::list(job_tmp, w.skip, page, one_entry, count, more, bytes, mounted) != P::s_ok || !count)
        return false;
    char path[file_engine::max_path], name[file_client::max_name + 1];
    const U32 n = page[5];
    for (U32 i = 0; i != n; ++i) name[i] = char(page[6 + i]);
    name[n] = 0;
    const bool job = page[0] == 1 && n > 4 && name[0] == 'j' && name[1] == 'o' && name[2] == 'b' && name[3] == '-';
    const bool package = page[0] == 1 && n > 4 && name[0] == 'p' && name[1] == 'k' && name[2] == 'g' && name[3] == '-';
    uint32_t size; // #4: an upload's tmp file left behind
    if (page[0] == 0 && join(path, job_tmp, "/", name) && file_engine::upload_size(path, size))
        return tree_change(0x25u, path) == P::s_ok; // unlink (uploading(): never the open one)
    if ((job || package) && join(path, job_tmp, "/", name) && !same_text(path, w.keep) && tree_begin(w.tree, path))
        w.removing = 1;
    else ++w.skip;
    return true;
}
void sweep_begin(Sweep& w, const char* keep) {
    w.removing = w.skip = w.calls = 0;
    join(w.keep, keep, "");
}
// #3 (from #24): leftover tmp/job-* folders and (#4) tmp/put-* files (a cut cable, a power cut, a cleanup that failed) are removed
// when the USB service starts, so a job's own sweep (js_sweep) finds none and its time does not depend on
// earlier boots. Once the library is ready (A serves), A is asked once (a_serving), then one sweep_step per
// menu pass while nothing else uses the files (no job, upload, window close or MTP operation); the job
// slot's sweep and page are free then. A job that starts ends it (its own sweep covers the same folders).
// Bounded as the job's sweep: sweep_cap calls, the first failure ends it.
volatile U32 gc_defer_ms __attribute__((section(".data"))) = 0;
volatile U32 boot_sweep __attribute__((section(".data"))) = 0; // 0 waiting, 1 running, 2 ended
// Background collection yields only between completed IO calls. Foreground
// jobs may then reuse the workspace; deletion was based on a complete snapshot.
void stop_idle_gc() {
    if (!blob_collecting) return;
    xyf::blob::Collect& g = slot().garbage;
    ContentIo io = {0};
    if (g.fd >= 0) {
        io.close(g.fd);
        g.fd = -1;
    }
    blob_collecting = 0;
    boot_sweep = 3;
    gc_defer_ms = now();
}
void boot_sweep_turn() {
    if (boot_sweep == 2) return;
    if (mounted == file_client::mounted_stale) {
        blob_collecting = 0;
        boot_sweep = 2;
        return;
    }
    if (blob_collecting) {
        JobSlot& s = slot();
        GarbageIo io;
        io.link = 0;
        const int done = xyf::blob::collect_step(s.garbage, io);
        if (done) {
            if (s.garbage.fd >= 0 && !io.link) io.close(s.garbage.fd);
            blob_collecting = 0;
            boot_sweep = 2;
            settle_begin();
        }
        return;
    }
    if (job_running()) {
        if (boot_sweep != 3) boot_sweep = 2;
        return;
    }
    if (closing || uploading() XYF_OR_MTP_BUSY) return;
#if defined(XYF_ONE_SESSION)
    if (!boot_sweep && xyf_boot::storage_view() != xyf::menu::storage_ready) return;
#else
    if (!boot_sweep && !read_enabled) return;
#endif
    JobSlot& s = slot();
    if (boot_sweep == 3) {
        if (journal_present) {
            boot_sweep = 2;
            return;
        }
        if (writes_due || !menu_link::usb_idle() || now() - rx_quiet_from < storage_server::window_settle_ms ||
            now() - gc_defer_ms < storage_server::window_settle_ms)
            return;
        if (!a_serving()) {
            boot_sweep = 2;
            return;
        }
        xyf::blob::collect_begin(s.garbage, reinterpret_cast<uint8_t*>(job_work_end - xyf::blob::index_bytes),
                                 xyf::blob::index_bytes);
        blob_collecting = 1;
        return;
    }
    if (!boot_sweep) {
        boot_sweep = a_serving() ? 1u : 2u;
        sweep_begin(s.leftovers, "");
        return;
    }
    if (!sweep_step(s.leftovers, s.page)) {
        boot_sweep = 3;
        gc_defer_ms = now();
    }
}
// Slot order is published with the entry's directory rename. Zero means a legacy entry without a record.
const char job_order_name[] = "/build-order.bin";
const U32 job_order_version = 1, job_order_bytes = 8;
const char* const job_ids_mods[2] = {"mods-a", "mods-b"};
U32 job_choice(JobSlot& s, xyf::state::Choice& choice) { // the state slots, as A's scan
    namespace st = xyf::state;
    for (U32 i = 0; i != sizeof(choice); ++i) reinterpret_cast<char*>(&choice)[i] = 0;
    static const char* const files[3] = {"/yaffs2/xy-forever/state-a.txt", "/yaffs2/xy-forever/state-b.txt",
                                         "/yaffs2/xy-forever/state.txt"};
    static const st::Source sources[3] = {st::source_a, st::source_b, st::source_legacy};
    for (U32 k = 0; k != 3; ++k) {
        uint32_t bytes = 0;
        const U32 code = file_client::read_file(files[k], 0, false, reinterpret_cast<uint8_t*>(s.state_text),
                                                xyf::store::max_text, bytes, mounted);
        if (code == P::s_ok) st::consider(choice, sources[k], s.state_text, bytes);
        else if (code != P::s_not_found && code != P::s_too_large) return code;
    }
    return P::s_ok;
}
#if defined(XYF_CONTENT_SWITCH)
U32 saved_mod_slot(char (&saved)[8]) {
    namespace xg = xyf::config;
    namespace json = xyf_core::json;
    struct Work {
        uint8_t raw[json::document_bytes];
        json::Workspace parser;
        char view[xg::max_text];
    };
    static_assert(sizeof(Work) <= mods_metadata_bytes, "saved slot lookup fits in the idle report slot");
    auto& work = *reinterpret_cast<Work*>(job_work_end - mods_metadata_bytes);
    config_io::Scan scan = {};
    uint32_t bytes = 0;
    bool native = false;
    U32 code = P::s_busy;
    for (U32 i = 0; i != 8 && code == P::s_busy; ++i) code = config_io::read(scan, work.raw, bytes, native, mounted);
    if (code != P::s_ok) return code == P::s_not_found ? U32(P::s_ok) : code;
    const char* text = reinterpret_cast<const char*>(work.raw);
    if (native) {
        const auto doc = json::parse(text, bytes, work.parser, json::config_schema);
        const auto result = json::config_text(doc, work.view, sizeof(work.view), bytes);
        if (result != json::ok) return result == json::version ? U32(P::s_unsupported) : U32(P::s_refused);
        text = work.view;
    } else if (!xg::supported_format(text, bytes)) return P::s_unsupported;
    xg::field(text, bytes, "slot", saved, sizeof(saved));
    return P::s_ok;
}
#endif
U32 job_target(JobSlot& s) {
    xyf::state::Choice choice;
    if (const U32 code = job_choice(s, choice)) return code;
    char saved[8] = {0}; // the saved Open-XY slot: never built over, safe mode too
#if defined(XYF_CONTENT_SWITCH)
    if (const U32 code = saved_mod_slot(saved)) return code;
#endif
    bool open[2], present[2];
    U32 order[2] = {};
    for (U32 i = 0; i != 2; ++i) {
        open[i] = !same_text(job_ids_mods[i], choice.state.default_id) &&
                  !same_text(job_ids_mods[i], choice.state.once) && !same_text(job_ids_mods[i], saved);
        char path[file_engine::max_path];
        join(path, job_firmware, job_ids_mods[i]);
        uint8_t record[file_client::record_bytes];
        const file_client::Reply r = file_client::stat(path, record);
        if (r.outcome) {
            if (r.outcome == file_client::link) mounted = file_client::mounted_stale;
            return file_client::status(r.outcome);
        }
        present[i] = r.value == 0;
        if (!present[i]) {
            if (r.error != file_client::enoent) return file_client::change_status(r.error);
            continue;
        }
        join(path, job_firmware, job_ids_mods[i], job_order_name);
        uint8_t data[job_order_bytes];
        uint32_t bytes = 0;
        const U32 code = file_client::read_file(path, 0, false, data, sizeof(data), bytes, mounted);
        if (code == P::s_not_found) continue;
        if (code != P::s_ok) return code;
        if (bytes != sizeof(data) || P::get32(data) != job_order_version) return P::s_unsupported;
        order[i] = P::get32(data + 4);
    }
    // Compare serial numbers across wrap. A missing record sorts before any recorded build.
    const bool b_newer = order[1] && (!order[0] || (order[1] - order[0] < 0x80000000u && order[1] != order[0]));
    const U32 newest = b_newer ? order[1] : order[0];
    s.build_order = newest == 0xffffffffu ? 1u : newest + 1;
    int pick = -1;
    if (open[0] && open[1]) {
        pick = !present[0] ? 0 : !present[1] ? 1 : !b_newer && order[0] != order[1] ? 1 : 0;
    } else if (open[0] || open[1]) pick = open[0] ? 0 : 1;
    if (pick < 0) return P::s_state;
    join(s.target, job_ids_mods[pick], "");
    join(s.entry, job_firmware, s.target);
    return P::s_ok;
}

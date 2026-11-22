// Part of usb_service.cpp, included there at this point: not compiled alone.
#if defined(XYF_CONTENT_SWITCH)
// ---- The stock entry's adoption (#178) as the job slot's fourth kind (job_kind 3) ---------------------
// A library with no stock entry and no default (the first start after Track 5 wrote XY Forever): the converge
// sequence (xyf_mods_io check) asks for it. The stock updater's own os.ldr (/yaffs2/upgrade/..., only read) is
// hashed first, before anything is written: it must be the exact stock 1.2.1 image (stock_os_bytes, stock_os_sha256,
// as boot.uninstall admits). Then, in A's write window: tmp/adopt-ID/ swept and made, os.ldr copied and read back
// (SHA-256 again), manifest.json and files.sha256 written and read back (as `xyf_host.py library-add` and a package
// publish write them), the folder renamed in as firmware/ID (one rename: a cut leaves only tmp/, which the next
// adoption sweeps), A's window closed (A rescans), then the default (default_commit). copy 0: the entry is there,
// only the default. The same single-job rule as every job (job_blocks answers busy); job.status reports it: phase 1
// hashing, 5 copying, 6 reading back (done/total: bytes), 7 the entry's texts, rename and default; 8 done with detail
// the id, 9 failed (error 4 the file is missing, 10 not the stock image, 17 firmware/ID is there already, else
// 16 write, 18 read back, 21 link; 8 the library unread: no catalog, or firmware/ID there but not in A's catalog, as
// after A's scan failed, code storage; 17 with code state only for an entry A listed); detail "adopt ID" while it runs
// and when it failed.
namespace xa {
const char source[] = "/yaffs2/upgrade/firmware/blackfin/bin/os.ldr";
const char root[] = "/yaffs2/xy-forever", tmp[] = "/yaffs2/xy-forever/tmp", firmware[] = "/yaffs2/xy-forever/firmware";
const char* const names[3] = {"os.ldr", "manifest.json", "files.sha256"};
}
enum AdoptStep : U32 {
    ad_find = 0,
    ad_open,
    ad_hash,
    ad_window,
    ad_sweep,
    ad_rmdir,
    ad_mkdir,
    ad_copy_open,
    ad_copy,
    ad_copy_close,
    ad_back_open,
    ad_back,
    ad_text,
    ad_rename,
    ad_closing,
    ad_ended
};
struct AdoptSlot {
    ContentIo io;
    int in, out;
    U32 id, step, at, error, code, started_ms, ended_ms, hash, crc, bytes, copy, sub, text_n, debris;
    xyf_core::hash::Sha256 sha;
    char target[32], detail[40], folder[96], path[128];
    char text[512];
    uint8_t piece[file_client::piece_max];
};
// In the content switch's work region, as the config job (one job at a time).
AdoptSlot& aslot() { return *reinterpret_cast<AdoptSlot*>(XYF_JOB_REGION_BEGIN + job_slot_bytes + content_slot_bytes); }
static_assert(sizeof(AdoptSlot) < 0x10000u, "below the save log's text");
U32 adopt_begin(const char* id, bool copy) {
    stop_idle_gc();
    if (job_running() || closing XYF_OR_MTP_BUSY || uploading()) return P::s_busy;
    if (!a_serving()) return P::s_link;
    if (!xyf::store::valid_id(id)) return P::s_bad_request;
    const U32 code = file_client::mount_ready(mounted);
    if (code != P::s_ok) return code;
    AdoptSlot& a = aslot();
    U32 n = 0;
    for (; id[n] && n + 1 != sizeof(a.target); ++n) a.target[n] = id[n];
    a.target[n] = 0;
    join(a.detail, "adopt ", a.target);
    join(a.folder, xa::tmp, "/adopt-", a.target);
    a.io.link = 0;
    a.in = a.out = -1;
    a.at = a.error = a.code = a.bytes = a.crc = a.sub = a.debris = 0;
    a.copy = copy ? 1u : 0u;
    a.step = copy ? ad_find : ad_closing;
    a.hash = xyf::state_write::id_hash(a.target);
    a.id = ++job_counter;
    a.started_ms = a.ended_ms = now();
    job_kind = 3;
    job_state = job_busy;
    job_usb = 0;
    return P::s_ok;
}
void adopt_fail(AdoptSlot& a, U32 error, U32 code) {
    a.error = a.io.lost() ? U32(xc::e_link) : error;
    a.code = a.io.lost() ? U32(P::s_link) : code;
    if (a.in >= 0) a.io.close(a.in);
    if (a.out >= 0) a.io.close(a.out);
    a.in = a.out = -1;
    a.step = ad_closing;
    settle_begin();                                                     // only when a write was due
}
// The entry's two texts (xyf_entry package metadata; files.sha256: sorted paths, two spaces) into a.text: bytes.
U32 adopt_texts(AdoptSlot& a, U32 which) {
    static const char hex[] = "0123456789abcdef";
    xg::Out o = {a.text, a.text + sizeof(a.text) - 1, true};
    char h[65];
    h[64] = 0;
    for (U32 i = 0; i != 32; ++i) {
        h[2 * i] = hex[usb_service::stock_os_sha256[i] >> 4];
        h[2 * i + 1] = hex[usb_service::stock_os_sha256[i] & 15u];
    }
    if (which == 1) {
        char c[9], b[12];
        for (U32 i = 0; i != 8; ++i) c[i] = hex[(a.crc >> (28 - 4 * i)) & 15u];
        c[8] = 0;
        decimal_text(b, usb_service::stock_os_bytes);
        xg::put(o, "{\"schema\":\"xy-forever.package\",\"schema_version\":1,"
                   "\"type\":\"firmware\",\"name\":\"OP-XY\",\"version\":\"1.2.1\",\"kind\":\"official\",\"bytes\":");
        xg::put(o, b);
        xg::put(o, ",\"crc32\":\"");
        xg::put(o, c);
        xg::put(o, "\",\"sha256\":\"");
        xg::put(o, h);
        xg::put(o, "\"}\n");
    } else {
        adopt_texts(a, 1);                                              // manifest.json's hash first
        xyf_core::hash::Sha256 s;
        xyf_core::hash::Digest d;
        if (!s.update(reinterpret_cast<const uint8_t*>(a.text), a.text_n) || !s.finish(d)) return 0;
        char i[65];
        i[64] = 0;
        for (U32 k = 0; k != 32; ++k) {
            i[2 * k] = hex[d.bytes[k] >> 4];
            i[2 * k + 1] = hex[d.bytes[k] & 15u];
        }
        o.at = a.text;
        xg::put(o, i);
        xg::put(o, "  manifest.json\n");
        xg::put(o, h);
        xg::put(o, "  os.ldr\n");
    }
    return a.text_n = o.ok ? U32(o.at - a.text) : 0u;
}
bool adopt_stock(AdoptSlot& a) {                                         // a.sha over a.bytes: the stock image?
    xyf_core::hash::Digest d;
    return a.bytes == usb_service::stock_os_bytes && a.sha.finish(d) &&
           sha_equal(d.bytes, usb_service::stock_os_sha256);
}
void adopt_step(AdoptSlot& a) {
    switch (a.step) {
    case ad_find: {                                                     // firmware/ID there already: not replaced
        const xyf::catalog::Catalog* c = xyf_boot::catalog();
        if (!c) return adopt_fail(a, xc::e_list, P::s_storage);        // A's library unread: nothing adopted
        join(a.path, xa::firmware, "/", a.target);
        uint8_t record[file_client::record_bytes];
        const file_client::Reply r = file_client::stat(a.path, record);
        if (r.outcome) {
            a.io.fail(r);
            return adopt_fail(a, xc::e_link, P::s_link);
        }
        if (r.value == 0) { // in A's catalog: an entry unusable (incomplete); else A's scan failed
            bool listed = false;
            for (U32 i = 0; !listed && i != c->count; ++i) listed = same_text(c->items[i].id, a.target);
            return listed ? adopt_fail(a, xc::e_rename, P::s_state) : adopt_fail(a, xc::e_list, P::s_storage);
        }
        a.step = ad_open;
        return;
    }
    case ad_open:
        a.in = a.io.open(xa::source, false);
        if (a.in == -2) return adopt_fail(a, xc::e_open, P::s_not_found);
        if (a.in < 0) return adopt_fail(a, xc::e_read, P::s_storage);
        a.sha.reset();
        a.bytes = a.crc = 0;
        a.step = ad_hash;
        return;
    case ad_hash:
    case ad_copy:
    case ad_back: { // one piece: hash (and copy) it
        const file_client::Reply r = file_client::read(a.in, a.piece, file_client::piece_max);
        if (r.outcome) {
            a.io.fail(r);
            return adopt_fail(a, xc::e_link, P::s_link);
        }
        if (r.value < 0 || U32(r.value) > file_client::piece_max)
            return adopt_fail(a, a.step == ad_back ? xc::e_verify : xc::e_read, P::s_storage);
        const U32 n = U32(r.value);
        if (a.step == ad_copy && n) {
            const file_client::Reply w = file_client::write(a.out, a.piece, n);
            if (w.outcome) {
                a.io.fail(w);
                return adopt_fail(a, xc::e_link, P::s_link);
            }
            if (w.value != r.value) return adopt_fail(a, xc::e_write, P::s_storage);
        }
        if (a.step != ad_copy) {
            a.sha.update(a.piece, n);
            if (a.step == ad_hash) a.crc = xyf::crc::update(a.crc, a.piece, n);
        }
        a.bytes += n;
        if (a.bytes > usb_service::stock_os_bytes)
            return adopt_fail(a, a.step == ad_back ? xc::e_verify : xc::e_sha256, P::s_sha);
        if (n) return;
        const int fd = a.in;
        a.in = -1;
        if (a.io.close(fd) < 0) return adopt_fail(a, xc::e_read, P::s_storage);
        if (a.step == ad_copy) {
            a.step = ad_copy_close;
            return;
        }
        if (!adopt_stock(a)) return adopt_fail(a, a.step == ad_back ? xc::e_verify : xc::e_sha256, P::s_sha);
        a.step = a.step == ad_hash ? ad_window : ad_text;
        a.sub = 0;
        return;
    }
    case ad_window: { // the stock image: now A's write window
        U32 code = enter_writes();
        if (code == P::s_ok) code = file_client::mount_ready(mounted);
        if (code != P::s_ok) return adopt_fail(a, code == P::s_link ? xc::e_link : xc::e_write, code);
        storage_used = 1;
        writes_due = 1;
        writes_ms = now();
        a.debris = 1;
        a.step = ad_sweep;
        a.sub = 0;
        return;
    }
    case ad_sweep: // a cut adoption's leftovers
        join(a.path, a.folder, "/", xa::names[a.sub]);
        if (a.io.unlink(a.path) < 0) return adopt_fail(a, xc::e_write, P::s_storage);
        if (++a.sub == 3) a.step = ad_rmdir;
        return;
    case ad_rmdir:
        if (a.io.rmdir(a.folder) != 0) return adopt_fail(a, xc::e_write, P::s_storage);
        a.step = ad_mkdir;
        a.sub = 0;
        return;
    case ad_mkdir: { // xy-forever/, tmp/, firmware/, tmp/adopt-ID/
        const char* folders[4] = {xa::root, xa::tmp, xa::firmware, a.folder};
        uint32_t error = 0;
        const U32 code = file_client::change(0x26u, folders[a.sub], error, mounted);
        if (code != P::s_ok && error != file_client::eexist)
            return adopt_fail(a, code == P::s_link ? xc::e_link : xc::e_write, code);
        if (++a.sub == 4) a.step = ad_copy_open;
        return;
    }
    case ad_copy_open:
        join(a.path, a.folder, "/os.ldr");
        a.in = a.io.open(xa::source, false);
        a.out = a.in >= 0 ? a.io.open(a.path, true) : -1;
        if (a.in < 0 || a.out < 0) return adopt_fail(a, a.in < 0 ? xc::e_read : xc::e_write, P::s_storage);
        a.bytes = 0;
        a.step = ad_copy;
        return;
    case ad_copy_close: {
        const int fd = a.out;
        a.out = -1;
        if (a.io.close(fd) < 0) return adopt_fail(a, xc::e_write, P::s_storage);
        a.in = a.io.open(a.path, false);
        if (a.in < 0) return adopt_fail(a, xc::e_verify, P::s_storage);
        a.sha.reset();
        a.bytes = 0;
        a.step = ad_back;
        return;
    }
    case ad_text: { // manifest.json, files.sha256: written, read back
        const U32 n = adopt_texts(a, a.sub + 1);
        join(a.path, a.folder, "/", xa::names[a.sub + 1]);
        const int fd = n ? a.io.open(a.path, true) : -1;
        const bool put = fd >= 0 && a.io.write(fd, reinterpret_cast<const uint8_t*>(a.text), n) == int(n);
        if (fd >= 0 && a.io.close(fd) < 0) return adopt_fail(a, xc::e_write, P::s_storage);
        if (!put) return adopt_fail(a, xc::e_write, P::s_storage);
        uint32_t got = 0;
        const U32 code = file_client::read_file(a.path, 0, false, a.piece, sizeof(a.piece), got, mounted);
        bool same = code == P::s_ok && got == n;
        for (U32 i = 0; same && i != n; ++i) same = a.piece[i] == uint8_t(a.text[i]);
        if (!same) return adopt_fail(a, xc::e_verify, P::s_storage);
        if (++a.sub == 2) a.step = ad_rename;
        return;
    }
    case ad_rename: { // the entry appears whole, or not at all
        join(a.path, xa::firmware, "/", a.target);
        if (a.io.rename(a.folder, a.path) < 0) return adopt_fail(a, xc::e_rename, P::s_storage);
        a.debris = 0;
        a.step = ad_closing;
        settle_begin(); // op 9: A rescans, B fetches the catalog
        return;
    }
    default: return;
    }
}
void adopt_pump() {
    AdoptSlot& a = aslot();
    const U32 start = now();
    while (a.step < ad_closing && now() - start < job_slice_ms) adopt_step(a);
    if (a.step != ad_closing || closing) return;
    if (!a.error) { // the default, only after a success
        const U32 code = default_commit(a.hash);
        if (code != P::s_ok) {
            a.error = code == P::s_link ? xc::e_link : xc::e_write;
            a.code = code;
        }
    }
    a.step = ad_ended;
    a.ended_ms = now();
    job_state = job_ended;
    boot_sweep = 3;
    gc_defer_ms = now();
}
U32 adopt_phase(const AdoptSlot& a) {
    if (a.step == ad_ended) return a.error ? U32(xc::phase_failed) : U32(xc::phase_done);
    return a.step <= ad_hash ? 1u : a.step <= ad_copy_close ? 5u : a.step <= ad_back ? 6u : 7u;
}
void adopt_view(JobView& v) {
    const AdoptSlot& a = aslot();
    v.state = job_state;
    v.id = a.id;
    v.phase = adopt_phase(a);
    v.done = a.step == ad_hash || a.step == ad_copy || a.step == ad_back ? a.bytes : 0u;
    v.total = v.phase == 1 || v.phase == 5 || v.phase == 6 ? usb_service::stock_os_bytes : 0u; // bytes, known ahead
    v.error = a.step == ad_ended ? a.error : 0u;
    v.status = a.code;
    v.debris = a.step == ad_ended ? a.debris : 0u;
    v.commit = 0;
}
// JOB_STATUS of the adoption: as the mod job's fields (see above).
void adopt_status(uint8_t seq, U32 want) {
    const AdoptSlot& a = aslot();
    uint8_t* p = files;
    const U32 code = want && want != a.id ? P::s_not_found : P::s_ok;
    P::put32(p, code);
    if (code != P::s_ok) {
        result.last_status = code;
        send(P::t_job_status, seq, p, 4);
        return;
    }
    JobView v;
    adopt_view(v);
    const bool ended = a.step == ad_ended;
    const U32 values[11] = {
        a.id, v.phase, v.done, v.total, v.error, ended ? a.code : 0u, (ended ? a.ended_ms : now()) - a.started_ms,
        0,    0,       0,      v.debris};
    for (U32 i = 0; i != 11; ++i) P::put32(p + 4 + 4 * i, values[i]);
    for (U32 i = 0; i != 32; ++i) p[48 + i] = 0;
    const char* detail = ended && !a.error ? a.target : a.detail;
    for (U32 i = 0; i != 31 && detail[i]; ++i) p[48 + i] = uint8_t(detail[i]);
    result.last_status = code;
    send(P::t_job_status, seq, p, 80);
}
#endif

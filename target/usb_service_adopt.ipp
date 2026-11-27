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
// #183, the content set's mode (adopt_content, job_kind 3 too): an official OS 1.2.1 entry without content.sha256 gets
// its content set from the live content, as `xyf_host.py library-add --content` writes it from the owner's copy. Only
// read, before anything is written: the content state must be official (no mods' content installed); /yaffs2/content/
// is listed (dlc/ and user/ aside: sound packs and the owner's), its paths sorted bytewise, every file hashed, and the
// list it makes (content.sha256's exact text) must hash to official_list_sha256: the official 1.2.1 set, nothing else.
// Then, in A's write window: firmware/ID/firmware.version written and read back, the list written to tmp/cset-ID and
// read back (its SHA-256 again), renamed in as firmware/ID/content.sha256: the list last, so it is never there without
// its version (a cut before it leaves only firmware.version, which nothing reads alone; the next build writes both
// again). Phases: 1 reading (done/total: the content's bytes, as listed), 7 writing; detail "content ID". The same
// one-job rule, slices and window as the adoption.
// #331, an OP-XY another tool changed (Open-XY): only the official paths are read (xyf_official_content.hpp; other
// files stay where they are, outside the set), and an op1_factory.json that is not the official one is put back by
// taking out the entries Open-XY inserted, accepted only when that gives the official SHA-256 (else error 22, before
// anything is written). Then, after firmware.version, the official bytes replace it through an upload's name
// (tmp/put-...), read back and renamed over the file: a cut leaves the old file or the new one, never neither, and the
// next build reads the content again.
// #334: those other files are removed too, after the catalog and before the list (cs_extras: regular files only, a
// link stays), then each folder whose every entry went, deepest first (cs_folders: never content/ itself, nor a folder
// that was empty). The light check (adopt_tidy, the first start's, after the adoption) does the same without reading
// the content: in the official state, with every official path listed, it reads only op1_factory.json (cs_catalog),
// and writes xy-forever/cleanup.txt last (cs_note): the one-time check is complete, with its counts for the install page.
// The uninstall checks again, before the stock write, with no note. Its detail is "tidy ID".
namespace xa {
const char source[] = "/yaffs2/upgrade/firmware/blackfin/bin/os.ldr";
const char root[] = "/yaffs2/xy-forever", tmp[] = "/yaffs2/xy-forever/tmp", firmware[] = "/yaffs2/xy-forever/firmware";
const char* const names[3] = {"os.ldr", "manifest.json", "files.sha256"};
const char content[] = "/yaffs2/content", state_old[] = "/yaffs2/xy-forever/content-state.old";
const char live_catalog[] = "/yaffs2/content/op1_factory.json";
// The list's bounds (docs/storage/packages.md: files.sha256's 4096 entries of at most 160 path bytes).
const U32 max_files = 4096, max_folders = 1024, max_rel = 160, pool_bytes = 0x40000u, text_bytes = 0x100000u;
const U32 line_bytes = 64 + 2 + max_rel + 1;
// #183: each file read in halves of file_client's read-ahead buffer (as the pack import, #279): one left out while
// B hashes the other.
const U32 half_bytes = file_client::stream_max;
static_assert(2 * half_bytes <= file_client::stream_capacity, "two halves");
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
    cs_entry, // #183: the content set's steps
    cs_state,
    cs_list,
    cs_open,
    cs_read,
    cs_catalog, // #334: the first start's light check reads only op1_factory.json
    cs_compare,
    cs_window,
    cs_version,
    cs_sweep,
    cs_write,
    cs_back,
    cs_rename,
    cs_extras,  // #334: another tool's files removed, then the folders they leave empty
    cs_folders,
    cs_note_window, // a clean install/upgrade: open the window for the migration record alone
    cs_note,    // #334: the first start's cleanup, for the install page
    ad_closing,
    ad_ended
};
struct AdoptSlot {
    ContentIo io;
    int in, out;
    U32 id, step, at, error, code, started_ms, ended_ms, hash, crc, bytes, copy, sub, text_n, debris, total, half,
        ahead;
    U32 removed, restored; // #334: what the cleanup did (files removed; 1: op1_factory.json put back)
    xyf_core::hash::Sha256 sha;
    char target[32], detail[40], folder[96], path[128];
    char text[512];
    uint8_t piece[file_client::piece_max];
};
// In the content switch's work region, as the config job (one job at a time).
AdoptSlot& aslot() { return *reinterpret_cast<AdoptSlot*>(XYF_JOB_REGION_BEGIN + job_slot_bytes + content_slot_bytes); }
static_assert(sizeof(AdoptSlot) < 0x10000u, "below the save log's text");
// #183: the content set's listing and list, above the save log's text (as the pack import's work: one job at a time).
struct ContentSetWork {
    U32 files, folders, pool_n, text_n, folder_at, skip, file_at;
    // #331: op1_factory.json as read (catalog_n over catalog_max: too large to keep), then the official bytes put back
    // in its place (restored_n; 0: none); changed: it is not the official one and could not be put back.
    U32 catalog_n, restored_n, changed;
    // #334: the files that are not official content (extras, removed), each with its folder's index; per folder: its
    // parent's index, its entries and how many of them were removed (all: it goes too).
    U32 extras, removed;
    uint8_t catalog[xyf::official::catalog_max];
    U32 file_ref[xa::max_files];      // pool offsets of the files' paths (relative to content/), sorted bytewise
    U32 folder_ref[xa::max_folders];  // the folders still to list, in the order found ("" the root)
    U32 extra_ref[xa::max_files], extra_in[xa::max_files];
    U32 folder_up[xa::max_folders], folder_n[xa::max_folders], folder_gone[xa::max_folders];
    xyf_core::hash::Sha256 list;      // over text, as it grows
    char pool[xa::pool_bytes];
    char text[xa::text_bytes];        // content.sha256
    uint8_t page[file_client::piece_max];
};
constexpr U32 content_set_work = XYF_JOB_REGION_BEGIN + job_slot_bytes + content_slot_bytes + 0x20000u;
static_assert(content_set_work + sizeof(ContentSetWork) <= job_work_end - mods_metadata_bytes, "below the page's work");
#if !defined(XYF_MTP_SESSION)
static_assert(content_set_work + sizeof(ContentSetWork) <= file_client::stream_buffer, "the read-ahead buffer is free");
#endif
ContentSetWork& cwork() { return *reinterpret_cast<ContentSetWork*>(content_set_work); }
// The official set's digests: in the simulator, XYF_SIM_OFFICIAL_CONTENT="<list hex> <catalog hex>" (its tests'
// synthetic content) replaces them.
#if defined(XYF_SIM)
bool hex_digest(const char* t, uint8_t* out) {
    for (U32 i = 0; i != 64; ++i) {
        const char c = t[i];
        const U32 v = c >= '0' && c <= '9' ? U32(c - '0') : c >= 'a' && c <= 'f' ? U32(c - 'a' + 10) : 16u;
        if (v == 16) return false;
        out[i / 2] = uint8_t(i & 1 ? (out[i / 2] << 4) | v : v);
    }
    return true;
}
#endif
const uint8_t* official_digest(bool catalog) {
#if defined(XYF_SIM)
    static uint8_t sim[64];
    const char* e = getenv("XYF_SIM_OFFICIAL_CONTENT");
    if (e && hex_digest(e, sim) && e[64] == ' ' && hex_digest(e + 65, sim + 32)) return catalog ? sim + 32 : sim;
#endif
    return catalog ? usb_service::official_catalog_sha256 : usb_service::official_list_sha256;
}
// #331: is rel (relative to content/) an official path? In the simulator, XYF_SIM_OFFICIAL_CONTENT's further fields
// " <engine>,<fx>,<seq> <path hash>..." (decimal counts; 8 hex digits each, sorted) replace the table and the counts.
namespace xo = xyf::official;
#if defined(XYF_SIM)
const char* sim_official() {
    const char* e = getenv("XYF_SIM_OFFICIAL_CONTENT");
    return e && text_bytes(e) > 130 && e[129] == ' ' ? e + 130 : 0;
}
#endif
// The official paths' table (n: its length).
const uint32_t* official_paths(U32& n) {
#if defined(XYF_SIM)
    static uint32_t table[256];
    if (const char* p = sim_official()) {
        while (*p && *p != ' ') ++p;
        n = 0;
        for (; *p == ' ' && n != 256; p += 9, ++n) {
            table[n] = 0;
            for (U32 i = 1; i != 9; ++i) table[n] = table[n] << 4 | U32(p[i] <= '9' ? p[i] - '0' : p[i] - 'a' + 10);
        }
        return table;
    }
#endif
    n = xo::path_count;
    return xo::path_hashes;
}
bool official_path(const char* rel) {
    U32 n = 0;
    const uint32_t* table = official_paths(n);
    return xo::listed(rel, table, n);
}
// #334: the light check cleans only content that holds every official path. In the simulator, only with its official
// facts (XYF_SIM_OFFICIAL_CONTENT): without them its content is a stand-in, never cleaned.
bool official_known() {
#if defined(XYF_SIM)
    return sim_official() != 0;
#else
    return true;
#endif
}
const xo::List* official_lists() {
#if defined(XYF_SIM)
    static xo::List lists[xo::list_count];
    if (const char* p = sim_official()) {
        for (U32 k = 0; k != xo::list_count; ++k, ++p) {
            lists[k] = xo::lists[k];
            lists[k].count = 0;
            for (; *p >= '0' && *p <= '9'; ++p) lists[k].count = lists[k].count * 10 + U32(*p - '0');
        }
        return lists;
    }
#endif
    return xo::lists;
}
// #331: w.catalog (catalog_n bytes, not the official file) put back in place: true when that is the official file
// (restored_n its length).
bool catalog_put_back(ContentSetWork& w) {
    w.restored_n = 0;
    if (w.catalog_n > xo::catalog_max) return false;
    const U32 n = xo::restore(w.catalog, w.catalog_n, official_lists(), xo::list_count);
    xyf_core::hash::Sha256 h;
    xyf_core::hash::Digest d;
    if (!n || !h.update(w.catalog, n) || !h.finish(d) || !sha_equal(d.bytes, official_digest(true))) return false;
    w.restored_n = n;
    return true;
}
// An official OS 1.2.1 entry of A's catalog (its version 1.2.1, or 1.2.1+build)? 0: no catalog.
const xyf::catalog::Item* official_121(const char* id, bool& found) {
    const xyf::catalog::Catalog* c = xyf_boot::catalog();
    found = false;
    for (U32 i = 0; c && i != c->count; ++i) {
        const xyf::catalog::Item& item = c->items[i];
        if (!same_text(item.id, id)) continue;
        found = true;
        const char* v = item.version;
        const bool version = v[0] == '1' && v[1] == '.' && v[2] == '2' && v[3] == '.' && v[4] == '1' &&
                             (!v[5] || v[5] == '+' || v[5] == ' ');
        return item.kind == xyf::store::kind_official && version ? &item : 0;
    }
    return 0;
}
// The content's listing from the start: the root ("") its first folder.
void cs_begin(AdoptSlot& a) {
    ContentSetWork& w = cwork();
    w.files = w.folders = w.pool_n = w.text_n = w.folder_at = w.skip = w.file_at = 0;
    w.catalog_n = w.restored_n = w.changed = w.extras = w.removed = 0;
    w.pool[0] = 0; // the root: ""
    w.pool_n = 1;
    w.folder_up[0] = w.folder_n[0] = w.folder_gone[0] = 0;
    w.folder_ref[w.folders++] = 0;
    w.list.reset();
    a.bytes = a.total = 0;
    a.step = cs_state;
}
U32 adopt_begin(const char* id, U32 mode) {
    stop_idle_gc();
    if (job_running() || closing XYF_OR_MTP_BUSY || uploading()) return P::s_busy;
    if (mode == usb_service::adopt_uninstall_tidy && usb_service::content_unfinished()) return P::s_busy;
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
    a.at = a.error = a.code = a.bytes = a.crc = a.sub = a.debris = a.half = a.ahead = a.removed = a.restored = 0;
    a.copy = mode;
    a.total = 0;
    a.step = mode == usb_service::adopt_copy ? ad_find : mode == usb_service::adopt_content ? cs_entry : ad_closing;
    if (mode == usb_service::adopt_content) join(a.detail, "content ", a.target);
    if (mode >= usb_service::adopt_tidy) {
        join(a.detail, "tidy ", a.target);
        cs_begin(a);
    }
    a.hash = xyf::state_write::id_hash(a.target);
    a.id = ++job_counter;
    a.started_ms = a.ended_ms = now();
    job_kind = 3;
    job_state = job_busy;
    job_usb = 0;
    return P::s_ok;
}
void adopt_fail(AdoptSlot& a, U32 error, U32 code) {
    if (a.ahead) { // the read left out: its answer first (bounded), then the file can close
        a.ahead = 0;
        if (file_client::left_poll(true).outcome) a.io.link = 1;
    }
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
// #183 helpers. Bytewise order of two NUL-ended paths (as LC_ALL=C sort).
int path_order(const char* a, const char* b) {
    for (;; ++a, ++b) {
        const uint8_t x = uint8_t(*a), y = uint8_t(*b);
        if (x != y || !x) return int(x) - int(y);
    }
}
// rel's bytes into the pool: its offset, ~0u when it does not fit.
U32 pool_put(ContentSetWork& w, const char* folder, const char* name, U32 n) {
    const U32 at = w.pool_n, f = text_bytes(folder);
    if (f + 1 + n + 1 > xa::max_rel + 1 || at + f + 1 + n + 1 > xa::pool_bytes) return ~0u;
    char* out = w.pool + at;
    for (U32 i = 0; i != f; ++i) *out++ = folder[i];
    if (f) *out++ = '/';
    for (U32 i = 0; i != n; ++i) *out++ = name[i];
    *out++ = 0;
    w.pool_n = U32(out - w.pool);
    return at;
}
// A file's path into file_ref, kept sorted (a binary search, then one move).
bool file_insert(ContentSetWork& w, U32 at) {
    if (w.files == xa::max_files) return false;
    U32 lo = 0, hi = w.files;
    while (lo < hi) {
        const U32 mid = (lo + hi) >> 1;
        if (path_order(w.pool + w.file_ref[mid], w.pool + at) < 0) lo = mid + 1;
        else hi = mid;
    }
    for (U32 i = w.files; i != lo; --i) w.file_ref[i] = w.file_ref[i - 1];
    w.file_ref[lo] = at;
    ++w.files;
    return true;
}
bool same_folded(const char* a, U32 n, const char* b) { // ASCII case folded
    for (U32 i = 0; i != n; ++i) {
        const char x = a[i] >= 'A' && a[i] <= 'Z' ? char(a[i] + 32) : a[i];
        if (!b[i] || x != b[i]) return false;
    }
    return !b[n];
}
// "/yaffs2/content" or "/yaffs2/content/<rel>" into out.
bool content_path(char* out, const char* rel) {
    return rel[0] ? join(out, xa::content, "/", rel) : join(out, xa::content, "");
}
// One page of folder folder_ref[folder_at]: its files and folders taken, the next page or folder next.
void content_list_page(AdoptSlot& a, ContentSetWork& w) {
    const char* rel = w.pool + w.folder_ref[w.folder_at];
    if (!content_path(a.path, rel)) return adopt_fail(a, xc::e_size, P::s_refused);
    uint32_t count = 0, more = 0, bytes = 0;
    const U32 code = file_client::list(a.path, w.skip, w.page, sizeof(w.page), count, more, bytes, mounted);
    if (code != P::s_ok) return adopt_fail(a, code == P::s_link ? xc::e_link : xc::e_list, code);
    for (U32 at = 0, k = 0; k != count && at + 6 <= bytes; ++k) {
        const uint8_t kind = w.page[at];
        const U32 size = P::get32(w.page + at + 1), n = w.page[at + 5];
        const char* name = reinterpret_cast<const char*>(w.page + at + 6);
        at += 6 + n;
        if (!n || (n == 1 && name[0] == '.') || (n == 2 && name[0] == '.' && name[1] == '.')) continue;
        if (!rel[0] && (same_folded(name, n, "dlc") || same_folded(name, n, "user"))) continue; // packs, the owner's
        ++w.folder_n[w.folder_at];
        const U32 ref = pool_put(w, rel, name, n);
        if (ref == ~0u) return adopt_fail(a, xc::e_size, P::s_refused);
        if (kind != 1 && !official_path(w.pool + ref)) { // #331: another tool's file, left out of the set
            if (kind == 0) {                               // #334: and removed (a link or a device stays)
                if (w.extras == xa::max_files) return adopt_fail(a, xc::e_size, P::s_refused);
                w.extra_in[w.extras] = w.folder_at;
                w.extra_ref[w.extras++] = ref;
            } else w.pool_n = ref;
            continue;
        }
        if (kind > 1) return adopt_fail(a, xc::e_sha256, P::s_refused); // a link or a device: not the official set
        if (kind == 1) {
            if (w.folders == xa::max_folders) return adopt_fail(a, xc::e_size, P::s_refused);
            w.folder_up[w.folders] = w.folder_at;
            w.folder_n[w.folders] = w.folder_gone[w.folders] = 0;
            w.folder_ref[w.folders++] = ref;
            continue;
        }
        if (!file_insert(w, ref)) return adopt_fail(a, xc::e_size, P::s_refused);
        if (a.copy < usb_service::adopt_tidy) a.total += size; // the light check reads only the catalog
    }
    if (more) {
        w.skip += count;
        return;
    }
    w.skip = 0;
    ++w.folder_at;
}
// The next half of file a.in asked into half a.half, left out (read_start); false: not sent (a.io.link: the link).
bool content_ask(AdoptSlot& a) {
    const file_client::Reply r = file_client::read_start(a.in, a.half * xa::half_bytes, xa::half_bytes);
    if (r.outcome) {
        a.io.fail(r);
        return false;
    }
    a.ahead = 1;
    return true;
}
// One line of the list ("<hex>  <path>\n") for file file_at, whose SHA-256 is d.
bool content_line(ContentSetWork& w, const xyf_core::hash::Digest& d) {
    static const char hex[] = "0123456789abcdef";
    const char* rel = w.pool + w.file_ref[w.file_at];
    const U32 n = text_bytes(rel);
    if (w.text_n + 64 + 2 + n + 1 > xa::text_bytes) return false;
    char* out = w.text + w.text_n;
    for (U32 i = 0; i != 32; ++i) {
        *out++ = hex[d.bytes[i] >> 4];
        *out++ = hex[d.bytes[i] & 15u];
    }
    *out++ = ' ';
    *out++ = ' ';
    for (U32 i = 0; i != n; ++i) *out++ = rel[i];
    *out++ = '\n';
    const U32 line = U32(out - (w.text + w.text_n));
    w.list.update(reinterpret_cast<const uint8_t*>(w.text + w.text_n), line);
    w.text_n += line;
    return true;
}
// The content set's outputs, in order (a.sub): 0 the official op1_factory.json put back (#331: only when it was), 1
// the list. Each one's bytes and the SHA-256 its read-back must have.
struct CsOut {
    const uint8_t* data;
    U32 n;
    const uint8_t* sha;
};
CsOut cs_out(const AdoptSlot& a) {
    ContentSetWork& w = cwork();
    CsOut o;
    o.data = a.sub == 0 ? w.catalog : reinterpret_cast<const uint8_t*>(w.text);
    o.n = a.sub == 0 ? w.restored_n : w.text_n;
    o.sha = official_digest(a.sub == 0);
    return o;
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
    case cs_entry: { // an official OS 1.2.1 entry without its content set
        bool found = false;
        if (!xyf_boot::catalog()) return adopt_fail(a, xc::e_list, P::s_storage);
        if (!official_121(a.target, found))
            return adopt_fail(a, found ? xc::e_info : xc::e_open, found ? P::s_refused : P::s_not_found);
        join(a.path, xa::firmware, "/", a.target);
        join(a.path, a.path, "/content.sha256");
        uint8_t record[file_client::record_bytes];
        const file_client::Reply r = file_client::stat(a.path, record);
        if (r.outcome) {
            a.io.fail(r);
            return adopt_fail(a, xc::e_link, P::s_link);
        }
        if (r.value == 0) { // it has one: nothing to do
            a.step = ad_closing;
            return;
        }
        save_lap(xl::p_cset_list);
        cs_begin(a);
        return;
    }
    case cs_state: { // the official state: no record (or state=official), no record mid-replace
        uint8_t record[file_client::record_bytes];
        const file_client::Reply r = file_client::stat(xa::state_old, record);
        if (r.outcome) {
            a.io.fail(r);
            return adopt_fail(a, xc::e_link, P::s_link);
        }
        const bool tidy = a.copy == usb_service::adopt_tidy; // #334: mods' content (or none known): nothing to clean
        if (r.value == 0) return tidy ? void(a.step = ad_closing) : adopt_fail(a, xc::e_content, P::s_refused);
        if (r.error != file_client::enoent) return adopt_fail(a, xc::e_read, P::s_storage);
        uint32_t got = 0;
        const U32 code =
            file_client::read_file(xyf::content::record_path, 0, true, a.piece, xyf::content::head_bytes, got, mounted);
        if (code == P::s_ok) {
            xyf::content::State st;
            xyf::content::parse(a.piece, got, st);
            if (st.kind != xyf::content::official)
                return tidy ? void(a.step = ad_closing) : adopt_fail(a, xc::e_content, P::s_refused);
        } else if (code != P::s_not_found) return adopt_fail(a, code == P::s_link ? xc::e_link : xc::e_read, code);
        a.step = tidy && !official_known() ? cs_note_window : cs_list;
        return;
    }
    case cs_list: { // one page of one folder
        ContentSetWork& w = cwork();
        if (w.folder_at != w.folders) return content_list_page(a, w);
        if (a.copy >= usb_service::adopt_tidy) { // #334: every official path there, or nothing is cleaned
            U32 n = 0;
            official_paths(n);
            if (w.files != n) {
                if (a.copy == usb_service::adopt_uninstall_tidy) return adopt_fail(a, xc::e_content, P::s_refused);
                a.step = cs_note_window;
                return;
            }
            a.in = a.io.open(xa::live_catalog, false);
            if (a.in < 0) return adopt_fail(a, xc::e_read, P::s_storage);
            a.step = cs_catalog;
            return;
        }
        if (!w.files) return adopt_fail(a, xc::e_sha256, P::s_refused);
        a.step = cs_open;
        return;
    }
    case cs_catalog: { // #334: op1_factory.json, one piece: the official one, or put back, or nothing is written
        ContentSetWork& w = cwork();
        const file_client::Reply r = file_client::read(a.in, a.piece, file_client::piece_max);
        if (r.outcome) {
            a.io.fail(r);
            return adopt_fail(a, xc::e_link, P::s_link);
        }
        if (r.value < 0 || U32(r.value) > file_client::piece_max) return adopt_fail(a, xc::e_read, P::s_storage);
        const U32 n = U32(r.value);
        if (n) {
            if (w.catalog_n + n <= xo::catalog_max)
                for (U32 i = 0; i != n; ++i) w.catalog[w.catalog_n++] = a.piece[i];
            else w.catalog_n = xo::catalog_max + 1;
            return;
        }
        const int fd = a.in;
        a.in = -1;
        if (a.io.close(fd) < 0) return adopt_fail(a, xc::e_read, P::s_storage);
        xyf_core::hash::Sha256 h;
        xyf_core::hash::Digest d;
        const bool official = w.catalog_n <= xo::catalog_max && h.update(w.catalog, w.catalog_n) && h.finish(d) &&
                              sha_equal(d.bytes, official_digest(true));
        if (!official && !catalog_put_back(w)) return adopt_fail(a, xc::e_catalog, P::s_refused);
        a.step = w.extras || w.restored_n ? cs_window : a.copy == usb_service::adopt_tidy ? cs_note_window : ad_closing;
        return;
    }
    case cs_open: {
        save_lap(xl::p_cset_open);
        ContentSetWork& w = cwork();
        if (w.file_at == w.files) {
            a.step = cs_compare;
            return;
        }
        join(a.path, xa::content, "/", w.pool + w.file_ref[w.file_at]);
        a.in = a.io.open(a.path, false);
        if (a.in < 0)
            return adopt_fail(a, a.in == -2 ? xc::e_open : xc::e_read, a.in == -2 ? P::s_not_found : P::s_storage);
        a.sha.reset();
        a.half = 0;
        if (!content_ask(a))
            return adopt_fail(a, a.io.link ? xc::e_link : xc::e_read, a.io.link ? P::s_link : P::s_storage);
        a.step = cs_read;
        return;
    }
    case cs_read: { // #183: one half read (A read it while B hashed the one before); the next asked, this one hashed
        save_lap(xl::p_cset_read);
        a.ahead = 0;
        const file_client::Reply r = file_client::left_poll(true);
        if (r.outcome) {
            a.io.fail(r);
            return adopt_fail(a, xc::e_link, P::s_link);
        }
        if (r.value < 0 || U32(r.value) > xa::half_bytes) return adopt_fail(a, xc::e_read, P::s_storage);
        const U32 n = U32(r.value);
        const uint8_t* data = file_client::stream(a.half * xa::half_bytes, n);
        a.half ^= 1u;
        if (n == xa::half_bytes && !content_ask(a)) // a whole half: more may follow (short only at the end)
            return adopt_fail(a, a.io.link ? xc::e_link : xc::e_read, a.io.link ? P::s_link : P::s_storage);
        save_lap(xl::p_cset_sha);
        a.sha.update(data, n);
        a.bytes += n;
        ContentSetWork& w = cwork();
        const bool catalog = same_text(w.pool + w.file_ref[w.file_at], xyf::content::catalog);
        if (catalog && w.catalog_n + n <= xo::catalog_max)        // #331: kept, to put back when it is not official
            for (U32 i = 0; i != n; ++i) w.catalog[w.catalog_n++] = data[i];
        else if (catalog) w.catalog_n = xo::catalog_max + 1;
        if (n == xa::half_bytes) return;
        save_lap(xl::p_cset_open);
        const int fd = a.in;
        a.in = -1;
        if (a.io.close(fd) < 0) return adopt_fail(a, xc::e_read, P::s_storage);
        xyf_core::hash::Digest d;
        save_lap(xl::p_cset_sha);
        if (!a.sha.finish(d)) return adopt_fail(a, xc::e_size, P::s_refused);
        if (catalog && !sha_equal(d.bytes, official_digest(true))) { // #331: put back, it is listed as the official one
            if (catalog_put_back(w))
                for (U32 i = 0; i != 32; ++i) d.bytes[i] = official_digest(true)[i];
            else w.changed = 1;
        }
        if (!content_line(w, d)) return adopt_fail(a, xc::e_size, P::s_refused);
        ++w.file_at;
        a.step = cs_open;
        return;
    }
    case cs_compare: { // the official 1.2.1 set, exactly
        xyf_core::hash::Digest d;
        if (!cwork().list.finish(d) || !sha_equal(d.bytes, official_digest(false)))
            return adopt_fail(a, cwork().changed ? xc::e_catalog : xc::e_sha256, P::s_refused);
        a.step = cs_window;
        return;
    }
    case cs_note_window:
    case cs_window: { // A's write window, tmp/ made
        save_lap(xl::p_cset_write);
        U32 code = enter_writes();
        if (code == P::s_ok) code = file_client::mount_ready(mounted);
        if (code != P::s_ok) return adopt_fail(a, code == P::s_link ? xc::e_link : xc::e_write, code);
        storage_used = 1;
        writes_due = 1;
        writes_ms = now();
        uint32_t error = 0;
        code = file_client::change(0x26u, xa::tmp, error, mounted);
        if (code != P::s_ok && error != file_client::eexist)
            return adopt_fail(a, code == P::s_link ? xc::e_link : xc::e_write, code);
        if (a.step == cs_note_window) a.step = cs_note;
        else if (a.copy < usb_service::adopt_tidy) a.step = cs_version;
        else if (cwork().restored_n) a.step = cs_sweep, a.sub = 0;
        else a.step = cs_extras, a.at = 0;
        return;
    }
    case cs_version: { // firmware/ID/firmware.version: written, read back
        const U32 n = sizeof(usb_service::official_version) - 1;
        join(a.path, xa::firmware, "/", a.target);
        join(a.path, a.path, "/firmware.version");
        const int fd = a.io.open(a.path, true);
        const bool put =
            fd >= 0 && a.io.write(fd, reinterpret_cast<const uint8_t*>(usb_service::official_version), n) == int(n);
        if (fd >= 0 && a.io.close(fd) < 0) return adopt_fail(a, xc::e_write, P::s_storage);
        if (!put) return adopt_fail(a, xc::e_write, P::s_storage);
        uint32_t got = 0;
        const U32 code = file_client::read_file(a.path, 0, false, a.piece, sizeof(a.piece), got, mounted);
        bool same = code == P::s_ok && got == n;
        for (U32 i = 0; same && i != n; ++i) same = a.piece[i] == uint8_t(usb_service::official_version[i]);
        if (!same) return adopt_fail(a, xc::e_verify, P::s_storage);
        if (cwork().restored_n) a.step = cs_sweep, a.sub = 0;
        else a.step = cs_extras, a.at = 0;
        return;
    }
    case cs_sweep: { // the next output's temporary file (a cut run's replaced), opened
        const CsOut o = cs_out(a);
        if (a.sub) join(a.folder, xa::tmp, "/cset-", a.target);
        else file_engine::upload_name(a.folder, o.n, 0x3310u);
        if (a.io.unlink(a.folder) < 0) return adopt_fail(a, xc::e_write, P::s_storage);
        a.out = a.io.open(a.folder, true);
        if (a.out < 0) return adopt_fail(a, xc::e_write, P::s_storage);
        a.at = 0;
        a.step = cs_write;
        return;
    }
    case cs_write: { // one chunk
        const CsOut o = cs_out(a);
        if (a.at != o.n) {
            const U32 n = o.n - a.at > file_client::chunk ? file_client::chunk : o.n - a.at;
            if (a.io.write(a.out, o.data + a.at, n) != int(n)) return adopt_fail(a, xc::e_write, P::s_storage);
            a.at += n;
            return;
        }
        const int fd = a.out;
        a.out = -1;
        if (a.io.close(fd) < 0) return adopt_fail(a, xc::e_write, P::s_storage);
        a.in = a.io.open(a.folder, false);
        if (a.in < 0) return adopt_fail(a, xc::e_verify, P::s_storage);
        a.sha.reset();
        a.at = 0;
        a.step = cs_back;
        return;
    }
    case cs_back: { // read back: its length and SHA-256 the expected ones
        const CsOut o = cs_out(a);
        const file_client::Reply r = file_client::read(a.in, a.piece, file_client::piece_max);
        if (r.outcome) {
            a.io.fail(r);
            return adopt_fail(a, xc::e_link, P::s_link);
        }
        if (r.value < 0 || U32(r.value) > file_client::piece_max) return adopt_fail(a, xc::e_verify, P::s_storage);
        if (r.value) {
            a.sha.update(a.piece, U32(r.value));
            a.at += U32(r.value);
            if (a.at > o.n) return adopt_fail(a, xc::e_verify, P::s_storage);
            return;
        }
        const int fd = a.in;
        a.in = -1;
        xyf_core::hash::Digest d;
        if (a.io.close(fd) < 0 || a.at != o.n || !a.sha.finish(d) || !sha_equal(d.bytes, o.sha))
            return adopt_fail(a, xc::e_verify, P::s_storage);
        a.step = cs_rename;
        return;
    }
    case cs_rename: // #331: the catalog over its file; the list last: the set complete
        if (!a.sub) {
            if (!xyf::content::allowed(xyf::content::Use::catalog, xa::live_catalog, true) ||
                a.io.rename(a.folder, xa::live_catalog, true) < 0)
                return adopt_fail(a, xc::e_rename, P::s_storage);
            a.restored = 1;
            a.step = cs_extras;
            a.at = 0;
            return;
        }
        join(a.path, xa::firmware, "/", a.target);
        join(a.path, a.path, "/content.sha256");
        if (a.io.rename(a.folder, a.path) < 0) return adopt_fail(a, xc::e_rename, P::s_storage);
        a.step = ad_closing;
        settle_begin(); // op 9: A rescans, B fetches the catalog
        return;
    case cs_extras: { // #334: one file that is not official content removed (another tool's: Open-XY's)
        ContentSetWork& w = cwork();
        if (a.at == w.extras) {
            a.at = w.folders;
            a.step = cs_folders;
            return;
        }
        if (!content_path(a.path, w.pool + w.extra_ref[a.at]) ||
            !xyf::content::allowed(xyf::content::Use::remove_file, a.path, false))
            return adopt_fail(a, xc::e_write, P::s_refused);
        if (a.io.unlink(a.path) < 0) return adopt_fail(a, xc::e_write, P::s_storage);
        ++w.folder_gone[w.extra_in[a.at]];
        ++a.removed;
        ++a.at;
        return;
    }
    case cs_folders: { // #334: a folder all of whose entries went, deepest first (never the root, nor one found empty)
        ContentSetWork& w = cwork();
        if (a.at > 1) {
            const U32 i = --a.at;
            if (!w.folder_n[i] || w.folder_gone[i] != w.folder_n[i]) return;
            if (!content_path(a.path, w.pool + w.folder_ref[i]) ||
                !xyf::content::allowed(xyf::content::Use::remove_folder, a.path, false))
                return adopt_fail(a, xc::e_write, P::s_refused);
            const int r = a.io.rmdir(a.path);
            if (r < 0) return adopt_fail(a, xc::e_write, P::s_storage);
            if (r == 0) ++w.folder_gone[w.folder_up[i]];
            return;
        }
        if (a.copy >= usb_service::adopt_tidy) {
            a.step = a.copy == usb_service::adopt_tidy ? cs_note : ad_closing;
            if (a.step == ad_closing) settle_begin();
            return;
        }
        a.sub = 1; // the list, last
        a.step = cs_sweep;
        return;
    }
    case cs_note: { // #334: what the first start's cleanup did, for the install page (xy-forever/cleanup.txt)
        char text[48] = "files=";
        U32 bytes = 6 + decimal_text(text + 6, a.removed);
        const char* tail = a.restored ? "\ncatalog=1\n" : "\ncatalog=0\n";
        for (U32 i = 0; tail[i]; ++i) text[bytes++] = tail[i];
        file_engine::upload_name(a.folder, bytes, 0x3420u);
        if (a.io.unlink(a.folder) < 0) return adopt_fail(a, xc::e_write, P::s_storage);
        const int fd = a.io.open(a.folder, true);
        const bool put = fd >= 0 && a.io.write(fd, reinterpret_cast<const uint8_t*>(text), bytes) == int(bytes);
        if (fd >= 0 && a.io.close(fd) < 0) return adopt_fail(a, xc::e_write, P::s_storage);
        if (!put) return adopt_fail(a, xc::e_write, P::s_storage);
        if (a.io.rename(a.folder, xyf::content::cleanup_note, true) < 0)
            return adopt_fail(a, xc::e_rename, P::s_storage);
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
    if (!a.error && a.copy < usb_service::adopt_content) { // the adoption's default, only after a success
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
    if (a.step >= cs_entry) return a.step <= cs_compare ? 1u : 7u;
    return a.step <= ad_hash ? 1u : a.step <= ad_copy_close ? 5u : a.step <= ad_back ? 6u : 7u;
}
void adopt_view(JobView& v) {
    const AdoptSlot& a = aslot();
    v.state = job_state;
    v.id = a.id;
    v.phase = adopt_phase(a);
    const bool content = a.step >= cs_entry && a.step <= cs_compare; // the content's bytes, as listed
    v.done = a.step == ad_hash || a.step == ad_copy || a.step == ad_back || content ? a.bytes : 0u;
    v.total = content ? a.total : v.phase == 1 || v.phase == 5 || v.phase == 6 ? usb_service::stock_os_bytes : 0u;
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

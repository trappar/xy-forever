// B owns the file session until the signed pack is published or scratch cleanup ends.
#if defined(XYF_TFW_CHECK)
namespace sp = xy_forever::sound_pack_archive;
namespace tf = xy_forever::tfw_import;
namespace zip = xyf_core::zip;
namespace ml = xy_forever::managed_blob_link;
namespace pack_hash = xyf_core::hash;
constexpr char kept_packs[] = "/yaffs2/xy-forever/packs";
constexpr char live_packs[] = "/yaffs2/content/dlc";
constexpr char inbox_packs[] = "/yaffs2/xy-forever/inbox";
enum PackPhase : U32 { pack_idle, pack_authenticating, pack_staging, pack_done, pack_failed };
enum SignedStep {
    ps_hash,
    ps_admit,
    ps_window,
    ps_extract,
    ps_blob_begin,
    ps_blobs,
    ps_record,
    ps_publish,
    ps_cleanup,
    ps_close
};
enum PackBlobStep { pb_stage, pb_seed, pb_seal, pb_copy, pb_open, pb_read, pb_sealed, pb_link };
enum PackOp : U32 { pack_status, pack_import, pack_cancel, pack_enable, pack_disable, pack_trace };
const char* const pack_record_head = "version=1\nsha256=";
volatile U32 signed_pack_phase __attribute__((section(".data"))) = pack_idle;
U32 signed_pack_done __attribute__((section(".data"))) = 0;
U32 signed_pack_total __attribute__((section(".data"))) = 0;
U32 signed_pack_error __attribute__((section(".data"))) = 0;
// #195: where a failed import stopped, for the status reply. site: the step << 8 | the call (pack_site),
// plus pack_source_close / pack_link_lost / pack_cleanup; detail: the member << 16 | A's errno or 0x8000 | an outcome.
enum PackSite : U32 {
    site_open = 1,
    site_extract = 2,
    site_flush = 3,
    site_close = 4,
    pack_source_close = 0x10000,
    pack_link_lost = 0x20000,
    pack_cleanup = 0x40000
};
U32 signed_pack_site __attribute__((section(".data"))) = 0;
U32 signed_pack_detail __attribute__((section(".data"))) = 0;
// B's log of its last requests to A (file_client::log_add), kept when an import fails: 4 words per entry.
uint32_t signed_pack_trace[4 * file_client::log_entries] __attribute__((section(".data"))) = {};
U32 signed_pack_traced __attribute__((section(".data"))) = 0;   // entries in signed_pack_trace
U32 pack_fail(U32 site, U32 detail, U32 code) {
    if (!signed_pack_site) {
        signed_pack_site = site;
        signed_pack_detail = detail;
    }
    return code;
}

void signed_pack_usb_turn();
void storage_space_product(U32 block, U32 blocks, U32& hi, U32& lo);
bool signed_pack_checkpoint(void*);
struct SignedPackJob {
    tf::Source source;
    xyf_core::Work work;
    ContentIo io;
    sp::Layout layout;
    sp::ManifestBuffer manifest;
    zip::Archive archive;
    zip::InflateWorkspace inflate;
    zip::Entry entries[4096];
    Tree tree;
    SignedStep step;
    U32 member, last_usb, pending_bytes, refs, verified_bytes;
    PackBlobStep blob_step;
    bool sealing, seal_open;
    pack_hash::Sha256 hash;
    char partial[file_engine::max_path];
    uint8_t references[4096][64];
    uint16_t ref_members[4096], ref_order[4096];
    uint8_t ref_text[4096 * (xyf::manifest::list_line + 1)];
    bool cancelled, created;
    int fd;
    char staging[file_engine::max_path], target[file_engine::max_path];
    uint8_t pending[file_client::piece_max];
    SignedPackJob()
        : work(this, signed_pack_checkpoint), io{0}, step(ps_hash), member(0), last_usb(0), pending_bytes(0), refs(0),
          verified_bytes(0), blob_step(pb_stage), sealing(false), seal_open(false), partial{}, cancelled(false),
          created(false), fd(-1) {}
};
constexpr U32 signed_pack_work = XYF_JOB_REGION_BEGIN + job_slot_bytes + content_slot_bytes + 0x20000u;
static_assert(sizeof(SignedPackJob) < job_work_end - signed_pack_work, "signed pack workspace");
SignedPackJob& signed_pack() { return *reinterpret_cast<SignedPackJob*>(signed_pack_work); }

bool signed_pack_checkpoint(void* context) {
    auto& j = *static_cast<SignedPackJob*>(context);
    tick();
    const U32 t = now();
    if (t - j.last_usb >= 20) {
        j.last_usb = t;
        signed_pack_usb_turn();
    }
    return !j.cancelled;
}

bool signed_pack_name(const char* name) {
    if (!xyf::manifest::pack_name(name)) return false;
    const U32 n = text_bytes(name);
    return n > 4 && name[n - 4] == '.' && (name[n - 3] | 32) == 't' && (name[n - 2] | 32) == 'f' &&
           (name[n - 1] | 32) == 'w';
}

// Literal directory entries, with the already checked parent. stat follows links.
int pack_kind(ContentIo& io, const char* parent, const char* wanted) {
    if (io.lost()) return -1;
    uint8_t page[1024];
    U32 skip = 0;
    while (skip < 65536u) {
        uint32_t count = 0, more = 0, bytes = 0;
        const U32 code = file_client::list(parent, skip, page, sizeof(page), count, more, bytes, mounted);
        if (code == P::s_not_found) return -2;
        if (code != P::s_ok) {
            if (code == P::s_link) io.link = 1;
            return -1;
        }
        for (U32 at = 0; at < bytes;) {
            const U32 size = page[at + 5];
            const char* name = reinterpret_cast<const char*>(page + at + 6);
            U32 i = 0;
            while (i < size && name[i] == wanted[i]) ++i;
            if (i == size && !wanted[i]) return page[at];
            at += 6 + size;
        }
        if (!more) return -2;
        if (!count) return -1;
        skip += count;
        tick();
    }
    return -1;
}

U32 signed_pack_start(const char* name) {
    if (!signed_pack_name(name)) return P::s_bad_request;
    stop_idle_gc();
    if (boot_probe_active() || rehearsal_active() || job_running() || recovery_pending || closing ||
        file_client::left_out() || uploading() XYF_OR_MTP_BUSY)
        return P::s_busy;
    if (writes_due) {
        settle_begin();
        return P::s_busy;
    }
    if (!a_serving()) return P::s_link;
    const U32 ready = file_client::mount_ready(mounted);
    if (ready) return ready;
    auto& j = *new (reinterpret_cast<void*>(signed_pack_work)) SignedPackJob;
    char path[file_engine::max_path];
    const int store = pack_kind(j.io, "/yaffs2", "xy-forever");
    if (store != 1) return store == -1 ? P::s_storage : P::s_refused;
    const int inbox = pack_kind(j.io, "/yaffs2/xy-forever", "inbox");
    if (inbox != 1) return inbox == -1 ? P::s_storage : P::s_state;
    const int kind = pack_kind(j.io, inbox_packs, name);
    if (kind == -2) return P::s_not_found;
    if (kind != 0 || !join(path, inbox_packs, "/", name)) return P::s_bad_request;
    const U32 code = j.source.begin(path);
    if (code) return code;
    signed_pack_active = 1;
    signed_pack_phase = pack_authenticating;
    signed_pack_done = signed_pack_error = signed_pack_site = signed_pack_detail = signed_pack_traced = 0;
    signed_pack_total = j.source.size();
    return P::s_ok;
}

void signed_pack_end(SignedPackJob& j, U32 code) {
    if (j.fd >= 0 && j.io.close(j.fd) && !code) code = P::s_storage;
    j.fd = -1;
    const U32 closed = j.source.close();
    if (closed) {
        mounted = file_client::mounted_stale;
        if (!code) code = closed;
    }
    signed_pack_error = code;
    if (code) signed_pack_site |= (closed ? U32(pack_source_close) : 0u) | (j.io.lost() ? U32(pack_link_lost) : 0u);
    if (code && code != P::s_cancelled) signed_pack_traced = file_client::log_copy(signed_pack_trace);
    if (j.seal_open) settle_begin();
    if (j.created && code && !closed && !j.io.lost() && tree_begin(j.tree, j.staging)) {
        signed_pack_site |= pack_cleanup;
        j.step = ps_cleanup;
        return;
    }
    if (mounted == file_client::mounted_stale && !writes_due) {
        writes_due = 1;
        settle_quiet = 1;
    }
    if (writes_due) settle_begin();
    j.step = ps_close;
}

U32 signed_pack_admit(SignedPackJob& j) {
    U32 code = j.source.open(&j.work);
    if (code) return code;
    const auto reader = j.source.reader();
    const zip::Reader zip_reader = {reader.context, reader.size, reader.read};
    auto status = zip::open(zip_reader, sp::limits(), j.entries, 4096, j.archive, &j.work);
    if (status == zip::Status::ok) status = sp::admit(j.archive, j.inflate, j.manifest, j.layout, &j.work);
    if (status != zip::Status::ok)
        return status == zip::Status::cancelled ? P::s_cancelled :
               status == zip::Status::checksum  ? P::s_crc :
                                                  P::s_image;
    const int kept = pack_kind(j.io, "/yaffs2/xy-forever", "packs");
    if ((kept != 1 && kept != -2) || !join(j.target, kept_packs, "/", j.layout.folder)) return P::s_refused;
    if (kept == 1 && pack_kind(j.io, kept_packs, j.layout.folder) != -2) return P::s_state;
    if (pack_kind(j.io, "/yaffs2", "content") != 1) return P::s_refused;
    const int live = pack_kind(j.io, "/yaffs2/content", "dlc");
    if (live != -2 && (live != 1 || pack_kind(j.io, live_packs, j.layout.folder) != -2)) return P::s_state;
    uint8_t space[file_client::space_bytes];
    const auto r = file_client::statfs("/yaffs2", space);
    if (r.outcome) return file_client::status(r.outcome);
    if (r.value) return P::s_storage;
    const U32 block = P::get32(space), blocks = P::get32(space + 8);
    if (!block) return P::s_storage;
    const U32 wanted = j.layout.bytes + j.layout.largest + (j.layout.files + 8u) * 8192u;
    U32 hi = 0, lo = 0;
    storage_space_product(block, blocks, hi, lo);
    if (!hi && lo < wanted) return P::s_too_large;
    return j.source.close(true);
}

U32 signed_pack_window(SignedPackJob& j) {
    const int tmp = pack_kind(j.io, "/yaffs2/xy-forever", "tmp");
    if (tmp != -2 && tmp != 1) return P::s_refused;
    char nonce[9];
    U32 token = now();
    for (U32 i = 0; i != 8; ++i) nonce[i] = "0123456789abcdef"[(token >> (28u - i * 4u)) & 15u];
    nonce[8] = 0;
    if (!join(j.staging, job_tmp, "/pkg-v1-", nonce)) return P::s_refused;
    if (tmp == 1 && pack_kind(j.io, job_tmp, j.staging + sizeof(job_tmp)) != -2) return P::s_state;
    U32 code = enter_writes();
    if (code) return code;
    writes_due = storage_used = 1;
    writes_ms = now();
    if ((tmp == -2 && j.io.mkdir(job_tmp) < 0) || j.io.mkdir(kept_packs) < 0) return P::s_storage;
    const auto mkdir = file_client::mkdir(j.staging);
    if (mkdir.outcome) return file_client::status(mkdir.outcome);
    if (mkdir.value < 0) return P::s_storage;
    j.created = true;
    code = j.source.open(&j.work);
    if (code) return code;
    file_client::log_note(file_client::log_mark, 1);                    // B's log: the source authenticated
    const auto reader = j.source.reader();
    j.archive.reader = {reader.context, reader.size, reader.read};
    signed_pack_phase = pack_staging;
    signed_pack_total = j.layout.files;
    signed_pack_done = 0;
    return P::s_ok;
}

bool signed_pack_flush(SignedPackJob& j) {
    if (!j.pending_bytes) return true;
    const auto wrote = file_client::write(j.fd, j.pending, j.pending_bytes);
    const U32 at = U32(ps_extract) << 8 | site_flush, member = (j.member - 1) << 16;
    if (wrote.outcome) {
        j.io.fail(wrote);
        pack_fail(at, member | 0x8000u | wrote.outcome, 0);
        return false;
    }
    if (wrote.value != int(j.pending_bytes)) {
        pack_fail(at, member | (wrote.error & 0xfffu), 0);
        return false;
    }
    j.pending_bytes = 0;
    return true;
}

bool signed_pack_sink(void* context, const uint8_t* data, U32 count) {
    auto& j = *static_cast<SignedPackJob*>(context);
    if (!j.hash.update(data, count)) return false;
    while (count) {
        const U32 room = sizeof(j.pending) - j.pending_bytes;
        const U32 n = count < room ? count : room;
        for (U32 i = 0; i != n; ++i) j.pending[j.pending_bytes++] = *data++;
        count -= n;
        if (j.pending_bytes == sizeof(j.pending) && !signed_pack_flush(j)) return false;
    }
    return true;
}

U32 signed_pack_member(SignedPackJob& j) {
    const auto& member = j.entries[j.member++];
    if (sp::same(member.name, sp::marker)) return P::s_ok;
    if (member.directory) {
        if (sp::archive_parent(member.name) || !member.name[j.layout.relative_at]) return P::s_ok;
        char folder[file_engine::max_path];
        if (!join(folder, j.staging, "/", member.name + j.layout.relative_at)) return P::s_refused;
        for (char* p = folder + text_bytes(j.staging) + 1; *p; ++p) {
            if (*p != '/') continue;
            *p = 0;
            if (j.io.mkdir(folder)) return j.io.lost() ? P::s_link : P::s_storage;
            *p = '/';
        }
        return P::s_ok;
    }
    char path[file_engine::max_path];
    if (!join(path, j.staging, "/", member.name + j.layout.relative_at)) return P::s_refused;
    uint32_t why = 0;
    const U32 opened = file_client::open_write(path, j.fd, mounted, &why);
    if (opened) return pack_fail(U32(ps_extract) << 8 | site_open, (j.member - 1) << 16 | why, opened);
    j.pending_bytes = 0;
    j.hash.reset();
    zip::Extraction result;
    const auto status = zip::extract(j.archive, j.member - 1, j.inflate, {&j, signed_pack_sink}, result, &j.work);
    const bool flushed = status == zip::Status::ok && signed_pack_flush(j);
    if (status != zip::Status::ok)
        pack_fail(U32(ps_extract) << 8 | site_extract, (j.member - 1) << 16 | U32(status), 0);
    if (j.io.close(j.fd))
        return pack_fail(U32(ps_extract) << 8 | site_close, (j.member - 1) << 16 | (j.io.lost() ? 0x8000u : 0u),
                         P::s_storage);
    j.fd = -1;
    if (!flushed)
        return status == zip::Status::cancelled ? P::s_cancelled :
               status == zip::Status::checksum  ? P::s_crc :
                                                  P::s_storage;
    pack_hash::Digest digest;
    if (!j.hash.finish(digest)) return P::s_sha;
    uint8_t* const line = j.references[j.refs];
    j.ref_members[j.refs] = uint16_t(j.member - 1);
    j.ref_order[j.refs] = uint16_t(j.refs);
    ++j.refs;
    for (U32 i = 0; i != 32; ++i) {
        line[2 * i] = "0123456789abcdef"[digest.bytes[i] >> 4];
        line[2 * i + 1] = "0123456789abcdef"[digest.bytes[i] & 15];
    }
    return P::s_ok;
}

#include "usb_service_pack_blobs.ipp"

const char* signed_pack_relative(const SignedPackJob& j, U32 ref) {
    return j.entries[j.ref_members[ref]].name + j.layout.relative_at;
}

bool pack_path_before(const char* a, const char* b) {
    while (*a && *a == *b) {
        ++a;
        ++b;
    }
    return *a < *b;
}

U32 signed_pack_refs(SignedPackJob& j) {
    // Shell sort keeps the archive table intact. References use the same path
    // ordering and line format as firmware asset lists.
    for (U32 gap = j.refs >> 1; gap; gap >>= 1) {
        for (U32 i = gap; i < j.refs; ++i) {
            const uint16_t ref = j.ref_order[i];
            const char* const path = signed_pack_relative(j, ref);
            U32 at = i;
            while (at >= gap && pack_path_before(path, signed_pack_relative(j, j.ref_order[at - gap]))) {
                j.ref_order[at] = j.ref_order[at - gap];
                at -= gap;
            }
            j.ref_order[at] = ref;
        }
    }
    U32 used = 0;
    for (U32 i = 0; i != j.refs; ++i) {
        const U32 ref = j.ref_order[i];
        for (uint8_t c : j.references[ref]) j.ref_text[used++] = c;
        j.ref_text[used++] = ' ';
        j.ref_text[used++] = ' ';
        for (const char* p = signed_pack_relative(j, ref); *p; ++p) j.ref_text[used++] = uint8_t(*p);
        j.ref_text[used++] = '\n';
    }
    return used;
}

U32 signed_pack_record(SignedPackJob& j) {
    char path[file_engine::max_path], record[96];
    if (!join(path, j.staging, "/", xyf::blob::pack_refs)) return P::s_refused;
    uint32_t written = 0, crc = 0;
    const U32 size_refs = signed_pack_refs(j);
    const U32 refs = file_client::write_file(path, j.ref_text, size_refs, xyf::crc::update(0, j.ref_text, size_refs),
                                             written, crc, mounted);
    if (refs) return refs;
    if (!join(path, j.staging, "/", sp::record_name)) return P::s_refused;
    const U32 head = text_bytes(pack_record_head);
    for (U32 i = 0; i != head; ++i) record[i] = pack_record_head[i];
    const auto& digest = j.source.identity();
    for (U32 i = 0; i != 32; ++i) {
        record[head + 2 * i] = "0123456789abcdef"[digest.bytes[i] >> 4];
        record[head + 2 * i + 1] = "0123456789abcdef"[digest.bytes[i] & 15];
    }
    record[head + 64] = '\n';
    const U32 size = head + 65;
    const auto bytes = reinterpret_cast<const uint8_t*>(record);
    return file_client::write_file(path, bytes, size, xyf::crc::update(0, bytes, size), written, crc, mounted);
}

U32 signed_pack_member_index(const SignedPackJob& j) { return j.member << 16 | U32(j.blob_step); }

void signed_pack_pump() {
    if (!signed_pack_active) return;
    auto& j = signed_pack();
    if (j.step == ps_close) {
        if (closing) return;
        if (mounted == file_client::mounted_stale && !signed_pack_error) signed_pack_error = P::s_link;
        signed_pack_phase = signed_pack_error ? pack_failed : pack_done;
        signed_pack_active = 0;
        return;
    }
    if (j.step == ps_cleanup) {
        if (closing) return;
        if (j.partial[0]) {
            tree_change(0x25u, j.partial);
            j.partial[0] = 0;
        }
        const U32 result = tree_step(j.tree, j.pending);
        if (result != tree_more) {
            settle_begin();
            j.step = ps_close;
        }
        return;
    }
    if (j.cancelled && j.step != ps_hash) {
        signed_pack_end(j, P::s_cancelled);
        return;
    }
    U32 code = P::s_ok;
    const SignedStep was = j.step;
    switch (j.step) {
    case ps_hash: {
        if (j.cancelled) j.source.cancel_hash();
        const auto phase = j.source.hash_step();
        signed_pack_done = j.source.progress();
        if (phase == tf::SourcePhase::hashing) return;
        if (phase == tf::SourcePhase::hashed) j.step = ps_admit;
        else code = j.source.error();
        break;
    }
    case ps_admit:
        code = signed_pack_admit(j);
        j.step = ps_window;
        break;
    case ps_window:
        code = signed_pack_window(j);
        j.step = ps_extract;
        break;
    case ps_extract:
        if (j.member == j.archive.count) j.step = ps_blob_begin;
        else code = signed_pack_member(j);
        break;
    case ps_blob_begin:
        code = signed_pack_begin_blobs(j);
        j.step = ps_blobs;
        break;
    case ps_blobs:
        if (j.member == j.archive.count) j.step = ps_record;
        else code = signed_pack_blob(j);
        break;
    case ps_record:
        code = signed_pack_record(j);
        j.step = ps_publish;
        break;
    case ps_publish:
        if (j.io.rename(j.staging, j.target)) code = j.io.lost() ? P::s_link : P::s_storage;
        else {
            j.created = false;
            signed_pack_end(j, P::s_ok);
            return;
        }
        break;
    default: return;
    }
    if (code) {
        pack_fail(U32(was) << 8, signed_pack_member_index(j), code);
        signed_pack_end(j, code);
    }
}

U32 signed_pack_toggle(const char* name, bool enable) {
    if (!xyf::manifest::pack_name(name)) return P::s_bad_request;
    stop_idle_gc();
    if (boot_probe_active() || rehearsal_active() || job_running() || recovery_pending || closing ||
        file_client::left_out() || uploading() XYF_OR_MTP_BUSY)
        return P::s_busy;
    if (!a_serving()) return P::s_link;
    ContentIo io = {};
    if (pack_kind(io, "/yaffs2", "xy-forever") != 1 || pack_kind(io, "/yaffs2", "content") != 1) return P::s_refused;
    const int kept = pack_kind(io, "/yaffs2/xy-forever", "packs");
    const int live = pack_kind(io, "/yaffs2/content", "dlc");
    const int from_kind = enable ? kept : live, to_kind = enable ? live : kept;
    const char* from_root = enable ? kept_packs : live_packs;
    const char* to_root = enable ? live_packs : kept_packs;
    char from[file_engine::max_path], to[file_engine::max_path], path[file_engine::max_path];
    if (from_kind != 1 || pack_kind(io, from_root, name) != 1 || !join(from, from_root, "/", name) ||
        pack_kind(io, from, sp::record_name) != 0)
        return P::s_not_found;
    if (!join(path, from, "/", sp::record_name) || !join(to, to_root, "/", name)) return P::s_refused;
    uint8_t record[96];
    uint32_t size = 0;
    const U32 read = file_client::read_file(path, 0, false, record, sizeof(record), size, mounted);
    if (read) return read;
    const U32 head = text_bytes(pack_record_head);
    if (size != head + 65 || record[head + 64] != '\n') return P::s_unsupported;
    for (U32 i = 0; i < head; ++i)
        if (record[i] != uint8_t(pack_record_head[i])) return P::s_unsupported;
    for (U32 i = head; i < head + 64; ++i)
        if (!(record[i] >= '0' && record[i] <= '9') && !(record[i] >= 'a' && record[i] <= 'f')) return P::s_unsupported;

    // Stock compatibility is pinned to an exact base and ciphertext digest after playback qualification.
    if (enable) return P::s_refused;
    if (to_kind != -2 && (to_kind != 1 || pack_kind(io, to_root, name) != -2)) return P::s_state;
    const U32 window = enter_writes();
    if (window) return window;
    writes_due = storage_used = 1;
    writes_ms = now();
    const bool ok = (to_kind != -2 || io.mkdir(to_root) == 0) && io.rename(from, to) == 0;
    settle_begin();
    return ok ? P::s_ok : io.lost() ? P::s_link : P::s_storage;
}
void packs_manage(uint8_t seq, const uint8_t* payload, U32 bytes) {
    U32 code = P::s_bad_request, op = pack_status;
    if (bytes >= 9 && bytes <= 136) {
        const U32 version = P::get32(payload);
        op = P::get32(payload + 4);
        U32 end = 8;
        while (end < bytes && payload[end]) ++end;
        const char* name = reinterpret_cast<const char*>(payload + 8);
        if (version != 1) code = P::s_unsupported;
        else if (end + 1 == bytes && op <= pack_trace) {
            if ((op == pack_status || op == pack_trace) && !*name) code = P::s_ok;
            else if (op == pack_cancel && !*name) {
                if (!signed_pack_active) code = P::s_state;
                else {
                    signed_pack().cancelled = true;
                    code = P::s_ok;
                }
            } else if (op == pack_import) code = signed_pack_start(name);
            else if (op == pack_enable || op == pack_disable) code = signed_pack_toggle(name, op == pack_enable);
        }
    }
    uint8_t reply[32 + sizeof(signed_pack_trace)];
    P::put32(reply, code);
    P::put32(reply + 4, signed_pack_phase);
    P::put32(reply + 8, signed_pack_done);
    P::put32(reply + 12, signed_pack_total);
    P::put32(reply + 16, signed_pack_error);
    P::put32(reply + 20, signed_pack_site);
    P::put32(reply + 24, signed_pack_detail);
    P::put32(reply + 28, signed_pack_traced);
    const U32 traced = op == pack_trace ? signed_pack_traced : 0;
    for (U32 i = 0; i != 4 * traced; ++i) P::put32(reply + 32 + 4 * i, signed_pack_trace[i]);
    send(P::t_packs_manage, seq, reply, code ? 4 : 32 + 16 * traced);
}
#endif

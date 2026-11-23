// B owns the file session until the pack is published or scratch cleanup ends.
#if defined(XYF_TFW_CHECK)
namespace sp = xy_forever::sound_pack_archive;
namespace tf = xy_forever::tfw_import;
namespace zip = xyf_core::zip;
namespace ml = xy_forever::managed_blob_link;
namespace pack_hash = xyf_core::hash;
constexpr char kept_packs[] = "/yaffs2/xy-forever/packs";
constexpr char live_packs[] = "/yaffs2/content/dlc";
constexpr char inbox_packs[] = "/yaffs2/xy-forever/inbox";
enum PackPhase : U32 { pack_idle, pack_opening, pack_staging, pack_done, pack_failed };
enum ImportStep {
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
enum PackBlobStep { pb_try, pb_stage, pb_clear, pb_replace, pb_publish, pb_open, pb_read, pb_link };
enum PackOp : U32 { pack_status, pack_import, pack_cancel, pack_enable, pack_disable, pack_trace, pack_timing };
const char* const pack_record_head = "version=1\nsha256=";
volatile U32 pack_import_phase __attribute__((section(".data"))) = pack_idle;
U32 pack_import_done __attribute__((section(".data"))) = 0;
U32 pack_import_total __attribute__((section(".data"))) = 0;
U32 pack_import_error __attribute__((section(".data"))) = 0;
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
U32 pack_import_site __attribute__((section(".data"))) = 0;
U32 pack_import_detail __attribute__((section(".data"))) = 0;
// B's log of its last requests to A (file_client::log_add), kept when an import fails: 4 words per entry.
uint32_t pack_import_trace[4 * file_client::log_entries] __attribute__((section(".data"))) = {};
U32 pack_import_traced __attribute__((section(".data"))) = 0;   // entries in pack_import_trace
U32 pack_fail(U32 site, U32 detail, U32 code) {
    if (!pack_import_site) {
        pack_import_site = site;
        pack_import_detail = detail;
    }
    return code;
}

void pack_import_usb_turn();
// #279: where the last import's time went on B, by the cycle counter (400 MHz). Each cycle counts in one bucket, the
// innermost one running (pack_clock); sums in units of 1024 cycles (2.56 us), operation 6 returns them. The phases:
// admit (B opens the source, reads the archive's directory, opens the window), finish (blobs, records, publish, or the cleanup). Extraction, by what B does: source (waiting
// for A's reads of the source, and for a write's answer before one), aes (decrypting, copying), inflate (decoding,
// CRC-32, stored copies), sha (SHA-256), put (sending a piece to A, waiting for the last one's answer), member (A's
// creates, closes and folders), service (the checkpoint's link tick and USB turns), loop (the menu loop between
// pump passes), other (the rest of the job's own work).
enum PackClock : U32 {
    pc_admit,
    pc_source,
    pc_aes,
    pc_inflate,
    pc_sha,
    pc_put,
    pc_member,
    pc_service,
    pc_loop,
    pc_other,
    pc_finish,
    pc_count
};
// Then the counts: source reads asked, pieces put, members written, member bytes.
const U32 pack_timing_words = 1 + pc_count + 4;
struct PackClocks {
    uint64_t spent[pc_count], last;
    U32 current, reads, puts, members, bytes;
};
PackClocks pack_clocks __attribute__((section(".data"))) = {};
U32 pack_clock(U32 bucket) {
    const uint64_t t = xyf::clock::cycles();
    pack_clocks.spent[pack_clocks.current] += t - pack_clocks.last;
    pack_clocks.last = t;
    const U32 was = pack_clocks.current;
    pack_clocks.current = bucket;
    return was;
}
void storage_space_product(U32 block, U32 blocks, U32& hi, U32& lo);
bool pack_import_checkpoint(void*);
struct PackImportJob {
    tf::Source source;
    xyf_core::Work work;
    ContentIo io;
    sp::Layout layout;
    sp::ManifestBuffer manifest;
    zip::Archive archive;
    zip::Reader plain;   // the source's decrypted reader, which archive.reader times (pack_import_read)
    zip::InflateWorkspace inflate;
    zip::Entry entries[4096];
    Tree tree;
    ImportStep step;
    U32 member, last_usb, pending_bytes, put_bytes, refs, verified_bytes;   // put_bytes: the piece left out, 0 none
    PackBlobStep blob_step;
    bool writing, published, cleared;   // writing: the member's sealed file open on A (create); published: it is the
                                        // blob; cleared: its link's scratch name removed
    pack_hash::Sha256 hash;
    char partial[file_engine::max_path];
    uint8_t references[4096][64];
    uint16_t ref_members[4096], ref_order[4096];
    uint8_t ref_text[4096 * (xyf::manifest::list_line + 1)];
    bool cancelled, created, recorded;   // created: the staging folder; recorded: the records' scratch folder
    int fd;
    char staging[file_engine::max_path], target[file_engine::max_path], records[file_engine::max_path];
    uint8_t pending[file_client::piece_max];
    PackImportJob()
        : work(this, pack_import_checkpoint), io{0}, step(ps_admit), member(0), last_usb(0), pending_bytes(0),
          put_bytes(0), refs(0), verified_bytes(0), blob_step(pb_try), writing(false), published(false), cleared(false),
          partial{}, cancelled(false), created(false), recorded(false), fd(-1) {}
};
constexpr U32 pack_import_work = XYF_JOB_REGION_BEGIN + job_slot_bytes + content_slot_bytes + 0x20000u;
static_assert(sizeof(PackImportJob) < job_work_end - pack_import_work, "pack import workspace");
#if !defined(XYF_MTP_SESSION)
static_assert(pack_import_work + sizeof(PackImportJob) <= file_client::stream_buffer, "the read-ahead buffer is free");
#endif
PackImportJob& import_job() { return *reinterpret_cast<PackImportJob*>(pack_import_work); }

bool pack_import_checkpoint(void* context) {
    auto& j = *static_cast<PackImportJob*>(context);
    const U32 was = pack_clock(pc_service);
    tick();
    const U32 t = now();
    if (t - j.last_usb >= 20) {
        j.last_usb = t;
        pack_import_usb_turn();
    }
    pack_clock(was);
    return !j.cancelled;
}

// The archive's reads: decrypting (aes), less the source's own waits for A (source).
bool pack_import_read(void* context, U32 offset, xyf_core::Byte* out, U32 count) {
    auto& j = *static_cast<PackImportJob*>(context);
    const U32 was = pack_clock(pc_aes);
    const uint64_t waited = j.source.waited();
    const bool ok = j.plain.read(j.plain.context, offset, out, count);
    pack_clock(was);
    const uint64_t source = j.source.waited() - waited;
    pack_clocks.spent[pc_aes] -= source;
    pack_clocks.spent[pc_source] += source;
    return ok;
}

bool pack_import_name(const char* name) {
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

U32 pack_import_start(const char* name) {
    if (!pack_import_name(name)) return P::s_bad_request;
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
    auto& j = *new (reinterpret_cast<void*>(pack_import_work)) PackImportJob;
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
    pack_import_active = 1;
    pack_import_phase = pack_opening;
    pack_import_done = pack_import_total = pack_import_error = pack_import_site = pack_import_detail = 0;
    pack_import_traced = 0;
    pack_clocks = PackClocks();
    pack_clocks.last = xyf::clock::cycles();
    return P::s_ok;
}

// The last piece's put, left out while B worked on the next one: its answer (the next request to A waits for it).
bool pack_import_collect(PackImportJob& j) {
    if (!j.put_bytes) return true;
    const U32 bytes = j.put_bytes;
    j.put_bytes = 0;
    const auto wrote = j.source.owner_kept() ? j.source.take_owner() : file_client::left_poll(true);
    const U32 at = U32(ps_extract) << 8 | site_flush, member = (j.member - 1) << 16;
    if (wrote.outcome) {
        j.io.fail(wrote);
        pack_fail(at, member | 0x8000u | wrote.outcome, 0);
        return false;
    }
    if (wrote.value != P::s_ok || wrote.error != bytes) {
        j.writing = false;                                                // A closed it
        pack_fail(at, member | 0x4000u | (wrote.value & 0xfffu), 0);
        return false;
    }
    return true;
}

// The member's sealed file closed on A (create's); A takes nothing else while it is open but the source's reads.
bool pack_import_close(PackImportJob& j) {
    const bool collected = pack_import_collect(j);
    if (!j.writing) return collected;
    j.writing = false;
    return !j.io.lost() && j.io.copy_blob_piece() == 1 && collected; // else op 9 closes it (close_engine)
}

void pack_import_end(PackImportJob& j, U32 code) {
    if (!pack_import_close(j) && !code) code = P::s_storage;
    if (j.fd >= 0 && j.io.close(j.fd) && !code) code = P::s_storage;
    j.fd = -1;
    const U32 closed = j.source.close();
    if (closed) {
        mounted = file_client::mounted_stale;
        if (!code) code = closed;
    }
    pack_import_error = code;
    if (code) pack_import_site |= (closed ? U32(pack_source_close) : 0u) | (j.io.lost() ? U32(pack_link_lost) : 0u);
    if (code && code != P::s_cancelled) pack_import_traced = file_client::log_copy(pack_import_trace);
    if (j.created && code && !closed && !j.io.lost() && tree_begin(j.tree, j.staging)) {
        pack_import_site |= pack_cleanup;
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

U32 pack_import_admit(PackImportJob& j) {
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

U32 pack_import_window(PackImportJob& j) {
    const int tmp = pack_kind(j.io, "/yaffs2/xy-forever", "tmp");
    if (tmp != -2 && tmp != 1) return P::s_refused;
    char nonce[9];
    U32 token = now();
    for (U32 i = 0; i != 8; ++i) nonce[i] = "0123456789abcdef"[(token >> (28u - i * 4u)) & 15u];
    nonce[8] = 0;
    if (!join(j.staging, job_tmp, "/pkg-v1-", nonce) || !join(j.records, j.staging, "-record")) return P::s_refused;
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
    file_client::log_note(file_client::log_mark, 1);                    // B's log: the source opened
    const auto reader = j.source.reader();
    j.plain = {reader.context, reader.size, reader.read};
    j.archive.reader = {&j, reader.size, pack_import_read};
    pack_import_phase = pack_staging;
    pack_import_total = j.layout.files;
    pack_import_done = 0;
    return P::s_ok;
}

// One piece to A, left out (file_client::write_start copies it): B decrypts, inflates and hashes the next piece while
// A writes this one.
bool pack_import_flush(PackImportJob& j) {
    if (!j.pending_bytes) return true;
    const U32 was = pack_clock(pc_put);
    const bool collected = pack_import_collect(j);
    j.source.settle();                                   // the source's read-ahead, if out (a failure: its next read)
    pack_clock(was);
    if (!collected) return false;
    ++pack_clocks.puts;
    const auto sent = file_client::put_blob_start(j.pending, j.pending_bytes);
    if (sent.outcome) {
        j.io.fail(sent);
        pack_fail(U32(ps_extract) << 8 | site_flush, (j.member - 1) << 16 | 0x8000u | sent.outcome, 0);
        return false;
    }
    j.put_bytes = j.pending_bytes;
    j.pending_bytes = 0;
    return true;
}

bool pack_import_sink(void* context, const uint8_t* data, U32 count) {
    auto& j = *static_cast<PackImportJob*>(context);
    const U32 was = pack_clock(pc_sha);
    const bool hashed = j.hash.update(data, count);
    pack_clock(was);
    if (!hashed) return false;
    pack_clocks.bytes += count;
    while (count) {
        const U32 room = sizeof(j.pending) - j.pending_bytes;
        const U32 n = count < room ? count : room;
        for (U32 i = 0; i != n; ++i) j.pending[j.pending_bytes++] = *data++;
        count -= n;
        if (j.pending_bytes == sizeof(j.pending) && !pack_import_flush(j)) return false;
    }
    return true;
}

U32 pack_import_member(PackImportJob& j) {
    const auto& member = j.entries[j.member++];
    if (sp::same(member.name, sp::marker)) return P::s_ok;
    if (member.directory) {
        if (sp::archive_parent(member.name) || !member.name[j.layout.relative_at]) return P::s_ok;
        char folder[file_engine::max_path];
        if (!join(folder, j.staging, "/", member.name + j.layout.relative_at)) return P::s_refused;
        const U32 was = pack_clock(pc_member);
        for (char* p = folder + text_bytes(j.staging) + 1; *p; ++p) {
            if (*p != '/') continue;
            *p = 0;
            const int made = j.io.mkdir(folder);
            if (made) {
                pack_clock(was);
                return j.io.lost() ? P::s_link : P::s_storage;
            }
            *p = '/';
        }
        pack_clock(was);
        return P::s_ok;
    }
    // The member goes straight into a sealed file (mode 0444) that B writes itself, hashing what it sends: the blob
    // phase then publishes that file as the blob, with no copy and no read-back.
    char path[file_engine::max_path];
    if (!join(path, j.staging, "/", member.name + j.layout.relative_at)) return P::s_refused;
    const U32 was = pack_clock(pc_member);
    auto opened = file_client::create_blob(path);
    if (!opened.outcome && opened.value == P::s_storage && file_client::make_folders(path, mounted) == P::s_ok)
        opened = file_client::create_blob(path); // its folders first (mkdir -p), as open_write
    pack_clock(was);
    if (opened.outcome || opened.value) {
        if (opened.outcome) j.io.fail(opened);
        const U32 why = opened.outcome ? 0x8000u | opened.outcome : 0x4000u | (opened.value & 0xfffu);
        return pack_fail(U32(ps_extract) << 8 | site_open, (j.member - 1) << 16 | why,
                         opened.outcome == file_client::link ? P::s_link :
                         opened.value == P::s_busy           ? P::s_busy :
                                                               P::s_storage);
    }
    j.writing = true;
    j.pending_bytes = 0;
    j.hash.reset();
    ++pack_clocks.members;
    zip::Extraction result;
    U32 clock = pack_clock(pc_inflate);
    const auto status = zip::extract(j.archive, j.member - 1, j.inflate, {&j, pack_import_sink}, result, &j.work);
    pack_clock(clock);
    const bool flushed = status == zip::Status::ok && pack_import_flush(j);
    if (status != zip::Status::ok)
        pack_fail(U32(ps_extract) << 8 | site_extract, (j.member - 1) << 16 | U32(status), 0);
    clock = pack_clock(pc_member);
    const bool closed = pack_import_close(j);
    pack_clock(clock);
    if (!closed)
        return pack_fail(U32(ps_extract) << 8 | site_close, (j.member - 1) << 16 | (j.io.lost() ? 0x8000u : 0u),
                         P::s_storage);
    if (!flushed) // a damaged member (its deflate stream, sizes) is the pack's fault; a failed write or read is not
        return status == zip::Status::cancelled                                                      ? P::s_cancelled :
               status == zip::Status::checksum                                                       ? P::s_crc :
               status == zip::Status::ok || status == zip::Status::sink || status == zip::Status::io ? P::s_storage :
                                                                                                       P::s_image;
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

const char* pack_import_relative(const PackImportJob& j, U32 ref) {
    return j.entries[j.ref_members[ref]].name + j.layout.relative_at;
}

bool pack_path_before(const char* a, const char* b) {
    while (*a && *a == *b) {
        ++a;
        ++b;
    }
    return *a < *b;
}

U32 pack_import_refs(PackImportJob& j) {
    // Shell sort keeps the archive table intact. References use the same path
    // ordering and line format as firmware asset lists.
    for (U32 gap = j.refs >> 1; gap; gap >>= 1) {
        for (U32 i = gap; i < j.refs; ++i) {
            const uint16_t ref = j.ref_order[i];
            const char* const path = pack_import_relative(j, ref);
            U32 at = i;
            while (at >= gap && pack_path_before(path, pack_import_relative(j, j.ref_order[at - gap]))) {
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
        for (const char* p = pack_import_relative(j, ref); *p; ++p) j.ref_text[used++] = uint8_t(*p);
        j.ref_text[used++] = '\n';
    }
    return used;
}

// The records go to their own scratch folder, published beside (never in) the pack folder: the OS reads that one.
U32 pack_import_record(PackImportJob& j) {
    char path[file_engine::max_path], record[96];
    if (j.io.mkdir(j.records)) return j.io.lost() ? P::s_link : P::s_storage;
    j.recorded = true;
    if (!join(path, j.records, "/", xyf::blob::pack_refs)) return P::s_refused;
    uint32_t written = 0, crc = 0;
    const U32 size_refs = pack_import_refs(j);
    const U32 refs = file_client::write_file(path, j.ref_text, size_refs, xyf::crc::update(0, j.ref_text, size_refs),
                                             written, crc, mounted);
    if (refs) return refs;
    if (!join(path, j.records, "/", sp::record_name)) return P::s_refused;
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

// The records first, by one rename of their folder (records left by an import cut short are moved aside into tmp/,
// which the startup sweep empties), then the pack by one rename: a cut between the two leaves records without their
// pack, which nothing lists and the next import of the pack replaces.
U32 pack_import_publish(PackImportJob& j) {
    char records[file_engine::max_path], stale[file_engine::max_path];
    if (!join(records, xyf::blob::pack_records, "/", j.layout.folder) || !join(stale, j.staging, "-stale"))
        return P::s_refused;
    const int root = pack_kind(j.io, "/yaffs2/xy-forever", "pack-records");
    if (root != 1 && root != -2) return root == -1 ? P::s_storage : P::s_refused;
    if (root == -2 && j.io.mkdir(xyf::blob::pack_records)) return P::s_storage;
    const int old = root == 1 ? pack_kind(j.io, xyf::blob::pack_records, j.layout.folder) : -2;
    if (old != 1 && old != -2) return old == -1 ? P::s_storage : P::s_state;
    if ((old == 1 && j.io.rename(records, stale)) || j.io.rename(j.records, records)) return P::s_storage;
    j.recorded = false;
    if (j.io.rename(j.staging, j.target)) return P::s_storage;
    j.created = false;
    return P::s_ok;
}

U32 pack_import_member_index(const PackImportJob& j) { return j.member << 16 | U32(j.blob_step); }

U32 pack_import_bucket(ImportStep step) {
    return step == ps_admit || step == ps_window ? pc_admit : step == ps_extract ? pc_other : pc_finish;
}
void pack_import_step(PackImportJob& j);
// One pass of the menu loop's: the job's steps, then the menu loop (loop while extracting) until the next one.
void pack_import_pump() {
    if (!pack_import_active) return;
    auto& j = import_job();
    pack_clock(pack_import_bucket(j.step));
    pack_import_step(j);
    pack_clocks.reads = j.source.reads();
    pack_clock(j.step == ps_extract ? U32(pc_loop) : pack_import_bucket(j.step));
}
// #279: the menu loop's pause is short while members are extracted (usb_service_home.ipp menu_pause_us).
bool pack_import_extracting() { return pack_import_active && pack_import_phase == pack_staging; }
void pack_import_step(PackImportJob& j) {
    if (j.step == ps_close) {
        if (closing) return;
        if (mounted == file_client::mounted_stale && !pack_import_error) pack_import_error = P::s_link;
        pack_import_phase = pack_import_error ? pack_failed : pack_done;
        pack_import_active = 0;
        return;
    }
    if (j.step == ps_cleanup) {
        if (closing) return;
        if (j.partial[0]) {
            tree_change(0x25u, j.partial);
            j.partial[0] = 0;
        }
        const U32 result = tree_step(j.tree, j.pending);
        if (result == tree_done && j.recorded && tree_begin(j.tree, j.records)) j.recorded = false; // then the records'
        else if (result != tree_more) {
            settle_begin();
            j.step = ps_close;
        }
        return;
    }
    if (j.cancelled) {
        pack_import_end(j, P::s_cancelled);
        return;
    }
    U32 code = P::s_ok;
    const ImportStep was = j.step;
    switch (j.step) {
    case ps_admit:
        code = pack_import_admit(j);
        j.step = ps_window;
        break;
    case ps_window:
        code = pack_import_window(j);
        j.step = ps_extract;
        break;
    case ps_extract: {
        // #279: members for a slice per pass, as the blobs below (a large one takes its time: its checkpoints turn
        // USB); the source's read-ahead is collected before the menu loop runs.
        const U32 start = now();
        do {
            if (j.member == j.archive.count) j.step = ps_blob_begin;
            else code = pack_import_member(j);
        } while (!code && j.step == ps_extract && now() - start < job_slice_ms && !j.cancelled);
        j.source.settle();
        break;
    }
    case ps_blob_begin:
        code = pack_import_begin_blobs(j);
        j.step = ps_blobs;
        break;
    case ps_blobs: {
        // One step is one request (a link, a rename, or a read of a blob that was there): steps for a slice per pass,
        // as the mod job does, instead of one step per menu loop pass. USB and the menu still run between slices.
        const U32 start = now();
        do {
            if (j.member == j.archive.count) j.step = ps_record;
            else code = pack_import_blob(j);
        } while (!code && j.step == ps_blobs && now() - start < job_slice_ms);
        break;
    }
    case ps_record:
        code = pack_import_record(j);
        j.step = ps_publish;
        break;
    case ps_publish:
        code = pack_import_publish(j);
        if (code == P::s_storage && j.io.lost()) code = P::s_link;
        if (!code) {
            pack_import_end(j, P::s_ok);
            return;
        }
        break;
    default: return;
    }
    if (code) {
        pack_fail(U32(was) << 8, pack_import_member_index(j), code);
        pack_import_end(j, code);
    }
}

U32 pack_import_toggle(const char* name, bool enable) {
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
    char records[file_engine::max_path];
    if (!join(records, xyf::blob::pack_records, "/", name)) return P::s_refused;
    if (from_kind != 1 || pack_kind(io, from_root, name) != 1 || !join(from, from_root, "/", name) ||
        pack_kind(io, "/yaffs2/xy-forever", "pack-records") != 1 || pack_kind(io, xyf::blob::pack_records, name) != 1 ||
        pack_kind(io, records, sp::record_name) != 0)
        return P::s_not_found;
    if (!join(path, records, "/", sp::record_name) || !join(to, to_root, "/", name)) return P::s_refused;
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

    // #195: an imported pack is enabled on any official base (untested there: the menu and packs list say so);
    // disabling it from the menu recovers.
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
        else if (end + 1 == bytes && op <= pack_timing) {
            if ((op == pack_status || op == pack_trace || op == pack_timing) && !*name) code = P::s_ok;
            else if (op == pack_cancel && !*name) {
                if (!pack_import_active) code = P::s_state;
                else {
                    import_job().cancelled = true;
                    code = P::s_ok;
                }
            } else if (op == pack_import) code = pack_import_start(name);
            else if (op == pack_enable || op == pack_disable) code = pack_import_toggle(name, op == pack_enable);
        }
    }
    static_assert(4 * pack_timing_words <= sizeof(pack_import_trace), "the timing fits the reply");
    uint8_t reply[32 + sizeof(pack_import_trace)];
    P::put32(reply, code);
    P::put32(reply + 4, pack_import_phase);
    P::put32(reply + 8, pack_import_done);
    P::put32(reply + 12, pack_import_total);
    P::put32(reply + 16, pack_import_error);
    P::put32(reply + 20, pack_import_site);
    P::put32(reply + 24, pack_import_detail);
    P::put32(reply + 28, pack_import_traced);
    const U32 traced = op == pack_trace ? pack_import_traced : 0;
    for (U32 i = 0; i != 4 * traced; ++i) P::put32(reply + 32 + 4 * i, pack_import_trace[i]);
    U32 extra = 16 * traced;
    if (op == pack_timing && code == P::s_ok) { // #279: the count, the buckets' units, then the counts
        U32 words[pack_timing_words] = {pc_count};
        for (U32 i = 0; i != pc_count; ++i) words[1 + i] = U32(pack_clocks.spent[i] >> 10);
        words[1 + pc_count] = pack_clocks.reads;
        words[2 + pc_count] = pack_clocks.puts;
        words[3 + pc_count] = pack_clocks.members;
        words[4 + pc_count] = pack_clocks.bytes;
        for (U32 i = 0; i != pack_timing_words; ++i) P::put32(reply + 32 + 4 * i, words[i]);
        extra = 4 * pack_timing_words;
    }
    send(P::t_packs_manage, seq, reply, code ? 4 : 32 + extra);
}
#endif

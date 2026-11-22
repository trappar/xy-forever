#ifndef XYF_CONTENT_JOB_HPP
#define XYF_CONTENT_JOB_HPP
#include "xyf_content.hpp"
#include "xyf_store.hpp"
#include "xyf_blob.hpp"
#include "xyf_package_record.hpp"
#include "../native/hash.hpp"
// The content switch (docs/storage/state.md "Content state"), run by processor B as a job: makes
// /yaffs2/content/ match a target library entry. step() sends at most one file request per call (B's menu
// loop calls it in slices; usb_service.cpp's ContentIo is one bounded file-client request per call). Io:
//   int open(const char* path, bool write)       fd >= 0; -2 no such file; else -1 (write: create/truncate)
//   int read(int fd, uint8_t* out, uint32_t n)    bytes (n <= chunk), 0 at the end, < 0 failed
//   int write(int fd, const uint8_t* in, uint32_t n)
//   int close(int fd)                             0 ok
//   int unlink(const char* path)                  0 removed, 1 not there, < 0 failed
//   int rmdir(const char* path)                   0 removed or not there, 1 not empty, < 0 failed
//   int mkdir(const char* path)                   0 made or already a folder, < 0 failed
//   int rename(const char* from, const char* to)  0 ok, < 0 failed (A's engine never replaces a file at `to`)
//   bool lost()                                   processor A stopped answering: nothing more is sent
// Steps: the target (info.txt: an assembled mod set has `generation`; else the official content), the record;
// from the official state: the live catalog's SHA-256 must be the op1_factory.json line of a stock entry's
// content.sha256 (candidates); else the saved official list is loaded. The target's content/ lines of
// files.sha256 (each path through `allowed`; one the official list names is refused). Every refusal comes
// before the first write. Then: from the official state, that list and the catalog saved under
// content-official/ (unless the same copies are there); the record "switching" (its files: the target's and
// the previous ones); the previous files the target does not carry removed (and folders left empty); the
// target's files copied (.part, read back, renamed over) unless the live file is the target's already (see
// "by difference", below); the catalog last (a stock target: the saved official one); the record for the
// target (installed: each file= line carries the SHA-256 it was written with).
// By difference: a target file is skipped without a read when the record is trusted (state=installed, read
// as is, its file= lines sorted, and the target is not the entry it names: switching to the installed entry
// again is the repair, which reads everything) and its line carries the target's SHA-256. Otherwise the live
// file is read and hashed: equal skips the write; missing or different writes it. A switching record (a cut,
// a failure) is never trusted, so the repair reads every file and converges.
namespace xyf { namespace content {
static const uint32_t chunk = 1024, max_files = 4096, max_candidates = 16;
static const uint32_t text_capacity = 0x100000u;        // each list, the record and the plan
static const uint32_t work_needed = 4 * text_capacity + 3 * max_files * 12 + 0x100000u;
enum Error : uint32_t {                                  // job.status `error` values (as native/mod_job.hpp)
    e_none = 0,
    e_path = 2,
    e_open = 4,
    e_read = 5,
    e_size = 7,
    e_list = 8,
    e_info = 9,
    e_sha256 = 10,
    e_content = 15,
    e_write = 16,
    e_rename = 17,
    e_verify = 18,
    e_link = 21
};
enum Refusal : uint32_t {                                // job.status `code` with e_content
    r_not_official = 1,   // the live catalog is not a stock entry's official one
    r_official_file = 2,  // the target would replace an official file
    r_no_saved = 3,       // not the official state, and no saved official list
    r_no_catalog = 4      // the target lists no content/op1_factory.json
};
enum Stage : uint32_t {
    st_info = 0,
    st_record,
    st_live,
    st_candidate,
    st_load_saved,
    st_target,
    st_saved_list,
    st_saved_catalog,
    st_save,
    st_blob,
    st_mark,
    st_remove,
    st_copy,
    st_catalog,
    st_final,
    st_done,
    st_failed
};
enum Phase : uint32_t { phase_inputs = 1, phase_content = 4, phase_finish = 7, phase_done = 8, phase_failed = 9 };
struct Ref {
    uint32_t path, bytes, hash;
};
struct Text {
    uint8_t* data;
    uint32_t capacity, length;
};
struct Job {
    char target[32];
    char candidates[max_candidates][32];
    uint32_t candidate_count, candidate;
    bool mods;                                           // the target is an assembled mod set
    bool record_on_disk;                                 // the record file exists (store renames it to .old)
    bool sorted, trusted;                                // the record's file= lines sorted; trusted (above)
    uint32_t stage, leg, sub, error, code, done, total, steps, peak;
    uint32_t compared, read, written, removed, bytes;    // timing log: target files compared, live files read,
                                                         // files written (catalog too), removed; bytes written
    char detail[32];
    State state;                                         // the record's head as the job found it
    blob::Copy cache;
    Text official, list, record, plan, file;
    Ref* official_refs;
    Ref* list_refs;
    Ref* record_refs;
    uint32_t official_count, list_count, record_count;
    uint32_t official_catalog;                           // the catalog line's hash in official.data, ~0: none
    uint8_t target_catalog[64];                          // hex SHA-256 of the catalog the target installs
    uint8_t live[32];                                    // SHA-256 of the live catalog (official state)
    int fd;
    uint32_t offset, index, cursor;
    xyf_core::hash::Sha256 sha;
    xyf_core::hash::Digest digest;
    uint8_t buffer[chunk];
    char path[max_path], part[max_path], key[max_path], folder[max_path];
};
static const char firmware[] = "/yaffs2/xy-forever/firmware/";
enum Io_ : uint32_t { x_more = 0, x_done, x_missing, x_failed };

static inline uint32_t phase(const Job& j) {
    return j.stage == st_done   ? phase_done :
           j.stage == st_failed ? phase_failed :
           j.stage == st_final  ? phase_finish :
           j.stage >= st_mark   ? phase_content :
                                  phase_inputs;
}
// a + b[0..bn) + c into out (max_path); false when it does not fit.
static inline bool join(char* out, const char* a, const uint8_t* b = 0, uint32_t bn = 0, const char* c = "") {
    uint32_t n = 0;
    for (; *a; ++a) {
        if (n + 1 >= max_path) return false;
        out[n++] = *a;
    }
    for (uint32_t i = 0; i != bn; ++i) {
        if (n + 1 >= max_path || !b[i]) return false;
        out[n++] = char(b[i]);
    }
    for (; *c; ++c) {
        if (n + 1 >= max_path) return false;
        out[n++] = *c;
    }
    out[n] = 0;
    return true;
}
static inline const uint8_t* bytes(const char* s) { return reinterpret_cast<const uint8_t*>(s); }
static inline uint32_t length(const char* s) {
    uint32_t n = 0;
    while (s[n]) ++n;
    return n;
}
static inline void note(Job& j, const char* text) {             // the detail: the text's last 31 bytes
    uint32_t n = length(text), k = 0;
    for (const char* t = n > 31 ? text + n - 31 : text; *t; ++t) j.detail[k++] = *t;
    j.detail[k] = 0;
}
static inline void fail(Job& j, uint32_t error, uint32_t code, const char* why) {
    j.error = error;
    j.code = code;
    j.stage = st_failed;
    note(j, why);
}
#include "xyf_content_io.hpp"

// ---- the lists
static inline int compare_ref(const Text& t, const Ref& r, const uint8_t* s, uint32_t n) {
    return order(t.data + r.path, r.bytes, s, n);
}
// Index of the first ref >= s (refs sorted).
static inline uint32_t lower(const Text& t, const Ref* refs, uint32_t count, const uint8_t* s, uint32_t n) {
    uint32_t lo = 0, hi = count;
    while (lo != hi) {
        const uint32_t mid = lo + (hi - lo) / 2;
        if (compare_ref(t, refs[mid], s, n) < 0) lo = mid + 1;
        else hi = mid;
    }
    return lo;
}
static inline bool listed(const Text& t, const Ref* refs, uint32_t count, const uint8_t* s, uint32_t n) {
    const uint32_t i = lower(t, refs, count, s, n);
    return i != count && !compare_ref(t, refs[i], s, n);
}
static inline bool official_file(Job& j, const uint8_t* s, uint32_t n) {
    return listed(j.official, j.official_refs, j.official_count, s, n);
}
// A folder (s, without its '/') of some official path.
static inline bool official_folder(Job& j, const uint8_t* s, uint32_t n) {
    if (n + 2 > max_path) return true;
    for (uint32_t i = 0; i != n; ++i) j.key[i] = char(s[i]);
    j.key[n] = '/';
    const uint32_t i = lower(j.official, j.official_refs, j.official_count, bytes(j.key), n + 1);
    if (i == j.official_count || j.official_refs[i].bytes <= n + 1) return false;
    return !order(j.official.data + j.official_refs[i].path, n + 1, bytes(j.key), n + 1);
}
// An official list (content.sha256) into official_refs: sorted, every line well formed; the catalog's line.
static inline bool index_official(Job& j) {
    j.official_count = 0;
    j.official_catalog = ~0u;
    Line l;
    bool bad = false;
    for (uint32_t at = 0; line(j.official.data, j.official.length, at, l, bad); at = l.next) {
        if (j.official_count == max_files) return false;
        Ref& r = j.official_refs[j.official_count];
        r.path = l.path;
        r.bytes = l.bytes;
        r.hash = l.hash;
        if (j.official_count &&
            compare_ref(j.official, j.official_refs[j.official_count - 1], j.official.data + l.path, l.bytes) >= 0)
            return false;
        if (!order(j.official.data + l.path, l.bytes, bytes(catalog), length(catalog))) j.official_catalog = l.hash;
        ++j.official_count;
    }
    return !bad;
}
// The record's file= lines (any order; `file=<path>` or `file=<path>\t<64 hex>`, the SHA-256 written); a path
// longer than max_rel is never one XY Forever wrote: skipped. hash 0: none. sorted: strictly increasing.
static inline bool index_record(Job& j) {
    j.record_count = 0;
    j.sorted = true;
    const uint8_t* t = j.record.data;
    for (uint32_t at = 0; at < j.record.length;) {
        uint32_t e = at;
        while (e < j.record.length && t[e] != '\n') ++e;
        uint32_t k = 0, n = e - at - 5, hash = 0;
        if (starts(t + at, e - at, "file=", k)) {
            if (n > 65 && t[e - 65] == '\t') {
                bool hex = true;
                for (uint32_t i = e - 64; hex && i != e; ++i) hex = hex_digit(t[i]);
                if (hex) {
                    hash = e - 64;
                    n -= 65;
                }
            }
        }
        if (k == 5 && n && n <= max_rel) {
            if (j.record_count == max_files) return false;
            if (j.record_count && compare_ref(j.record, j.record_refs[j.record_count - 1], t + at + 5, n) >= 0)
                j.sorted = false;
            Ref& r = j.record_refs[j.record_count++];
            r.path = at + 5;
            r.bytes = n;
            r.hash = hash;
        }
        at = e + 1;
    }
    return true;
}
// Both the path and its .part pass `allowed` (official: the path is an official file).
static inline bool rule(Job& j, Use use, const uint8_t* rel, uint32_t n, bool official) {
    return join(j.key, root, rel, n) && allowed(use, j.key, official) && join(j.key, root, rel, n, ".part") &&
           allowed(use, j.key, official);
}
// The target's files.sha256: its content/ lines (sorted), the catalog's hash. Fails the job on a refusal.
static inline bool index_target(Job& j) {
    j.list_count = 0;
    bool catalog_seen = false, bad = false;
    Line l;
    for (uint32_t at = 0; line(j.list.data, j.list.length, at, l, bad); at = l.next) {
        uint32_t k = 0;
        const uint8_t* p = j.list.data + l.path;
        if (!starts(p, l.bytes, "content/", k) || l.bytes == 8) continue;
        const uint8_t* rel = p + 8;
        const uint32_t n = l.bytes - 8;
        if (!order(rel, n, bytes(catalog), length(catalog))) {
            for (uint32_t i = 0; i != 64; ++i) j.target_catalog[i] = j.list.data[l.hash + i];
            catalog_seen = true;
            continue;
        }
        join(j.key, "", rel, n);
        if (official_file(j, rel, n)) {
            fail(j, e_content, r_official_file, j.key);
            return false;
        }
        if (!rule(j, Use::install, rel, n, false)) {
            join(j.key, "", rel, n);
            fail(j, e_path, 0, j.key);
            return false;
        }
        if (j.list_count == max_files) {
            fail(j, e_size, 0, "files.sha256");
            return false;
        }
        if (j.list_count && compare_ref(j.list, j.list_refs[j.list_count - 1], rel, n) >= 0) {
            fail(j, e_list, 0, "files.sha256 order");
            return false;
        }
        Ref& r = j.list_refs[j.list_count++];
        r.path = l.path + 8;
        r.bytes = n;
        r.hash = l.hash;
    }
    if (bad) {
        fail(j, e_list, 0, "files.sha256");
        return false;
    }
    if (!catalog_seen) {
        fail(j, e_content, r_no_catalog, "no content/op1_factory.json");
        return false;
    }
    return true;
}
// A record path the switch removes: not one the target carries, not official, through the rule.
static inline bool removable(Job& j, uint32_t i) {
    const Ref& r = j.record_refs[i];
    const uint8_t* rel = j.record.data + r.path;
    return !listed(j.list, j.list_refs, j.list_count, rel, r.bytes) && !official_file(j, rel, r.bytes) &&
           rule(j, Use::remove_file, rel, r.bytes, false);
}
static inline bool put(Text& t, const uint8_t* s, uint32_t n) {
    if (t.capacity - t.length < n) return false;
    for (uint32_t i = 0; i != n; ++i) t.data[t.length++] = s[i];
    return true;
}
static inline bool put(Text& t, const char* s) { return put(t, bytes(s), length(s)); }
static inline bool put_file(Text& t, const uint8_t* rel, uint32_t n) {
    return put(t, "file=") && put(t, rel, n) && put(t, "\n");
}
// The record to write: switching (the target's files and the ones still to remove) or the target's.
static inline bool plan(Job& j, bool switching_) {
    Text& t = j.plan;
    t.length = 0;
    bool ok = switching_ ? put(t, "state=switching\ntarget=") && put(t, j.target) && put(t, "\n") :
              j.mods     ? put(t, "state=installed\nentry=") && put(t, j.target) && put(t, "\n") :
                           put(t, "state=official\n");
    for (uint32_t i = 0; ok && i != j.list_count; ++i) {
        const Ref& f = j.list_refs[i];
        ok = put(t, "file=") && put(t, j.list.data + f.path, f.bytes) &&
             (switching_ || (put(t, "\t") && put(t, j.list.data + f.hash, 64))) && put(t, "\n");
    }
    for (uint32_t i = 0; ok && switching_ && i != j.record_count; ++i)
        if (removable(j, i)) ok = put_file(t, j.record.data + j.record_refs[i].path, j.record_refs[i].bytes);
    if (!ok) fail(j, e_size, 0, "record");
    return ok;
}

// The job, on a work region of at least work_needed bytes. False: a bad target id or too little memory.
static inline bool start(Job& j, const char* target, const char* const* candidates, uint32_t count, uint8_t* work,
                         uint32_t work_bytes) {
    for (uint32_t i = 0; i != sizeof(Job); ++i) reinterpret_cast<uint8_t*>(&j)[i] = 0;
    j.sha.reset();
    j.fd = -1;
    j.official_catalog = ~0u;
    j.stage = st_info;
    if (!target || !store::valid_id(target) || !work || work_bytes < work_needed) return false;
    join(j.target, target);
    for (uint32_t i = 0; i != count && j.candidate_count != max_candidates; ++i)
        if (candidates[i] && store::valid_id(candidates[i])) join(j.candidates[j.candidate_count++], candidates[i]);
    Text* texts[4] = {&j.official, &j.list, &j.record, &j.plan};
    for (uint32_t i = 0; i != 4; ++i) {
        texts[i]->data = work + i * text_capacity;
        texts[i]->capacity = text_capacity;
    }
    j.official_refs = reinterpret_cast<Ref*>(work + 4 * text_capacity);
    j.list_refs = j.official_refs + max_files;
    j.record_refs = j.list_refs + max_files;
    const uint32_t used = 4 * text_capacity + 3 * max_files * uint32_t(sizeof(Ref));
    j.file.data = work + used;
    j.file.capacity = work_bytes - used;
    note(j, "start");
    return true;
}

template<class Io> static uint32_t step(Job& j, Io& io) {
    if (j.stage >= st_done) return phase(j);
    ++j.steps;
    char* const p = j.path;
    uint32_t r = x_more;
    switch (j.stage) {
    case st_info: {                                      // the target: an assembled mod set?
        if (!join(p, firmware, bytes(j.target), length(j.target), j.leg ? "/info.txt" : "/manifest.json")) {
            fail(j, e_path, 0, j.target);
            break;
        }
        r = load(j, io, p, j.plan, j.leg ? store::max_text : xyf_core::json::document_bytes);
        if (r == x_more || r == x_failed) break;
        if (r == x_missing && !j.leg) {
            j.leg = 1;
            break;
        }
        if (r == x_missing) {
            fail(j, e_open, 0, p);
            break;
        }
        store::Info info;
        for (uint32_t i = 0; i != sizeof(info); ++i) reinterpret_cast<uint8_t*>(&info)[i] = 0;
        if (j.leg) store::parse_info(reinterpret_cast<const char*>(j.plan.data), j.plan.length, info);
        else {
            // list is not populated until st_target. This job owns it; no GC/report
            // workspace is borrowed, and the raw source/digest stay untouched.
            package_record::Workspace& work = *reinterpret_cast<package_record::Workspace*>(j.list.data);
            const xyf_core::json::Status status =
                package_record::parse(reinterpret_cast<const char*>(j.plan.data), j.plan.length, work);
            if (status != xyf_core::json::ok || work.manifest.status != manifest::ok ||
                work.manifest.type != manifest::type_firmware) {
                fail(j, e_info, status ? 0x100u + status : work.manifest.status, p);
                break;
            }
            manifest::legacy_info(work.manifest, info);
        }
        j.mods = info.has_generation;
        j.leg = 0;
        j.stage = st_record;
        note(j, "record");
        break;
    }
    case st_record:                                      // what content/ matches now, and what XY Forever installed
        r = load(j, io, j.leg ? record_old : record_path, j.record, j.record.capacity);
        if (r == x_more || r == x_failed) break;
        if (r == x_missing && !j.leg) {
            j.leg = 1;
            break;
        }   // no record: its .old (a cut record write)?
        if (r == x_missing) {
            j.record.length = 0;
            j.state.kind = official;
            j.state.entry[0] = 0;
        } else parse(j.record.data, j.record.length, j.state);
        if (r == x_done && j.leg) j.state.kind = switching;     // only the .old: needs repair
        j.record_on_disk = r == x_done && !j.leg;
        j.leg = 0;
        if (!index_record(j)) {
            fail(j, e_size, 0, record_path);
            break;
        }
        j.trusted = j.record_on_disk && j.state.kind == installed && j.sorted && !same(j.state.entry, j.target);
        j.stage = j.state.kind == official ? st_live : st_load_saved;
        note(j, "official");
        break;
    case st_live:                                        // the official state: the live catalog's SHA-256
        if (!join(p, root, bytes(catalog), length(catalog))) {
            fail(j, e_path, 0, catalog);
            break;
        }
        r = load(j, io, p, j.file, j.file.capacity);
        if (r == x_more || r == x_failed) break;
        if (r == x_missing) {
            fail(j, e_content, r_not_official, "no op1_factory.json");
            break;
        }
        for (uint32_t i = 0; i != 32; ++i) j.live[i] = j.digest.bytes[i];
        j.candidate = 0;
        j.stage = st_candidate;
        break;
    case st_candidate:                                   // ... is the catalog line of a stock entry's list
        if (j.candidate == j.candidate_count) {
            fail(j, e_content, r_not_official, "content not official");
            break;
        }
        if (!join(p, firmware, bytes(j.candidates[j.candidate]), length(j.candidates[j.candidate]),
                  "/content.sha256")) {
            ++j.candidate;
            break;
        }
        r = load(j, io, p, j.official, j.official.capacity);
        if (r == x_more || r == x_failed) break;
        if (r == x_done && index_official(j) && j.official_catalog != ~0u &&
            hash_equal(j.official.data + j.official_catalog, j.live)) {
            j.stage = st_target;
            note(j, "files.sha256");
        } else ++j.candidate;
        break;
    case st_saved_list:                                  // the same copies saved already? (then nothing to write)
        r = load(j, io, saved_list, j.plan, j.plan.capacity);
        if (r == x_more || r == x_failed) break;
        j.leg = 0;
        j.stage = st_save;
        if (r == x_done && j.plan.length == j.official.length) {
            uint32_t d = 0;
            for (uint32_t i = 0; i != j.plan.length; ++i) d |= j.plan.data[i] ^ j.official.data[i];
            if (!d) j.stage = st_saved_catalog;
        }
        break;
    case st_saved_catalog:
        r = load(j, io, saved_catalog, j.plan, j.plan.capacity);
        if (r == x_more || r == x_failed) break;
        j.leg = 0;
        j.stage = r == x_done && hash_equal(j.official.data + j.official_catalog, j.digest.bytes) ? st_blob : st_save;
        break;
    case st_save:                                        // before the first change: the official list and catalog
        if (j.leg == 0) {
            if (!allowed(Use::saved, saved_folder, false)) {
                fail(j, e_path, 0, saved_folder);
                break;
            }
            if (io.mkdir(saved_folder) < 0) {
                fail(j, io.lost() ? e_link : e_write, 0, saved_folder);
                break;
            }
            j.leg = 1;
            break;
        }
        if (j.leg == 1) {
            if (!allowed(Use::saved, saved_list, false)) {
                fail(j, e_path, 0, saved_list);
                break;
            }
            if (store(j, io, saved_list, j.official.data, j.official.length) == x_done) j.leg = 2;
            break;
        }
        if (!allowed(Use::saved, saved_catalog, false)) {
            fail(j, e_path, 0, saved_catalog);
            break;
        }
        if (store(j, io, saved_catalog, j.file.data, j.file.length) == x_done) {
            j.stage = st_blob;
            j.leg = 0;
        }
        break;
    case st_blob: {                                     // retain the outgoing official catalog before it disappears
        if (!j.leg) {
            static const char folder[] = "/yaffs2/xy-forever/tmp";
            char temporary[max_path];
            blob::scratch_name(temporary, j.file.length);
            if (!allowed(Use::blob, folder, false) || !allowed(Use::blob, temporary, false) || io.mkdir(folder) < 0 ||
                !blob::begin(j.cache, saved_catalog, temporary, j.live)) {
                fail(j, io.lost() ? e_link : e_write, 0, folder);
                break;
            }
            j.leg = 1;
            break;
        }
        const int copied = blob::step(j.cache, io);
        if (copied < 0) {
            blob::abandon(j.cache, io);
            fail(j, io.lost() ? e_link : e_verify, 0, "catalog blob");
        } else if (copied) {
            j.stage = st_mark;
            j.leg = 0;
        }
        break;
    }
    case st_load_saved:                                  // not the official state: the list saved before
        r = load(j, io, saved_list, j.official, j.official.capacity);
        if (r == x_more || r == x_failed) break;
        if (r == x_missing || !index_official(j) || j.official_catalog == ~0u) {
            fail(j, e_content, r_no_saved, "no saved official list");
            break;
        }
        j.stage = st_target;
        note(j, "files.sha256");
        break;
    case st_target:                                      // what the target installs (refusals before any change)
        if (j.mods) {
            if (!join(p, firmware, bytes(j.target), length(j.target), "/files.sha256")) {
                fail(j, e_path, 0, j.target);
                break;
            }
            r = load(j, io, p, j.list, j.list.capacity);
            if (r == x_more || r == x_failed) break;
            if (r == x_missing) {
                fail(j, e_open, 0, p);
                break;
            }
            if (!index_target(j)) break;
        } else {
            j.list_count = 0;
            j.list.length = 0;
            for (uint32_t i = 0; i != 64; ++i) j.target_catalog[i] = j.official.data[j.official_catalog + i];
        }
        j.total = j.list_count + 1;
        for (uint32_t i = 0; i != j.record_count; ++i)
            if (removable(j, i)) ++j.total;
        j.leg = 0;
        j.stage = j.state.kind == official ? st_saved_list : st_mark;    // the official copies saved first
        note(j, "switching");
        break;
    case st_mark:                                        // the record says "switching" from here on
        if (j.leg == 0) {
            if (plan(j, true)) j.leg = 1;
            break;
        }
        if (!allowed(Use::record, record_path, false)) {
            fail(j, e_path, 0, record_path);
            break;
        }
        if (store(j, io, record_path, j.plan.data, j.plan.length) == x_done) {
            j.stage = st_remove;
            j.index = 0;
            j.leg = 0;
        }
        break;
    case st_remove: {                                    // the previous files the target does not carry
        while (j.index != j.record_count && j.leg == 0 && !removable(j, j.index)) ++j.index;
        if (j.index == j.record_count) {
            j.stage = st_copy;
            j.index = 0;
            j.leg = 0;
            j.cursor = 0;
            j.folder[0] = 0;
            break;
        }
        const Ref& f = j.record_refs[j.index];
        const uint8_t* rel = j.record.data + f.path;
        if (j.leg < 2) {
            join(p, root, rel, f.bytes, j.leg ? ".part" : "");
            if (j.leg == 0) note(j, p);
            if (!allowed(Use::remove_file, p, false)) {
                fail(j, e_path, 0, p);
                break;
            }
            const int u = io.unlink(p);
            if (u < 0) {
                fail(j, io.lost() ? e_link : e_write, 0, p);
                break;
            }
            if (u == 0 && j.leg == 0) ++j.removed;
            if (++j.leg == 2) {
                j.cursor = f.bytes;
                while (j.cursor && rel[j.cursor - 1] != '/') --j.cursor;
                if (j.cursor) --j.cursor;
            }
            break;
        }
        // leg 2: the folders left empty, deepest first, never an official one
        bool next = !j.cursor || official_folder(j, rel, j.cursor) || !join(p, root, rel, j.cursor) ||
                    !allowed(Use::remove_folder, p, false);
        if (!next) {
            const int d = io.rmdir(p);
            if (d < 0) {
                fail(j, io.lost() ? e_link : e_write, 0, p);
                break;
            }   // a repeat retries it
            if (d == 0) {
                while (j.cursor && rel[j.cursor - 1] != '/') --j.cursor;
                if (j.cursor) --j.cursor;
            } else next = true;
        }
        if (next || !j.cursor) {
            ++j.done;
            ++j.index;
            j.leg = 0;
        }
        break;
    }
    case st_copy: {                                      // the target's files
        if (j.index == j.list_count) {
            j.stage = st_catalog;
            j.leg = 0;
            note(j, catalog);
            break;
        }
        const Ref& f = j.list_refs[j.index];
        const uint8_t* rel = j.list.data + f.path;
        if (j.leg == 0) {                                // the trusted record's SHA-256 is the target's?
            ++j.compared;
            j.leg = 1;
            if (j.trusted) {
                const uint32_t at = lower(j.record, j.record_refs, j.record_count, rel, f.bytes);
                const Ref* had = at != j.record_count && !compare_ref(j.record, j.record_refs[at], rel, f.bytes) ?
                                     j.record_refs + at :
                                     0;
                uint32_t d = had && had->hash ? 0u : 1u;
                for (uint32_t i = 0; !d && i != 64; ++i) d |= j.record.data[had->hash + i] ^ j.list.data[f.hash + i];
                if (!d) {
                    ++j.index;
                    ++j.done;
                    j.leg = 0;
                }
            }
            break;
        }
        if (j.leg == 1) {                                // else the live file: the target's already? (no write)
            join(p, root, rel, f.bytes);
            r = load(j, io, p, j.file, j.file.capacity);
            if (r == x_more || r == x_failed) break;
            if (r == x_done) ++j.read;
            if (r == x_done && hash_equal(j.list.data + f.hash, j.digest.bytes)) {
                ++j.index;
                ++j.done;
                j.leg = 0;
            } else j.leg = 2;
            break;
        }
        if (j.leg == 2) {                                // its folders, shallowest first
            uint32_t k = j.cursor;
            while (k < f.bytes && rel[k] != '/') ++k;
            if (k >= f.bytes) {
                j.leg = 3;
                j.cursor = 0;
                break;
            }
            j.cursor = k + 1;
            bool made = true;                            // a prefix of the last folder made
            for (uint32_t i = 0; made && i != k; ++i) made = j.folder[i] == char(rel[i]);
            if (made && (j.folder[k] == '/' || !j.folder[k])) break;      // (an official folder too: it may be gone)
            join(p, root, rel, k);
            if (!allowed(Use::make_folder, p, false)) {
                fail(j, e_path, 0, p);
                break;
            }
            if (io.mkdir(p) < 0) {
                fail(j, io.lost() ? e_link : e_write, 0, p);
                break;
            }
            join(j.folder, "", rel, k);
            break;
        }
        if (j.leg == 3) {                                // the entry's copy, its SHA-256 as listed
            if (!join(p, firmware, bytes(j.target), length(j.target), "/content/") ||
                !join(p + length(p), "", rel, f.bytes)) {
                fail(j, e_path, 0, j.target);
                break;
            }
            note(j, p);
            r = load(j, io, p, j.file, j.file.capacity);
            if (r == x_more || r == x_failed) break;
            if (r == x_missing) {
                fail(j, e_open, 0, p);
                break;
            }
            if (!hash_equal(j.list.data + f.hash, j.digest.bytes)) {
                fail(j, e_sha256, 0, p);
                break;
            }
            j.leg = 4;
            break;
        }
        join(p, root, rel, f.bytes);
        if (!allowed(Use::install, p, false)) {
            fail(j, e_path, 0, p);
            break;
        }
        if (store(j, io, p, j.file.data, j.file.length) == x_done) {
            ++j.index;
            ++j.done;
            ++j.written;
            j.bytes += j.file.length;
            j.leg = 0;
            j.cursor = 0;
        }
        break;
    }
    case st_catalog:                                     // the catalog last (a stock target: the saved official one)
        if (j.leg == 0) {
            const char* source = saved_catalog;
            if (j.mods) {
                if (!join(p, firmware, bytes(j.target), length(j.target), "/content/op1_factory.json")) {
                    fail(j, e_path, 0, j.target);
                    break;
                }
                source = p;
            }
            r = load(j, io, source, j.file, j.file.capacity);
            if (r == x_more || r == x_failed) break;
            if (r == x_missing) {
                fail(j, e_open, 0, source);
                break;
            }
            if (!hash_equal(j.target_catalog, j.digest.bytes)) {
                fail(j, e_sha256, 0, source);
                break;
            }
            j.leg = 1;
            break;
        }
        join(p, root, bytes(catalog), length(catalog));
        if (!allowed(Use::catalog, p, false)) {
            fail(j, e_path, 0, p);
            break;
        }
        if (store(j, io, p, j.file.data, j.file.length) == x_done) {
            ++j.done;
            ++j.written;
            j.bytes += j.file.length;
            j.stage = st_final;
            j.leg = 0;
            note(j, "record");
        }
        break;
    case st_final:                                       // the record for the target, last
        if (j.leg == 0) {
            if (plan(j, false)) j.leg = 1;
            break;
        }
        if (!allowed(Use::record, record_path, false)) {
            fail(j, e_path, 0, record_path);
            break;
        }
        if (store(j, io, record_path, j.plan.data, j.plan.length) == x_done) {
            j.stage = st_done;
            note(j, j.target);
        }
        break;
    default: break;
    }
    const uint32_t used = j.official.length + j.list.length + j.record.length + j.plan.length + j.file.length;
    if (used > j.peak) j.peak = used;
    return phase(j);
}
}}
#endif

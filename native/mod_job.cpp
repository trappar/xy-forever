// Mod assembly job: mod_link, mod_patch and mod_content in bounded
// steps over one work region, every file through the caller's file calls, one library entry out. See
// mod_job.hpp.
#include "mod_job.hpp"
#include "inflate.hpp"
#include "../target/xyf_package_record.hpp"
namespace xyf_core { namespace mod_job {
namespace {
enum Stage {
    st_base_list, st_base_info, st_alloc, st_edits,
    st_package_list, st_package_info, st_package_catalog, st_catalog, st_official, st_stock,  // inputs
    st_patch_start, st_patch_wait,              // patch up to the module
    st_link_begin, st_link,                     // link
    st_set_module, st_patch, st_crc,            // patch, os.ldr's CRC-32
    st_content_start, st_content, st_entry,     // content, entry texts
    st_write, st_verify, st_check_start, st_check, st_finish
};
// Outputs in write, read-back and rename order: mod_content's list (content/..., sorted), then these three,
// os.ldr last.
const U32 fixed_outputs = 3, none = 0xffffffffu;
const char* const fixed_names[fixed_outputs] = {xyf::package_record::native_name, "files.sha256", "os.ldr"};
const char part[] = ".part";
const char* const kinds[6] = {"synth", "drum", "sample", "effect", "player", "extension"};

__attribute__((noinline)) bool same(const char* a, const char* b) {
    while (*a && *a == *b) { ++a; ++b; }
    return *a == *b;
}
bool same_bytes(const Byte* a, U32 n, const char* b) {
    for (U32 i = 0; i != n; ++i) if (!b[i] || Byte(b[i]) != a[i]) return false;
    return !b[n];
}
bool starts(const char* s, const char* prefix) {
    for (U32 i = 0; prefix[i]; ++i) if (s[i] != prefix[i]) return false;
    return true;
}
void copy_digest(hash::Digest& to, const hash::Digest& from) { for (U32 i = 0; i != 32; ++i) to.bytes[i] = from.bytes[i]; }

// Detail: up to three parts joined by '/', cut at detail_bytes - 1.
void set_detail(Job& j, const char* a, const char* b = 0, const char* c = 0) {
    const char* parts[3] = {a, b, c};
    U32 n = 0;
    for (U32 k = 0; k != 3; ++k) {
        if (!parts[k]) continue;
        if (k && n < detail_bytes - 1) j.detail[n++] = '/';
        for (const char* s = parts[k]; *s && n < detail_bytes - 1; ++s) j.detail[n++] = *s;
    }
    j.detail[n] = 0;
}
__attribute__((noinline)) void set_detail_text(Job& j, const char* text) {
    U32 n = 0;
    for (; text[n] && n < detail_bytes - 1; ++n) j.detail[n] = text[n];
    j.detail[n] = 0;
}
// j.path = a/b/c + suffix (b, c optional); false when it does not fit path_bytes.
bool compose(Job& j, const char* a, const char* b, const char* c, const char* suffix) {
    const char* parts[4] = {a, b, c, suffix};
    U32 n = 0;
    for (U32 k = 0; k != 4; ++k) {
        if (!parts[k]) continue;
        if (k && k != 3) { if (n == path_bytes - 1) return false; j.path[n++] = '/'; }
        for (const char* s = parts[k]; *s; ++s) { if (n == path_bytes - 1) return false; j.path[n++] = *s; }
    }
    j.path[n] = 0;
    return true;
}

// Removes every output name the job may have written (parts and finals: each content file opened so far,
// the three fixed names always) and closes an open handle. Results are ignored: a name that is absent stays
// absent. Folders stay.
void cleanup(Job& j) {
    const Files& f = *j.files;
    if (j.load.active && j.load.handle >= 0) f.close(f.context, j.load.handle);
    j.load.active = false; j.load.ready = false; j.load.handle = -1;
    if (j.out_handle >= 0) f.close(f.context, j.out_handle);
    j.out_handle = -1;
    const U32 listed = j.out_count ? j.out_count - fixed_outputs : 0;
    for (U32 i = 0; i != listed + fixed_outputs; ++i) {
        if (i < listed && i >= j.out_written) continue;   // a content file never opened
        const char* name = i < listed ? j.builder.listed[i].path : fixed_names[i - listed];
        if (compose(j, j.input->out, name, 0, part)) f.remove(f.context, j.path);
        if (compose(j, j.input->out, name, 0, 0)) f.remove(f.context, j.path);
    }
    j.out_written = 0;
}
__attribute__((noinline)) Phase fail(Job& j, Error error, U32 status = 0) {
    j.error = error; j.status = status;
    cleanup(j);
    j.phase = Phase::failed;
    return j.phase;
}
Phase phase_of(U32 stage) {
    if (stage <= st_stock) return Phase::inputs;
    if (stage == st_link_begin || stage == st_link) return Phase::link;
    if (stage <= st_crc) return Phase::patch;
    if (stage <= st_entry) return Phase::content;
    if (stage == st_write) return Phase::write;
    if (stage <= st_check) return Phase::verify;
    return Phase::finish;
}
__attribute__((noinline)) void go(Job& j, U32 stage, U32 total = 0) {
    const Phase next = phase_of(stage);
    if (next != j.phase) { j.done = 0; j.total = total; }
    j.stage = stage; j.phase = next;
}
// region[low...) for `bytes`; kept (low advances) when keep. 0: it does not fit.
__attribute__((noinline)) Byte* take(Job& j, U32 bytes, bool keep) {
    // Pointer alignment (4 on the Blackfin; 8 on a 64-bit host, where tables of pointers live here).
    const U32 a = alignof(void*) > 4 ? U32(alignof(void*)) : 4u;
    const U32 at = (j.low + (a - 1)) & ~(a - 1);
    if (at > j.region_bytes || bytes > j.region_bytes - at) return 0;
    if (at + bytes > j.peak) j.peak = at + bytes;
    if (keep) j.low = at + bytes;
    return j.region + at;
}

// Reading one file (j.path), one read call per step. dest 0: at region[low] (kept when keep). Read-back
// (back): the size must be `expect`, every failure is Error::verify; stream: into dest (chunk_bytes) again
// and again, only hashed. want: the SHA-256 it must have.
Error begin_load(Job& j, const hash::Digest* want, Byte* dest, U32 capacity, bool keep, bool back = false,
                 bool stream = false, U32 expect = 0) {
    const Files& f = *j.files;
    U32 size = 0;
    if (f.size(f.context, j.path, &size) < 0) return back ? Error::verify : Error::open;
    if (back) {
        if (size != expect || (!stream && size > capacity)) return Error::verify;
    } else if (!dest) {
        if (!keep && size <= j.spare_bytes) {
            dest = j.spare;
            const U32 end = U32(dest - j.region) + size;
            if (end > j.peak) j.peak = end;
        } else dest = take(j, size, keep);
        if (!dest) return Error::region;
    } else if (size > capacity) return Error::size;
    const int handle = f.open(f.context, j.path, false);
    if (handle < 0) return back ? Error::verify : Error::open;
    Job::Load& l = j.load;
    l.handle = handle; l.data = dest; l.size = size; l.done = 0; l.want = want;
    l.sha.reset();
    l.active = true; l.ready = false; l.stream = stream; l.back = back;
    return Error::none;
}
__attribute__((noinline)) Error load_step(Job& j) {
    const Files& f = *j.files;
    Job::Load& l = j.load;
    U32 n = l.size - l.done;
    if (n > chunk_bytes) n = chunk_bytes;
    if (n) {
        Byte* at = l.stream ? l.data : l.data + l.done;
        const int r = f.read(f.context, l.handle, at, n);
        if (r < 0 || U32(r) > n) return Error::read;
        if (!r) return Error::truncated;
        if (l.want) l.sha.update(at, U32(r));
        l.done += U32(r); j.work = U32(r);
        if (j.phase == Phase::inputs) j.done += U32(r);
        if (l.done != l.size) return Error::none;
    }
    const int closed = f.close(f.context, l.handle);
    l.handle = -1; l.active = false;
    if (closed < 0) return Error::read;
    if (l.want) {
        hash::Digest d;
        l.sha.finish(d);
        if (!hash::equal(d, *l.want)) return l.back ? Error::verify : Error::sha256;
    }
    l.ready = true;
    return Error::none;
}
bool consume(Job& j) {   // the file begun earlier is read and checked
    if (!j.load.ready) return false;
    j.load.ready = false;
    return true;
}

int hex(Byte c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}
#include "mod_job_metadata.ipp"
Job::Package* package_named(Job& j, const char* name) {
    for (U32 i = 0; i != j.package_count; ++i) if (same(j.packages[i].name, name)) return &j.packages[i];
    return 0;
}

// info.txt (key=value lines, '#' comments; the last of a repeated key wins), as host/mod_assemble.cpp.
bool value_of(const Byte* info, U32 bytes, const char* key, const Byte*& value, U32& length) {
    bool found = false;
    for (U32 s = 0; s < bytes;) {
        U32 e = s;
        while (e != bytes && info[e] != '\n') ++e;
        U32 eq = s;
        while (eq != e && info[eq] != '=') ++eq;
        if (e != s && info[s] != '#' && eq != e && same_bytes(info + s, eq - s, key)) {
            value = info + eq + 1; length = e - eq - 1; found = true;
        }
        s = e + 1;
    }
    return found;
}
// strtoull(value, 0, 0) over the whole value, <= 0xffffffff.
bool number(const Byte* v, U32 n, U32& out) {
    if (!n) return false;
    U32 base = 10, i = 0;
    if (n > 2 && v[0] == '0' && (v[1] == 'x' || v[1] == 'X')) { base = 16; i = 2; }
    else if (n > 1 && v[0] == '0') { base = 8; i = 1; }
    const U32 most = base == 16 ? 0x0fffffffu : base == 8 ? 0x1fffffffu : 429496729u;   // no 64-bit or division helpers
    U32 value = 0;
    for (; i != n; ++i) {
        const int d = hex(v[i]);
        if (d < 0 || U32(d) >= base || value > most || (base == 10 && value == most && d > 5)) return false;
        value = value * base + U32(d);
    }
    out = value;
    return true;
}
bool number_of(const Byte* info, U32 bytes, const char* key, U32& out) {
    const Byte* v; U32 n;
    return value_of(info, bytes, key, v, n) && number(v, n, out);
}
const char* text_copy(Job& j, const Byte* v, U32 n) {
    char* s = reinterpret_cast<char*>(take(j, n + 1, true));
    if (!s) return 0;
    for (U32 i = 0; i != n; ++i) s[i] = char(v[i]);
    s[n] = 0;
    return s;
}
// "%u,%u" as sscanf reads it (leading digits of each part).
bool pair(const Byte* v, U32 n, U32& a, U32& b) {
    U32 i = 0, k = 0;
    U32* out[2] = {&a, &b};
    for (; k != 2; ++k) {
        if (k) { if (i == n || v[i] != ',') return false; ++i; }
        if (i == n || v[i] < '0' || v[i] > '9') return false;
        U32 x = 0;
        while (i != n && v[i] >= '0' && v[i] <= '9') x = x * 10 + U32(v[i++] - '0');
        *out[k] = x;
    }
    return true;
}
// The content step's view of package i (info.txt, files.sha256, catalog.txt).
Error content_view(Job& j, U32 i) {
    const Job::Package& p = j.packages[i];
    mod_content::Package& c = j.content_packages[i - 1];
    const Byte* v; U32 n;
    c.id = value_of(p.info, p.info_bytes, "id", v, n) ? text_copy(j, v, n) : "";
    c.kind = mod_content::Kind(9);
    if (value_of(p.info, p.info_bytes, "machine", v, n))
        for (U32 k = 0; k != 6; ++k) if (same_bytes(v, n, kinds[k])) c.kind = mod_content::Kind(k);
    c.order = 0; number_of(p.info, p.info_bytes, "order", c.order);
    c.asset = 0;
    const bool asset = value_of(p.info, p.info_bytes, "asset", v, n) && n && !same_bytes(v, n, "-");
    if (asset) c.asset = text_copy(j, v, n);
    c.numbered = false; c.number_start = c.number_end = 0;
    if (value_of(p.info, p.info_bytes, "catalog_number", v, n) && !same_bytes(v, n, "-")) {
        c.numbered = true;
        if (!pair(v, n, c.number_start, c.number_end)) { set_detail(j, p.name, "info.txt catalog_number"); return Error::info; }
    }
    c.presets = 0; number_of(p.info, p.info_bytes, "presets", c.presets);
    c.files = p.files; c.file_count = p.file_count;
    c.catalog_text = p.catalog; c.catalog_bytes = p.catalog_bytes;
    if (!c.id || (asset && !c.asset)) { set_detail(j, p.name, "info.txt"); return Error::region; }
    return Error::none;
}

// Begins reading package file `path` (listed in its files.sha256).
Phase load_listed(Job& j, Job::Package& p, const char* path, bool keep) {
    set_detail(j, p.name, path);
    const hash::Digest* want = listed(p, path);
    if (!want) return fail(j, Error::unlisted);
    char stored[24];                             // flat: mod_job.hpp
    if (j.input->flat) stored_name(path, *want, stored);
    if (!compose(j, j.input->generation, p.name, j.input->flat ? stored : path, 0)) return fail(j, Error::path);
    const Error e = begin_load(j, want, 0, 0, keep);
    return e == Error::none ? j.phase : fail(j, e);
}
Phase load_input(Job& j, const char* what, const char* path) {
    set_detail_text(j, what);
    if (!compose(j, path, 0, 0, 0)) return fail(j, Error::path);
    const Error e = begin_load(j, 0, 0, 0, true);
    return e == Error::none ? j.phase : fail(j, e);
}
// Output i (0 <= i < out_count): its name below `out`, its bytes (a content file's only while it is being
// written) and the SHA-256 it must read back with.
__attribute__((noinline)) const char* out_name(const Job& j, U32 i) {
    const U32 listed = j.out_count - fixed_outputs;
    return i < listed ? j.builder.listed[i].path : fixed_names[i - listed];
}
__attribute__((noinline)) const Byte* out_data(const Job& j, U32 i) {
    const U32 listed = j.out_count - fixed_outputs;
    if (i < listed) return j.builder.listed[i].package == none ? j.catalog_out : j.source;
    const Byte* const fixed[fixed_outputs] = {j.info_out, j.list_out, j.image};
    return fixed[i - listed];
}
__attribute__((noinline)) const hash::Digest& out_digest(const Job& j, U32 i) {
    const U32 listed = j.out_count - fixed_outputs;
    if (i < listed) return *j.builder.listed[i].sha256;
    const hash::Digest* const fixed[fixed_outputs] = {&j.info_sha256, &j.list_sha256, &j.ldr_sha256};
    return *fixed[i - listed];
}
// Every folder of content output i below `out` that the output before it did not already need (the list
// is sorted, so those exist): mkdir, outermost first.
__attribute__((noinline)) Error make_folders(Job& j, U32 i) {
    const char* path = j.builder.listed[i].path;
    const char* prev = i ? j.builder.listed[i - 1].path : "";
    if (!compose(j, j.input->out, path, 0, 0)) return Error::path;
    U32 at = 0;
    while (j.input->out[at]) ++at;
    ++at;                                // after "<out>/"
    bool same = true;
    for (U32 k = 0; path[k]; ++k) {
        same = same && prev[k] == path[k];
        if (path[k] != '/' || same) continue;
        j.path[at + k] = 0;
        const int made = j.files->mkdir(j.files->context, j.path);
        j.path[at + k] = '/';
        if (made < 0) return Error::write;
    }
    return Error::none;
}

// Text into a bounded buffer (ok false once it overflows); decimal without division helpers.
#include "mod_job_text.ipp"

bool valid_name(const char* s) {       // manifest name
    U32 n = 0;
    for (; s[n] && n != 48; ++n) if (Byte(s[n]) < 0x20 || Byte(s[n]) > 0x7e) return false;
    return n && n <= 47 && !s[n] && s[0] != ' ' && s[n - 1] != ' ';
}
bool valid_id(const char* id) {
    U32 n = 0;
    for (; id[n] && n != 64; ++n) {
        const char c = id[n];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_')) return false;
    }
    return n && !id[n];
}

Phase step_link(Job& j) {
    if (consume(j)) {                    // the requested file is in RAM
        const Job::Load& l = j.load;
        if (j.check_object) {
            const mod_content::Status s = mod_content::check_object(l.data, l.size);
            if (s != mod_content::Status::ok) return fail(j, Error::content, U32(s));
        }
        j.work = l.size;
        const mod_link::Status s = mod_link::step(j.workspace, l.size ? l.data : 0, l.size, j.linked);
        ++j.done;
        if (s != mod_link::Status::ok) { set_detail_text(j, j.linked.detail); return fail(j, Error::link, U32(s)); }
        return j.phase;
    }
    mod_link::Request r;
    if (!mod_link::next(j.workspace, r)) {
        if (j.linked.status != mod_link::Status::ok) {
            set_detail_text(j, j.linked.detail);
            return fail(j, Error::link, U32(j.linked.status));
        }
        go(j, st_set_module);
        return j.phase;
    }
    j.link_pass = !*r.path ? 2u : j.linked.sizes.initialized ? 3u : 1u;
    if (!*r.path) {                      // a zero slice of the module
        j.work = mod_link::zero_slice;
        const mod_link::Status s = mod_link::step(j.workspace, 0, 0, j.linked);
        ++j.done;
        if (s != mod_link::Status::ok) { set_detail_text(j, j.linked.detail); return fail(j, Error::link, U32(s)); }
        return j.phase;
    }
    Job::Package* p = package_named(j, r.package);
    if (!p) { set_detail(j, r.package, r.path); return fail(j, Error::unlisted); }
    if (same(r.path, "info.txt")) {      // in RAM
        j.work = p->info_bytes;
        const mod_link::Status s = mod_link::step(j.workspace, p->info_bytes ? p->info : 0, p->info_bytes, j.linked);
        ++j.done;
        if (s != mod_link::Status::ok) { set_detail_text(j, j.linked.detail); return fail(j, Error::link, U32(s)); }
        return j.phase;
    }
    j.check_object = p != j.packages && starts(r.path, "objects/");
    // The file goes into the module's unused part of the image and on into the free region above it (the image
    // is the last thing kept; the larger layout's image leaves too little room above it for the largest
    // objects): from the module's start before the zero steps (initialized is 0 until pass 1 ends), after its
    // initialized part in pass 2 (the linker writes only below that).
    const U32 used = (j.linked.sizes.initialized + 7u) & ~7u, at = U32(j.image - j.region) + j.profile.stock_bytes + used;
    j.spare = j.region + at; j.spare_bytes = at < j.region_bytes ? j.region_bytes - at : 0;
    const Phase next = load_listed(j, *p, r.path, false);
    j.spare_bytes = 0;
    return next;
}
}

Error start(Job& j, const Input& input, const Files& files, Byte* region, U32 region_bytes) {
    j.input = &input; j.files = &files;
    j.region = region; j.region_bytes = region_bytes; j.low = 0; j.peak = 0;
    j.phase = Phase::idle; j.error = Error::none; j.status = 0; j.detail[0] = 0;
    j.stage = st_base_list; j.index = 0; j.steps = 0; j.work = 0; j.done = 0; j.total = 0; j.link_pass = 0;
    j.packages = 0; j.package_count = 0; j.content_packages = 0;
    j.load.handle = -1; j.load.active = false; j.load.ready = false; j.load.stream = false;
    j.out_handle = -1; j.out_index = 0; j.out_done = 0; j.out_count = 0; j.out_written = 0; j.out_sizes = 0;
    j.source = 0; j.check_object = false; j.spare = 0; j.spare_bytes = 0;
    j.image = 0; j.image_bytes = 0; j.edits = 0; j.edits_bytes = 0;
    j.stock_catalog = j.official = 0; j.stock_catalog_bytes = j.official_bytes = 0;
    const mod_link::Selection& sel = input.selection;
    const bool calls = files.probe_metadata && files.size && files.open && files.read && files.write && files.close && files.rename &&
                       files.remove && files.mkdir;
    if (!calls || !input.out) { j.error = Error::argument; j.phase = Phase::failed; return j.error; }
    const U32 count = 1 + sel.machine_count + sel.extension_count;
    bool ok = input.generation && input.stock && input.catalog && input.official && input.policy && region &&
              input.name && valid_name(input.name) &&
              !(reinterpret_cast<unsigned long>(region) & 3) && (sel.machines || !sel.machine_count) &&
              (sel.extensions || !sel.extension_count) && sel.machine_count <= mod_content::max_packages &&
              sel.extension_count <= mod_content::max_packages && count - 1 <= mod_content::Limits().packages;
    for (U32 i = 0; ok && i != sel.machine_count; ++i) ok = sel.machines[i] && valid_id(sel.machines[i]);
    for (U32 i = 0; ok && i != sel.extension_count; ++i) ok = sel.extensions[i] && valid_id(sel.extensions[i]);
    if (!ok) { set_detail_text(j, "input"); fail(j, Error::argument); return j.error; }
    j.phase = Phase::inputs;
    j.packages = reinterpret_cast<Job::Package*>(take(j, count * U32(sizeof(Job::Package)), true));
    j.content_packages = reinterpret_cast<mod_content::Package*>(
        take(j, (count - 1) * U32(sizeof(mod_content::Package)), true));
    if (!j.packages || !j.content_packages) { set_detail_text(j, "packages"); fail(j, Error::region); return j.error; }
    j.package_count = count;
    for (U32 i = 0; i != count; ++i) {
        Job::Package& p = j.packages[i];
        if (!i) p.name = "base";
        else if (i <= sel.machine_count) p.name = sel.machines[i - 1];
        else {
            const char* id = sel.extensions[i - 1 - sel.machine_count];
            U32 n = 0;
            while (id[n]) ++n;
            char* name = reinterpret_cast<char*>(take(j, n + 5, true));
            if (!name) { set_detail_text(j, "packages"); fail(j, Error::region); return j.error; }
            name[0] = 'e'; name[1] = 'x'; name[2] = 't'; name[3] = '-';
            for (U32 k = 0; k <= n; ++k) name[4 + k] = id[k];
            p.name = name;
        }
        p.list = 0; p.list_bytes = 0; p.files = 0; p.file_count = 0;
        p.info_sha256 = p.native_sha256 = 0; p.metadata = 0;
        p.info = 0; p.info_bytes = 0; p.catalog = 0; p.catalog_bytes = 0;
    }
    return Error::none;
}

Phase step(Job& j) {
    if (j.phase == Phase::idle || j.phase == Phase::done || j.phase == Phase::failed || j.phase == Phase::cancelled)
        return j.phase;
    ++j.steps; j.work = 0;
    if (j.load.active) {
        const Error e = load_step(j);
        return e == Error::none ? j.phase : fail(j, e);
    }
    const Files& f = *j.files;
    const Input& in = *j.input;
    Job::Package& base = j.packages[0];
    switch (j.stage) {
    case st_base_list:
    case st_package_list: {
        Job::Package& p = j.packages[j.index];
        if (!consume(j)) {
            set_detail(j, p.name, "files.sha256");
            if (!compose(j, in.generation, p.name, "files.sha256", 0)) return fail(j, Error::path);
            const Error e = begin_load(j, 0, 0, 0, true);
            return e == Error::none ? j.phase : fail(j, e);
        }
        p.list = j.load.data; p.list_bytes = j.load.size;
        const Error e = parse_list(j, p);
        if (e != Error::none) return fail(j, e);
        go(j, j.stage == st_base_list ? st_base_info : st_package_info);
        return j.phase;
    }
    case st_base_info: {
        if (!consume(j)) return begin_metadata(j, base);
        const Error e = finish_metadata(j, base, xyf::manifest::type_mod_base);
        if (e != Error::none) return fail(j, e);
        char detail[mod_patch::detail_bytes];
        if (mod_patch::read_profile(base.info, base.info_bytes, j.profile, detail) != mod_patch::Status::ok) {
            set_detail_text(j, detail);
            return fail(j, Error::base);
        }
        const char* keys[3] = {"catalog_engine_at", "catalog_fx_at", "catalog_seq_at"};
        U32* fields[3] = {&j.pins.engine_at, &j.pins.fx_at, &j.pins.seq_at};
        for (U32 k = 0; k != 3; ++k)
            if (!number_of(base.info, base.info_bytes, keys[k], *fields[k])) { set_detail_text(j, keys[k]); return fail(j, Error::base); }
        copy_digest(j.pins.sha256, j.profile.catalog_sha256);
        if (!listed(base, "edits.bin")) { set_detail_text(j, "edits.bin"); return fail(j, Error::base); }
        j.profile.expanded = in.selection.expanded;
        mod_patch::Profile used;             // the layout the link and patch use (maybe the larger one)
        if (!mod_patch::layout(j.profile, used)) { set_detail_text(j, "expanded_module_base"); return fail(j, Error::base); }
        j.module_limit = used.module_bytes; j.reserved_bytes = used.reserved_bytes;
        if (j.module_limit > 0xffffffffu - 16u - j.profile.stock_bytes) {
            set_detail_text(j, "image size");
            return fail(j, Error::base);
        }
        go(j, st_alloc);
        return j.phase;
    }
    case st_alloc: {                     // the work areas (the image buffer comes last: st_official)
        const mod_link::Limits llimits; const mod_patch::Limits plimits; const mod_content::Limits climits;
        const U32 lbytes = mod_link::scratch_bytes(llimits), pbytes = mod_patch::scratch_bytes(plimits),
                  cbytes = mod_content::scratch_bytes(climits);
        j.link_scratch = take(j, lbytes, true);
        j.patch_scratch = j.link_scratch ? take(j, pbytes, true) : 0;
        j.content_scratch = j.patch_scratch ? take(j, cbytes, true) : 0;
        j.catalog_out = j.content_scratch ? take(j, catalog_bytes, true) : 0;
        j.list_out = j.catalog_out ? take(j, list_bytes, true) : 0;
        if (!j.list_out) { set_detail_text(j, "work areas"); return fail(j, Error::region); }
        j.workspace.scratch = j.link_scratch; j.workspace.capacity = lbytes;
        go(j, st_edits);
        return j.phase;
    }
    case st_stock: {                     // straight into the image buffer
        if (!consume(j)) {
            set_detail_text(j, "stock");
            if (!compose(j, in.stock, 0, 0, 0)) return fail(j, Error::path);
            const Error e = begin_load(j, 0, j.image, j.image_bytes, false);
            return e == Error::none ? j.phase : fail(j, e);
        }
        go(j, st_patch_start);
        return j.phase;
    }
    case st_edits:
        if (!consume(j)) return load_listed(j, base, "edits.bin", true);
        j.edits = j.load.data; j.edits_bytes = j.load.size;
        j.index = 1;
        go(j, j.package_count > 1 ? st_package_list : st_catalog);
        return j.phase;
    case st_package_info: {
        Job::Package& p = j.packages[j.index];
        if (!consume(j)) return begin_metadata(j, p);
        const Error e = finish_metadata(j, p, xyf::manifest::type_mod);
        if (e != Error::none) return fail(j, e);
        go(j, st_package_catalog);
        return j.phase;
    }
    case st_package_catalog: {
        Job::Package& p = j.packages[j.index];
        if (!consume(j)) {
            if (listed(p, "catalog.txt") && !p.catalog) return load_listed(j, p, "catalog.txt", true);
        } else {
            p.catalog = j.load.data; p.catalog_bytes = j.load.size;
        }
        const Error e = content_view(j, j.index);
        if (e != Error::none) return fail(j, e);
        ++j.index;
        go(j, j.index < j.package_count ? st_package_list : st_catalog);
        return j.phase;
    }
    case st_catalog:
        if (!consume(j)) return load_input(j, "catalog", in.catalog);
        j.stock_catalog = j.load.data; j.stock_catalog_bytes = j.load.size;
        go(j, st_official);
        return j.phase;
    case st_official:
        if (!consume(j)) return load_input(j, "official", in.official);
        j.official = j.load.data; j.official_bytes = j.load.size;
        // The image buffer, the last thing kept: the module's unused part then runs on into the free region,
        // where the link's files go (step_link).
        j.image_bytes = j.profile.stock_bytes + 16u + j.module_limit;
        j.image = take(j, j.image_bytes, true);
        if (!j.image) { set_detail_text(j, "work areas"); return fail(j, Error::region); }
        go(j, st_stock);
        return j.phase;
    case st_patch_start:
    case st_patch_wait:
    case st_set_module:
    case st_patch: {
        const mod_patch::Limits plimits;
        mod_patch::Status s;
        if (j.stage == st_patch_start)
            s = mod_patch::start(j.patcher, j.profile, j.edits, j.edits_bytes, *listed(base, "edits.bin"), j.image,
                                 j.image_bytes, in.profiling, in.unverified, *in.policy, plimits, j.patch_scratch,
                                 mod_patch::scratch_bytes(plimits));
        else if (j.stage == st_set_module) s = mod_patch::set_module(j.patcher, j.workspace, j.linked);
        else { s = mod_patch::step(j.patcher); j.work = j.patcher.slice_bytes; }
        ++j.done;
        if (s == mod_patch::Status::more) {
            if (j.stage == st_patch_start) go(j, st_patch_wait);
            if (j.stage == st_set_module) go(j, st_patch);
            return j.phase;
        }
        const bool before = j.stage == st_patch_start || j.stage == st_patch_wait;
        if (before && s == mod_patch::Status::wait) { go(j, st_link_begin); return j.phase; }
        if (!before && s == mod_patch::Status::ok) { j.crc = 0xffffffffu; j.crc_done = 0; go(j, st_crc); return j.phase; }
        set_detail_text(j, j.patcher.detail);
        return fail(j, s == mod_patch::Status::unverified ? Error::base : Error::patch, U32(s));   // an unknown base: base
    }
    case st_link_begin: {
        const mod_link::Limits llimits;
        const mod_link::Status s = mod_link::begin(in.selection, llimits, j.workspace, j.image + j.profile.stock_bytes,
                                                   j.image_bytes - j.profile.stock_bytes, j.linked);
        if (s != mod_link::Status::ok) { set_detail_text(j, j.linked.detail); return fail(j, Error::link, U32(s)); }
        go(j, st_link);
        return j.phase;
    }
    case st_link:
        return step_link(j);
    case st_crc: {                       // os.ldr CRC-32, crc_slice bytes per step
        U32 n = j.patcher.total - j.crc_done;
        if (n > crc_slice) n = crc_slice;
        j.crc = zip::crc32_update(j.crc, j.image + j.crc_done, n);
        j.crc_done += n; j.work = n; ++j.done;
        if (j.crc_done == j.patcher.total) { j.crc ^= 0xffffffffu; go(j, st_content_start, j.package_count - 1); }
        return j.phase;
    }
    case st_content_start:
    case st_content: {
        const mod_content::Limits climits;
        mod_content::Status s;
        if (j.stage == st_content_start)
            s = mod_content::start(j.builder, j.pins, j.stock_catalog, j.stock_catalog_bytes, j.official, j.official_bytes,
                                   j.content_packages, j.package_count - 1, in.selection, j.workspace, j.linked,
                                   j.catalog_out, catalog_bytes, j.list_out, list_bytes, climits, j.content_scratch,
                                   mod_content::scratch_bytes(climits));
        else { s = mod_content::step(j.builder); ++j.done; }
        if (s == mod_content::Status::more) { go(j, st_content); return j.phase; }
        if (s != mod_content::Status::ok) { set_detail_text(j, j.builder.detail); return fail(j, Error::content, U32(s)); }
        go(j, st_entry);
        return j.phase;
    }
    case st_entry: {
        const Error e = entry_texts(j);
        if (e != Error::none) return fail(j, e);
        j.out_index = 0; j.out_done = 0; j.out_written = 0;
        go(j, st_write, j.out_count);
        return j.phase;
    }
    case st_write: {                     // <name>.part: a content file loaded first, folders, open; one write call per step
        const U32 i = j.out_index, listed = j.out_count - fixed_outputs;
        if (j.out_handle < 0) {
            if (i < listed && j.builder.listed[i].package != none) {
                const mod_content::Listed& l = j.builder.listed[i];
                if (!consume(j)) return load_listed(j, j.packages[l.package + 1], l.path, false);
                j.source = j.load.data; j.out_sizes[i] = j.load.size;
            }
            set_detail_text(j, out_name(j, i));
            if (i < listed) {
                const Error e = make_folders(j, i);
                if (e != Error::none) return fail(j, e);
            }
            if (!compose(j, in.out, out_name(j, i), 0, part)) return fail(j, Error::path);
            j.out_written = i + 1;
            j.out_handle = f.open(f.context, j.path, true);
            if (j.out_handle < 0) return fail(j, Error::write);
            j.out_done = 0;
            return j.phase;
        }
        const U32 bytes = j.out_sizes[i];
        U32 n = bytes - j.out_done;
        if (n > chunk_bytes) n = chunk_bytes;
        if (n) {
            const int r = f.write(f.context, j.out_handle, out_data(j, i) + j.out_done, n);
            if (r <= 0 || U32(r) > n) return fail(j, Error::write);
            j.out_done += U32(r); j.work = U32(r);
            if (j.out_done != bytes) return j.phase;
        }
        const int closed = f.close(f.context, j.out_handle);
        j.out_handle = -1;
        if (closed < 0) return fail(j, Error::write);
        ++j.done;
        if (++j.out_index == j.out_count) { j.out_index = 0; go(j, st_verify, j.out_count); }
        return j.phase;
    }
    case st_verify: {                    // each part read back and its SHA-256 compared; os.ldr whole, into the image buffer
        const U32 i = j.out_index;
        const bool ldr = i == j.out_count - 1;
        if (!consume(j)) {
            set_detail_text(j, out_name(j, i));
            if (!compose(j, in.out, out_name(j, i), 0, part)) return fail(j, Error::path);
            Byte* buffer = ldr ? j.image : take(j, chunk_bytes, false);
            if (!buffer) return fail(j, Error::region);
            const Error e = begin_load(j, &out_digest(j, i), buffer, ldr ? j.image_bytes : chunk_bytes, false, true, !ldr,
                                       j.out_sizes[i]);
            return e == Error::none ? j.phase : fail(j, e);
        }
        if (ldr) { go(j, st_check_start); return j.phase; }
        ++j.done; ++j.out_index;
        return j.phase;
    }
    case st_check_start:                 // os.ldr as read back: its layout and the policy; st_verify proved its
                                         // SHA-256 equal to the image patch pass 2 checked whole (190: not hashed again)
    case st_check: {
        const mod_patch::Limits plimits;
        mod_patch::Status s;
        if (j.stage == st_check_start) {
            s = mod_patch::start_verify(j.patcher, j.profile, j.edits, j.edits_bytes, *listed(base, "edits.bin"), j.image,
                                        j.ldr_bytes, in.unverified, *in.policy, plimits, j.patch_scratch,
                                        mod_patch::scratch_bytes(plimits), true);
            go(j, st_check);
        } else { s = mod_patch::step(j.patcher); j.work = j.patcher.slice_bytes; }
        if (s == mod_patch::Status::more) return j.phase;
        if (s != mod_patch::Status::ok) { set_detail_text(j, j.patcher.detail); return fail(j, Error::image, U32(s)); }
        if (!hash::equal(j.patcher.sha256, j.ldr_sha256)) {   // the patch's own hash (kept: `hashed`), as recorded
            set_detail_text(j, "image sha256");
            return fail(j, Error::image, U32(mod_patch::Status::verify));
        }
        ++j.done; j.out_index = 0;
        go(j, st_finish, j.out_count);
        return j.phase;
    }
    case st_finish: {                    // every part onto its name, os.ldr last
        const char* name = out_name(j, j.out_index);
        set_detail_text(j, name);
        if (!compose(j, in.out, name, 0, 0)) return fail(j, Error::path);
        f.remove(f.context, j.path);
        char final_path[path_bytes];
        for (U32 k = 0; k != path_bytes; ++k) { final_path[k] = j.path[k]; if (!j.path[k]) break; }
        if (!compose(j, in.out, name, 0, part)) return fail(j, Error::path);
        if (f.rename(f.context, j.path, final_path) < 0) return fail(j, Error::rename);
        ++j.done;
        if (++j.out_index == j.out_count) { j.phase = Phase::done; j.detail[0] = 0; }
        return j.phase;
    }
    }
    return fail(j, Error::argument);
}

void cancel(Job& j) {
    if (j.phase == Phase::idle || j.phase == Phase::done || j.phase == Phase::failed || j.phase == Phase::cancelled)
        return;
    j.error = Error::cancelled; j.status = 0;
    cleanup(j);
    j.phase = Phase::cancelled;
}

void progress(const Job& j, Progress& out) {
    out.phase = j.phase; out.error = j.error; out.status = j.status; out.done = j.done; out.total = j.total;
}

const char* error_name(Error e) {
    switch (e) {
    case Error::none: return "none";
    case Error::argument: return "argument";
    case Error::path: return "path";
    case Error::region: return "region";
    case Error::open: return "open";
    case Error::read: return "read";
    case Error::truncated: return "truncated";
    case Error::size: return "size";
    case Error::list: return "list";
    case Error::unlisted: return "unlisted";
    case Error::sha256: return "sha256";
    case Error::info: return "info";
    case Error::base: return "base";
    case Error::link: return "link";
    case Error::patch: return "patch";
    case Error::content: return "content";
    case Error::write: return "write";
    case Error::rename: return "rename";
    case Error::verify: return "verify";
    case Error::cancelled: return "cancelled";
    case Error::image: return "image";
    }
    return "?";
}
U32 timing_part(const Job& j) {
    switch (j.stage) {
    case st_base_list: case st_base_info: case st_alloc: case st_edits: return tp_base;
    case st_package_list: case st_package_info: case st_package_catalog: return tp_packages;
    case st_catalog: case st_official: return tp_catalog;
    case st_stock: return tp_stock;
    case st_patch_start: case st_patch_wait: return tp_patch1;
    case st_link_begin: case st_link: return j.link_pass == 2 ? tp_zero : j.link_pass == 3 ? tp_link2 : tp_link1;
    case st_set_module: return tp_set_module;
    case st_patch: return tp_patch2;
    case st_crc: return tp_crc;
    case st_content_start: case st_content: case st_entry: return tp_content;
    case st_write: return tp_write;
    case st_verify: return tp_verify;
    case st_check_start: case st_check: return tp_check;
    default: return tp_finish;
    }
}
const char* phase_name(Phase p) {
    switch (p) {
    case Phase::idle: return "idle";
    case Phase::inputs: return "inputs";
    case Phase::link: return "link";
    case Phase::patch: return "patch";
    case Phase::content: return "content";
    case Phase::write: return "write";
    case Phase::verify: return "verify";
    case Phase::finish: return "finish";
    case Phase::done: return "done";
    case Phase::failed: return "failed";
    case Phase::cancelled: return "cancelled";
    }
    return "?";
}
} }

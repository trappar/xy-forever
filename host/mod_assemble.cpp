// Host adapter for mod assembly: runs the device's freestanding linker, patch
// and content steps (native/mod_link, mod_patch, mod_content) on the Mac end to end, the way the device
// job does: every input file whole in RAM (the base and the chosen packages, each
// checked against its files.sha256, the stock os.ldr, the official catalog), stock loaded into the image
// buffer, the linker writing the module straight into that buffer at stock_bytes, step() until done.
// The facts come from the package files only (the base's info.txt through mod_patch::read_profile, its
// identities.txt through the linker, edits.bin, each package's info.txt and files.sha256); the job names
// the selection and the files. Nothing comes from core.mjs. Built and driven by xy_forever/modpatch.py.
//
//   mod_assemble run JOB OUT            OUT/os.ldr (then read back and checked by start_verify),
//                                       OUT/content/... (the catalog from the content step, the listed
//                                       package files copied and checked as the job will) and
//                                       OUT/files.sha256; prints JSON (statuses, the link's numbers,
//                                       memory, steps). Nothing is left on a refusal.
//   mod_assemble verify JOB IMAGE       start_verify() on an image file (tamper tests); prints JSON.
//   mod_assemble fuzz JOB SEED N        N damaged variants of the inputs in memory (stock, catalog, any
//                                       package file, the assembled image), file hashes refreshed so the
//                                       damage reaches the parsers; prints the status counts (built with
//                                       sanitizers).
//   mod_assemble job JOB OUT            the stepped device job (native/mod_job) over
//                                       an in-memory file system backed by the real files (folders made by
//                                       the job's mkdir calls), in an 8 MiB region filled with garbage; its
//                                       os.ldr and catalog compared with this one-shot assembly's, its
//                                       files.sha256 must begin with this one's content list; the whole
//                                       entry is written to OUT; prints JSON (steps, largest step per
//                                       phase, peak region use).
//   mod_assemble faults JOB [STRIDE [K]] the job with a read and a write failure injected at every
//                                       STRIDE-th step from K (1), cancelled there, a region one
//                                       byte short, missing, truncated and short files: each must end in
//                                       its error with no output name left, and the same Job must then
//                                       assemble the same bytes again; prints JSON with any violations.
//   mod_assemble jobfuzz JOB SEED N S   the job fed N damaged copies of the files it reads (hashes and pins
//                                       mostly refreshed), at most S seconds: each must end done or failed,
//                                       no output left, no handle open, within a step cap (tests: XYF_FUZZ).
//
// JOB is line based (xy_forever/modpatch.py): generation DIR, machine ID..., extension ID..., layout ID
// SLOT N PAGE..., stock PATH, catalog PATH, official PATH (the firmware's content.sha256), profiling 0|1,
// unverified 0|1 (1: the user confirmed a base that is not a known one), name TEXT (the job's info.txt
// name; default "OP-XY mods"), limit image|catalog|list N,
// unlinked ID... (tests: a machine package given to the content step but not to the link).
#include "../native/mod_link.hpp"
#include "../native/mod_patch.hpp"
#include "../native/mod_content.hpp"
#include "../native/mod_job.hpp"
#include "../target/xyf_package_record.hpp"
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <functional>
#include <map>
#include <random>
#include <set>
#include <sstream>
#include <string>
#include <vector>
#include <sys/stat.h>
#include <unistd.h>
using namespace xyf_core;
typedef std::vector<Byte> Bytes;

namespace {
#include "cold_policy.inc"
const Policy cold = {allowed, sizeof(allowed) / sizeof(allowed[0]), protected_ranges,
                     sizeof(protected_ranges) / sizeof(protected_ranges[0])};

bool slurp(const std::string& path, Bytes& out) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return false;
    out.assign(std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>());
    return true;
}
bool unhex(const std::string& text, hash::Digest& d) {
    if (text.size() != 64) return false;
    for (int i = 0; i != 32; ++i) {
        const std::string pair = text.substr(2 * i, 2);
        char* end = 0;
        d.bytes[i] = Byte(std::strtoul(pair.c_str(), &end, 16));
        if (*end) return false;
    }
    return true;
}
std::string hex(const hash::Digest& d) {
    char out[65];
    for (int i = 0; i != 32; ++i) std::snprintf(out + 2 * i, 3, "%02x", d.bytes[i]);
    return std::string(out, 64);
}
hash::Digest digest(const Byte* p, size_t n) {
    hash::Sha256 h; hash::Digest d;
    h.update(p, U32(n)); h.finish(d);
    return d;
}

struct Job {
    std::string generation, stock, catalog, official, name = "OP-XY mods";
    std::vector<std::string> machines, extensions, unlinked;
    std::vector<mod_link::Number> layout;
    bool has_layout = false, profiling = false, unverified = false, expanded = false;
    U32 image_limit = 0, catalog_limit = 16384, list_limit = 65536;
};
bool read_job(const char* path, Job& job) {
    std::ifstream f(path);
    if (!f) return false;
    std::string line;
    while (std::getline(f, line)) {
        std::istringstream in(line);
        std::string key;
        if (!(in >> key)) continue;
        auto rest = [&]() { std::string r; std::getline(in >> std::ws, r); return r; };
        if (key == "generation") job.generation = rest();
        else if (key == "machine") job.machines.push_back(rest());
        else if (key == "extension") job.extensions.push_back(rest());
        else if (key == "unlinked") job.unlinked.push_back(rest());
        else if (key == "layout") {
            mod_link::Number n; std::memset(&n, 0, sizeof n);
            std::string id;
            in >> id >> n.identity.slot >> n.identity.number >> n.identity.page;
            std::strncpy(n.id, id.c_str(), mod_link::id_bytes - 1);
            n.identity.has_number = n.identity.has_page = true;
            job.layout.push_back(n);
            job.has_layout = true;
        } else if (key == "stock") job.stock = rest();
        else if (key == "catalog") job.catalog = rest();
        else if (key == "official") job.official = rest();
        else if (key == "name") job.name = rest();
        else if (key == "profiling") { int v = 0; in >> v; job.profiling = v; }
        else if (key == "unverified") { int v = 0; in >> v; job.unverified = v; }
        else if (key == "memory") {          // standard, or expanded (the 4 MiB option)
            const std::string m = rest();
            if (m != "standard" && m != "expanded") return false;
            job.expanded = m == "expanded";
        }
        else if (key == "limit") {
            std::string what; U32 v = 0; in >> what >> v;
            if (what == "image") job.image_limit = v;
            else if (what == "catalog") job.catalog_limit = v;
            else if (what == "list") job.list_limit = v;
        } else return false;
    }
    return true;
}

#include "mod_assemble_inputs.ipp"

struct Timing { unsigned steps = 0; double longest_ms = 0; };
template <class F> auto timed(Timing& t, F f) -> decltype(f()) {
    const auto a = std::chrono::steady_clock::now();
    auto r = f();
    const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - a).count();
    ++t.steps; if (ms > t.longest_ms) t.longest_ms = ms;
    return r;
}

std::string quoted(const char* s) {
    std::string r = "\"";
    for (; *s; ++s) r += (*s == '"' || *s == '\\' || Byte(*s) < 0x20 || Byte(*s) >= 0x7f) ? '?' : *s;
    return r + "\"";
}
std::string hex_text(const char* s) {
    std::string r;
    char two[3];
    for (; *s; ++s) { std::snprintf(two, 3, "%02x", Byte(*s)); r += two; }
    return r;
}

// One end-to-end assembly over inputs in RAM. Status words: "ok", a refusal, or "skipped".
struct Assembly {
    std::string input = "ok", input_detail;   // the job's own checks (hashes, base facts)
    mod_link::Status link = mod_link::Status::argument;
    bool linked = false, patched = false, contented = false;
    mod_patch::Status patch = mod_patch::Status::argument, verify = mod_patch::Status::argument;
    mod_content::Status content = mod_content::Status::argument;
    std::string content_object;               // a mod object check_object refused
    mod_patch::Profile profile{};
    mod_content::Catalog pins{};
    Bytes image, out, list;
    std::vector<std::vector<mod_content::File>> files;
    std::vector<mod_content::Package> packages;
    std::vector<std::string> ids, assets;
    std::vector<U32> link_scratch;
    mod_link::Workspace ws{};
    mod_link::Result result{};
    mod_link::Selection selection{};
    std::vector<const char*> machine_ids, extension_ids;
    Timing lt, pt, vt, ct;
    U32 zero_steps = 0, largest_file = 0;
};
mod_content::Kind kind_of(const std::string& k) {
    const char* names[] = {"synth", "drum", "sample", "effect", "player", "extension"};
    for (int i = 0; i != 6; ++i) if (k == names[i]) return mod_content::Kind(i);
    return mod_content::Kind(9);
}

bool check_inputs(Inputs& in, Assembly& a) {
    for (auto& p : in.packages) {
        for (auto& f : p.listing) {
            hash::Digest want;
            auto it = p.files.find(f.second);
            if (it == p.files.end() || !unhex(f.first, want) ||
                !hash::equal(digest(it->second.data(), it->second.size()), want)) {
                a.input = "sha256"; a.input_detail = p.name + "/" + f.second;
                return false;
            }
            a.largest_file = std::max(a.largest_file, U32(it->second.size()));
        }
        if (!listed_sha(p, p.metadata)) {
            a.input = "missing"; a.input_detail = p.name + "/" + p.metadata;
            return false;
        }
        if (p.metadata == record::native_name) {
            const auto type = p.name == "base" ? xyf::manifest::type_mod_base : xyf::manifest::type_mod;
            const auto status = metadata_view(p, type);
            if (status != json::ok) {
                a.input = "package"; a.input_detail = p.name + "/" + p.metadata + ": " + json::message(status);
                return false;
            }
        }
    }
    const Bytes* metadata = info_data(in.packages[0]);
    char detail[mod_patch::detail_bytes];
    if (!metadata || mod_patch::read_profile(metadata->data(), U32(metadata->size()), a.profile, detail) != mod_patch::Status::ok) {
        a.input = "base"; a.input_detail = metadata ? detail : in.packages[0].metadata;
        return false;
    }
    const auto info = info_of(in.packages[0]);
    const char* keys[] = {"catalog_engine_at", "catalog_fx_at", "catalog_seq_at"};
    U32* fields[] = {&a.pins.engine_at, &a.pins.fx_at, &a.pins.seq_at};
    for (int k = 0; k != 3; ++k)
        if (!number_of(info, keys[k], *fields[k])) { a.input = "base"; a.input_detail = keys[k]; return false; }
    a.pins.sha256 = a.profile.catalog_sha256;   // checked against the known bases by the patch
    if (!listed_sha(in.packages[0], "edits.bin")) { a.input = "base"; a.input_detail = "edits.bin"; return false; }
    return true;
}

// The content step's view of each chosen package (info.txt, files.sha256, catalog.txt).
bool content_view(const Inputs& in, Assembly& a) {
    const size_t n = in.packages.size() - 1;
    a.files.assign(n, {}); a.packages.assign(n, mod_content::Package()); a.ids.assign(n, ""); a.assets.assign(n, "");
    for (size_t i = 0; i != n; ++i) {
        const Package& p = in.packages[i + 1];
        const auto info = info_of(p);
        for (auto& f : p.listing) {
            mod_content::File file{f.second.c_str(), {}};
            unhex(f.first, file.sha256);
            if (f.second != record::legacy_name && f.second != record::native_name) a.files[i].push_back(file);
        }
        mod_content::Package& pkg = a.packages[i];
        a.ids[i] = info.count("id") ? info.at("id") : "";
        a.assets[i] = info.count("asset") ? info.at("asset") : "-";
        pkg.id = a.ids[i].c_str();
        pkg.kind = kind_of(info.count("machine") ? info.at("machine") : "");
        number_of(info, "order", pkg.order);
        pkg.asset = a.assets[i] == "-" || a.assets[i].empty() ? 0 : a.assets[i].c_str();
        const std::string at = info.count("catalog_number") ? info.at("catalog_number") : "-";
        pkg.numbered = at != "-";
        if (pkg.numbered && std::sscanf(at.c_str(), "%u,%u", &pkg.number_start, &pkg.number_end) != 2) {
            a.input = "package"; a.input_detail = p.name + "/" + p.metadata + " catalog_number";
            return false;
        }
        number_of(info, "presets", pkg.presets);
        pkg.files = a.files[i].data(); pkg.file_count = U32(a.files[i].size());
        auto it = p.files.find("catalog.txt");
        if (it != p.files.end()) { pkg.catalog_text = it->second.data(); pkg.catalog_bytes = U32(it->second.size()); }
        for (auto& f : p.files)   // each mod object, as the job loads it
            if (!f.first.compare(0, 8, "objects/") &&
                mod_content::check_object(f.second.data(), U32(f.second.size())) != mod_content::Status::ok) {
                a.content = mod_content::Status::package; a.content_object = p.name + "/" + f.first;
                return false;
            }
    }
    return true;
}

hash::Digest edits_sha256(const Inputs& in) {
    hash::Digest d{};
    unhex(*listed_sha(in.packages[0], "edits.bin"), d);
    return d;
}
// start_verify, then (the caller's part) the image's SHA-256 against `want`, the hash recorded at assembly.
mod_patch::Status verify(const Assembly& a, const Inputs& in, const Bytes& image, mod_patch::Patcher& v, Timing& t,
                         bool unverified, const hash::Digest* want) {
    const mod_patch::Limits limits;
    Bytes scratch(mod_patch::scratch_bytes(limits) + 4);
    const Bytes& edits = in.packages[0].files.at("edits.bin");
    mod_patch::Status s = mod_patch::start_verify(v, a.profile, edits.data(), U32(edits.size()), edits_sha256(in),
                                                  image.data(), U32(image.size()), unverified, cold, limits,
                                                  scratch.data(), U32(scratch.size()));
    while (s == mod_patch::Status::more) s = timed(t, [&] { return mod_patch::step(v); });
    if (s == mod_patch::Status::ok && want && !hash::equal(*want, v.sha256)) {
        std::snprintf(v.detail, sizeof v.detail, "image sha256");
        s = mod_patch::Status::verify;
    }
    return s;
}

// Input checks, then: stock into the image, patch steps to its wait, the link (module at stock_bytes),
// set_module, patch steps to ok; the content step over the same link.
void assemble(const Job& job, Inputs& in, Assembly& a, mod_patch::Patcher& p, mod_content::Builder& b) {
    if (!check_inputs(in, a)) return;
    a.profile.expanded = job.expanded;
    mod_patch::Profile used;
    if (!mod_patch::layout(a.profile, used)) { a.input = "base"; a.input_detail = "expanded_module_base"; return; }
    const Bytes& edits = in.packages[0].files.at("edits.bin");
    const hash::Digest edits_sha = edits_sha256(in);
    const unsigned long long size = job.image_limit ? job.image_limit :
        (unsigned long long)used.stock_bytes + 16 + used.module_bytes;
    if (size > (64u << 20) || size < a.profile.stock_bytes) { a.input = "base"; a.input_detail = "image size"; return; }
    a.image.resize(size_t(size));                 // zeroed with memset: a fuzz round reuses the allocation
    std::memset(a.image.data(), 0, a.image.size());
    std::memcpy(a.image.data(), in.stock.data(), std::min(in.stock.size(), a.image.size()));
    const mod_patch::Limits plimits;
    static Bytes pscratch;
    pscratch.assign(mod_patch::scratch_bytes(plimits) + 4, 0);
    a.patched = true;
    a.patch = mod_patch::start(p, a.profile, edits.data(), U32(edits.size()), edits_sha, a.image.data(),
                               U32(a.image.size()), job.profiling, job.unverified, cold, plimits, pscratch.data(),
                               U32(pscratch.size()));
    while (a.patch == mod_patch::Status::more) a.patch = timed(a.pt, [&] { return mod_patch::step(p); });

    // The link, file by file from RAM.
    for (auto& m : job.machines) a.machine_ids.push_back(m.c_str());
    for (auto& e : job.extensions) a.extension_ids.push_back(e.c_str());
    a.selection.machines = a.machine_ids.data(); a.selection.machine_count = U32(a.machine_ids.size());
    a.selection.extensions = a.extension_ids.data(); a.selection.extension_count = U32(a.extension_ids.size());
    a.selection.layout = job.layout.data(); a.selection.layout_count = U32(job.layout.size());
    a.selection.has_layout = job.has_layout;
    a.selection.expanded = job.expanded;
    const mod_link::Limits llimits;
    const U32 lbytes = mod_link::scratch_bytes(llimits);
    a.link_scratch.resize(lbytes / 4 + 1);
    std::memset(a.link_scratch.data(), 0, a.link_scratch.size() * sizeof(U32));
    a.ws.scratch = reinterpret_cast<Byte*>(a.link_scratch.data()); a.ws.capacity = lbytes;
    const U32 stock = std::min<U32>(a.profile.stock_bytes, U32(a.image.size()));
    a.linked = true;
    a.link = mod_link::begin(a.selection, llimits, a.ws, a.image.data() + stock, U32(a.image.size()) - stock, a.result);
    mod_link::Request request;
    while (a.link == mod_link::Status::ok && mod_link::next(a.ws, request)) {
        const Byte* data = 0;
        U32 bytes = 0;
        if (*request.path) {
            const Package* pk = package_named(in, request.package);
            const Bytes* file = !pk ? nullptr : std::strcmp(request.path, record::legacy_name) == 0 ?
                info_data(*pk) : file_data(*pk, request.path);
            if (!file) {
                a.input = "missing"; a.input_detail = std::string(request.package) + "/" + request.path;
                a.link = mod_link::Status::argument;
                break;
            }
            data = file->empty() ? nullptr : file->data(); bytes = U32(file->size());
        } else ++a.zero_steps;
        a.link = timed(a.lt, [&] { return mod_link::step(a.ws, data, bytes, a.result); });
    }
    if (a.link != mod_link::Status::ok) return;
    if (a.patch == mod_patch::Status::wait) {
        a.patch = mod_patch::set_module(p, a.ws, a.result);
        while (a.patch == mod_patch::Status::more) a.patch = timed(a.pt, [&] { return mod_patch::step(p); });
    }
    // Content.
    if (!content_view(in, a)) { a.contented = a.content == mod_content::Status::package; return; }
    const mod_content::Limits climits;
    static Bytes cscratch;
    cscratch.assign(mod_content::scratch_bytes(climits) + 4, 0);
    a.out.assign(job.catalog_limit, 0);
    a.list.assign(job.list_limit, 0);
    a.contented = true;
    a.content = mod_content::start(b, a.pins, in.catalog.data(), U32(in.catalog.size()), in.official.data(),
                                   U32(in.official.size()), a.packages.data(), U32(a.packages.size()), a.selection,
                                   a.ws, a.result, a.out.data(), U32(a.out.size()),
                                   a.list.data(), U32(a.list.size()), climits, cscratch.data(), U32(cscratch.size()));
    while (a.content == mod_content::Status::more) a.content = timed(a.ct, [&] { return mod_content::step(b); });
}

bool write_file(const std::string& path, const Byte* p, size_t n, std::vector<std::string>& written) {
    for (size_t i = 1; i < path.size(); ++i) if (path[i] == '/') mkdir(path.substr(0, i).c_str(), 0755);
    std::FILE* f = std::fopen(path.c_str(), "wb");
    if (!f) return false;
    written.push_back(path);
    const bool ok = std::fwrite(p, 1, n, f) == n;
    return std::fclose(f) == 0 && ok;
}

int run(const Job& job, const std::string& outdir) {
    Inputs in;
    if (!load(job, in)) { std::printf("{\"error\": \"input missing\"}\n"); return 2; }
    static Assembly a;
    static mod_patch::Patcher p, v;
    static mod_content::Builder b;
    std::vector<std::string> written;
    assemble(job, in, a, p, b);
    const char* skipped = "skipped";
    const bool patch_ok = a.patched && a.patch == mod_patch::Status::ok;
    if (patch_ok) {
        Bytes back;
        if (!write_file(outdir + "/os.ldr", a.image.data(), p.total, written) || !slurp(outdir + "/os.ldr", back)) return 2;
        a.verify = verify(a, in, back, v, a.vt, job.unverified, &p.sha256);   // the written file, read back
    }
    const char* copy = "ok";
    const bool content_ok = a.contented && a.content == mod_content::Status::ok;
    if (content_ok) {   // the job's part: write, copy the listed files
        write_file(outdir + "/content/op1_factory.json", a.out.data(), b.catalog_bytes, written);
        for (U32 i = 0; i != b.listed_count; ++i) {
            const mod_content::Listed& l = b.listed[i];
            if (l.package == 0xffffffffu) continue;
            const Bytes& data = in.packages[l.package + 1].files.at(l.path);
            if (!hash::equal(digest(data.data(), data.size()), *l.sha256)) { copy = "sha256"; break; }
            write_file(outdir + "/" + l.path, data.data(), data.size(), written);
        }
        write_file(outdir + "/files.sha256", a.list.data(), b.list_bytes, written);
    }
    const bool link_ok = a.linked && a.link == mod_link::Status::ok;
    if (a.input != "ok" || !link_ok || !patch_ok || a.verify != mod_patch::Status::ok || !content_ok || strcmp(copy, "ok"))
        for (auto& w : written) unlink(w.c_str());
    std::string numbers = "[";
    mod_link::Number n;
    for (U32 i = 0; link_ok && mod_link::number_at(a.ws, i, n); ++i) {
        const char* slots[] = {"engine", "effect", "player"};
        char row[96];
        std::snprintf(row, sizeof row, "%s[\"%s\", \"%s\", %d, %d]", i ? ", " : "", n.id,
                      n.identity.slot < 3 ? slots[n.identity.slot] : "?", n.identity.number, n.identity.page);
        numbers += row;
    }
    numbers += "]";
    std::printf("{\"base\": \"%s\", \"input\": \"%s\", \"input_detail\": %s, \"link\": %d, \"link_detail_hex\": \"%s\", "
                "\"link_ran\": %s, \"numbers\": %s, \"initialized\": %u, \"allocated\": %u, "
                "\"patch\": \"%s\", \"patch_detail\": %s, \"verify\": \"%s\", \"verify_detail\": %s, "
                "\"entry\": %u, \"records\": %u, \"bytes\": %u, \"applied\": %u, \"edits\": %u, \"sha256\": \"%s\", "
                "\"content\": \"%s\", \"content_detail\": %s, \"copy\": \"%s\", \"files\": %u, \"catalog_bytes\": %u, "
                "\"list_bytes\": %u, \"link_scratch\": %u, \"patch_scratch\": %u, \"patch_state\": %zu, "
                "\"content_scratch\": %u, \"content_state\": %zu, \"image_bytes\": %zu, \"catalog_limit\": %u, "
                "\"list_limit\": %u, \"largest_file\": %u, \"link_steps\": %u, \"zero_steps\": %u, "
                "\"link_longest_ms\": %.3f, \"patch_steps\": %u, \"patch_longest_ms\": %.3f, \"verify_longest_ms\": %.3f, "
                "\"content_steps\": %u, \"content_longest_ms\": %.3f}\n",
                !a.patched ? skipped : p.known ? "known" : "unverified", a.input.c_str(),
                quoted(a.input_detail.c_str()).c_str(), a.linked ? int(a.link) : -1,
                hex_text(a.result.detail).c_str(), a.linked ? "true" : "false", numbers.c_str(),
                a.result.sizes.initialized, a.result.sizes.allocated,
                a.patched ? mod_patch::status_name(a.patch) : skipped, quoted(p.detail).c_str(),
                patch_ok ? mod_patch::status_name(a.verify) : skipped, quoted(v.detail).c_str(),
                p.ldr.entry, p.ldr.records, p.total, p.applied, p.edit_count, hex(p.sha256).c_str(),
                a.contented ? mod_content::status_name(a.content) : skipped,
                quoted(a.content_object.empty() ? b.detail : a.content_object.c_str()).c_str(),
                copy, b.listed_count, b.catalog_bytes, b.list_bytes, mod_link::scratch_bytes(mod_link::Limits()),
                mod_patch::scratch_bytes(mod_patch::Limits()), sizeof(mod_patch::Patcher),
                mod_content::scratch_bytes(mod_content::Limits()), sizeof(mod_content::Builder), a.image.size(),
                job.catalog_limit, job.list_limit, a.largest_file, a.lt.steps, a.zero_steps, a.lt.longest_ms,
                a.pt.steps, a.pt.longest_ms, a.vt.longest_ms, a.ct.steps, a.ct.longest_ms);
    return 0;
}

int verify_file(const Job& job, const char* path) {
    Inputs in;
    Bytes image;
    if (!load(job, in) || !slurp(path, image)) return 2;
    static Assembly a;
    static mod_patch::Patcher v;
    if (!check_inputs(in, a)) { std::printf("{\"verify\": \"input\", \"detail\": %s}\n", quoted(a.input_detail.c_str()).c_str()); return 0; }
    a.profile.expanded = job.expanded;
    const mod_patch::Status s = verify(a, in, image, v, a.vt, job.unverified, 0);
    std::printf("{\"verify\": \"%s\", \"detail\": %s, \"base\": \"%s\", \"sha256\": \"%s\"}\n",
                mod_patch::status_name(s), quoted(v.detail).c_str(), v.known ? "known" : "unverified",
                s == mod_patch::Status::ok ? hex(v.sha256).c_str() : "");
    return 0;
}

void damage(Bytes& b, std::mt19937& rng) {
    const int how = rng() % 6;
    if (b.empty()) { b.push_back(Byte(rng())); return; }
    if (how == 0) b.resize(rng() % b.size());
    else if (how == 1) b.insert(b.begin() + rng() % b.size(), 1 + rng() % 8, Byte(rng()));
    else if (how == 2) for (int i = 0, k = 1 + rng() % 4; i != k; ++i) b[rng() % b.size()] ^= Byte(1u << (rng() % 8));
    else if (how == 3) b[rng() % b.size()] = Byte(rng());
    else if (how == 4) { const size_t at = rng() % b.size(); for (size_t i = at; i < b.size() && i < at + 16; ++i) b[i] = 0xff; }
    else { const size_t at = rng() % b.size(); b[at] = Byte("{}[],:\"\\0 x=-\n"[rng() % 14]); }
}
void rehash(Package& p, const std::string& path) {
    for (auto& f : p.listing)
        if (f.second == path) f.first = hex(digest(p.files[path].data(), p.files[path].size()));
}
// A base info.txt key set to a new value (the file and its hash refreshed).
void set_info(Package& base, const std::string& key, const std::string& value) {
    Bytes& info = base.files["info.txt"];
    std::string text(info.begin(), info.end()), out;
    std::istringstream in(text);
    std::string line;
    while (std::getline(in, line)) out += (line.compare(0, key.size() + 1, key + "=") ? line : key + "=" + value) + "\n";
    info.assign(out.begin(), out.end());
    rehash(base, "info.txt");
}

int fuzz(const Job& job, unsigned seed, unsigned count) {
    Inputs in0;
    if (!load(job, in0)) return 2;
    std::mt19937 rng(seed);
    std::map<std::string, unsigned> counts;
    static mod_patch::Patcher p, v;
    static mod_content::Builder b;
    for (unsigned round = 0; round != count; ++round) {
        Inputs in = in0;
        const int target = rng() % 8;
        const bool refresh = rng() % 4 != 0;
        if (target == 0) {   // the stock os.ldr (its pin refreshed)
            damage(in.stock, rng);
            if (refresh) set_info(in.packages[0], "needs_os", hex(digest(in.stock.data(), in.stock.size())));
        } else if (target == 1) {   // the official catalog (its pin refreshed)
            damage(in.catalog, rng);
            if (refresh) set_info(in.packages[0], "catalog_sha256", hex(digest(in.catalog.data(), in.catalog.size())));
        } else if (target <= 4) {   // any file of the base (2) or of a package (3, 4)
            Package& pk = in.packages[target == 2 || in.packages.size() == 1 ? 0 : 1 + rng() % (in.packages.size() - 1)];
            if (!pk.listing.empty()) {
                const std::string path = pk.listing[rng() % pk.listing.size()].second;
                damage(pk.files[path], rng);
                if (refresh) rehash(pk, path);
            }
        } else if (target == 5) {   // a base fact
            const char* keys[] = {"stock_bytes", "module_base", "module_bytes", "catalog_engine_at", "catalog_fx_at",
                                  "catalog_seq_at", "stock_records", "reserved_bytes", "heap_fill_base", "heap_fill_bytes"};
            const auto info = info_of(in.packages[0]);
            const char* key = keys[rng() % 10];
            U32 x = 0;
            number_of(info, key, x);
            x = rng() % 2 ? x + U32(int(rng() % 9) - 4) : U32(rng());
            set_info(in.packages[0], key, std::to_string(x));
        } else if (target == 6 && in.packages.size() > 1) {   // a package fact
            Package& pk = in.packages[1 + rng() % (in.packages.size() - 1)];
            const char* keys[] = {"order", "presets", "catalog_number", "asset", "id", "machine"};
            const char* key = keys[rng() % 6];
            const std::string values[] = {"0", "1", "3,9", "x.svg", "-", "effect", "zz", std::to_string(rng() % 100)};
            set_info(pk, key, values[rng() % 8]);
        }
        static Assembly a;
        Bytes image;                  // the large buffers outlive the fresh Assembly (assemble zeroes them):
        std::vector<U32> scratch;     // allocating them each round is most of a sanitized round's time
        image.swap(a.image), scratch.swap(a.link_scratch);
        a.~Assembly();
        new (&a) Assembly();
        a.image.swap(image), a.link_scratch.swap(scratch);
        assemble(job, in, a, p, b);
        std::string key = "input=" + a.input;
        if (a.linked) key += " link=" + std::to_string(int(a.link));
        if (a.patched) key += std::string(" patch=") + mod_patch::status_name(a.patch);
        if (a.patched && a.patch == mod_patch::Status::ok) {
            Bytes image(a.image.begin(), a.image.begin() + p.total);
            if (target == 7) damage(image, rng);
            const mod_patch::Status vs = verify(a, in, image, v, a.vt, job.unverified, &p.sha256);
            key += std::string(" verify=") + mod_patch::status_name(vs);
            if (target != 7 && vs != mod_patch::Status::ok) { std::printf("{\"error\": \"verify refused a patch\"}\n"); return 1; }
        }
        if (a.contented) key += std::string(" content=") + mod_content::status_name(a.content);
        ++counts[key];
    }
    std::printf("{");
    bool first = true;
    for (auto& c : counts) { std::printf("%s\"%s\": %u", first ? "" : ", ", c.first.c_str(), c.second); first = false; }
    std::printf("}\n");
    return 0;
}

// ---------------------------------------------------------------- the stepped job (native/mod_job)

// Files for the job: reads come from the real files (cached), writes stay in memory. Tests remove,
// replace or misreport files and fail every read or write call made during one step.
struct MemFs {
    std::map<std::string, Bytes> cache, replaced, written;
    std::set<std::string> missing, short_size;   // short_size: size() reports one byte more
    std::set<std::string> dirs{"/out"};          // folders: the output folder, then what mkdir made
    struct Handle { std::string path; size_t pos; bool write, open; };
    std::vector<Handle> handles;
    unsigned now = 0, fail_read = 0, fail_write = 0;   // step numbers (1-based; 0: never)
    bool injected = false;
    const Bytes* find(const std::string& path) {
        auto w = written.find(path);
        if (w != written.end()) return &w->second;
        if (missing.count(path)) return 0;
        auto r = replaced.find(path);
        if (r != replaced.end()) return &r->second;
        auto c = cache.find(path);
        if (c != cache.end()) return &c->second;
        Bytes data;
        if (path.compare(0, 5, "/out/") == 0 || !slurp(path, data)) return 0;
        return &(cache[path] = data);
    }
    void reset() { written.clear(); replaced.clear(); missing.clear(); short_size.clear(); handles.clear();
                   dirs = {"/out"}; now = fail_read = fail_write = 0; injected = false; }
    bool parent_made(const std::string& path) const { return dirs.count(path.substr(0, path.rfind('/'))) != 0; }
    static MemFs& of(void* c) { return *static_cast<MemFs*>(c); }
    static int probe_metadata(void* c, const char* path) {
        U32 bytes;
        return size(c, path, &bytes);
    }
    static int size(void* c, const char* path, U32* bytes) {
        MemFs& fs = of(c);
        if (fs.missing.count(path)) return mod_job::missing_file;
        if (!fs.written.count(path) && !fs.replaced.count(path) && !fs.cache.count(path)) {
            struct stat st;
            if (lstat(path, &st) != 0) return errno == ENOENT ? mod_job::missing_file : -1;
            if (!S_ISREG(st.st_mode) && (stat(path, &st) != 0 || !S_ISREG(st.st_mode))) return -1;
            const auto size = uint64_t(st.st_size) + fs.short_size.count(path);
            if (size > 0xffffffffu) return -1;
            *bytes = U32(size);
            return 0;
        }
        const Bytes* d = fs.find(path);
        if (!d) return -1;
        *bytes = U32(d->size()) + (fs.short_size.count(path) ? 1 : 0);
        return 0;
    }
    static int open(void* c, const char* path, bool write) {
        MemFs& fs = of(c);
        if (write) {
            if (!fs.parent_made(path)) return -1;  // the job must make every folder it writes into
            fs.written[path].clear();
        } else if (!fs.find(path)) return -1;
        fs.handles.push_back({path, 0, write, true});
        return int(fs.handles.size() - 1);
    }
    static int read(void* c, int h, Byte* data, U32 bytes) {
        MemFs& fs = of(c);
        if (fs.fail_read && fs.now == fs.fail_read) { fs.injected = true; return -5; }
        if (h < 0 || size_t(h) >= fs.handles.size() || !fs.handles[h].open || fs.handles[h].write) return -9;
        const Bytes* d = fs.find(fs.handles[h].path);
        if (!d) return -2;
        const size_t n = std::min<size_t>(bytes, d->size() - std::min(d->size(), fs.handles[h].pos));
        std::memcpy(data, d->data() + fs.handles[h].pos, n);
        fs.handles[h].pos += n;
        return int(n);
    }
    static int write(void* c, int h, const Byte* data, U32 bytes) {
        MemFs& fs = of(c);
        if (fs.fail_write && fs.now == fs.fail_write) { fs.injected = true; return -5; }
        if (h < 0 || size_t(h) >= fs.handles.size() || !fs.handles[h].open || !fs.handles[h].write) return -9;
        Bytes& d = fs.written[fs.handles[h].path];
        d.insert(d.end(), data, data + bytes);
        return int(bytes);
    }
    static int close(void* c, int h) {
        MemFs& fs = of(c);
        if (h < 0 || size_t(h) >= fs.handles.size() || !fs.handles[h].open) return -9;
        fs.handles[h].open = false;
        return 0;
    }
    static int rename(void* c, const char* from, const char* to) {
        MemFs& fs = of(c);
        auto it = fs.written.find(from);
        if (it == fs.written.end() || fs.written.count(to)) return -1;
        Bytes d;
        d.swap(it->second);
        fs.written.erase(it);
        fs.written[to].swap(d);
        return 0;
    }
    static int remove(void* c, const char* path) { return of(c).written.erase(path) ? 0 : -1; }
    static int mkdir(void* c, const char* path) {   // a write fault fails it too
        MemFs& fs = of(c);
        if (fs.fail_write && fs.now == fs.fail_write) { fs.injected = true; return -5; }
        if (!fs.parent_made(path)) return -1;
        fs.dirs.insert(path);
        return 0;
    }
    unsigned open_handles() const { unsigned n = 0; for (auto& h : handles) n += h.open; return n; }
};
const char* const job_outputs[] = {"/out/os.ldr", "/out/content/op1_factory.json", "/out/files.sha256"};

struct JobRun {
    mod_job::Error error = mod_job::Error::none;
    mod_job::Phase phase = mod_job::Phase::idle;
    unsigned steps = 0;
    U32 peak = 0, status = 0;
    std::string detail;
    std::map<std::string, U32> largest;            // bytes of work, per phase
    std::map<std::string, double> longest_ms;      // host time, per phase
};
struct JobSetup {
    std::vector<const char*> machines, extensions;
    mod_job::Input in{};
    mod_job::Files files{};
    std::vector<U32> region;
    JobSetup(const Job& job, MemFs& fs) {
        for (auto& m : job.machines) machines.push_back(m.c_str());
        for (auto& e : job.extensions) extensions.push_back(e.c_str());
        in.selection.machines = machines.data(); in.selection.machine_count = U32(machines.size());
        in.selection.extensions = extensions.data(); in.selection.extension_count = U32(extensions.size());
        in.selection.layout = job.layout.data(); in.selection.layout_count = U32(job.layout.size());
        in.selection.has_layout = job.has_layout;
        in.selection.expanded = job.expanded;
        in.generation = job.generation.c_str(); in.stock = job.stock.c_str(); in.catalog = job.catalog.c_str();
        in.official = job.official.c_str(); in.out = "/out"; in.name = job.name.c_str(); in.policy = &cold;
        in.profiling = job.profiling; in.unverified = job.unverified;
        Bytes store;
        in.flat = slurp(job.generation + "/store.txt", store);   // the device's store (a copy of it)
        files = {&fs, MemFs::probe_metadata, MemFs::size, MemFs::open, MemFs::read, MemFs::write, MemFs::close, MemFs::rename, MemFs::remove,
                 MemFs::mkdir};
        region.assign((8u << 20) / 4, 0);
    }
};
// One job: start, then step (cancelled after `cancel_after` steps when nonzero; `hook` before each step)
// until it ends.
JobRun run_job(JobSetup& s, MemFs& fs, mod_job::Job& job, U32 region_bytes, unsigned cancel_after = 0,
               const std::function<void(mod_job::Job&)>& hook = nullptr) {
    std::memset(s.region.data(), 0xa5, s.region.size() * 4);     // the device's region is not cleared
    JobRun r;
    r.error = mod_job::start(job, s.in, s.files, reinterpret_cast<Byte*>(s.region.data()), region_bytes);
    if (r.error == mod_job::Error::none) {
        for (;;) {
            if (cancel_after && job.steps == cancel_after) { mod_job::cancel(job); break; }
            if (hook) hook(job);
            const std::string phase = mod_job::phase_name(job.phase);
            fs.now = job.steps + 1;
            const auto a = std::chrono::steady_clock::now();
            const mod_job::Phase p = mod_job::step(job);
            const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - a).count();
            r.largest[phase] = std::max(r.largest[phase], job.work);
            r.longest_ms[phase] = std::max(r.longest_ms[phase], ms);
            if (p == mod_job::Phase::done || p == mod_job::Phase::failed || p == mod_job::Phase::cancelled) break;
        }
        r.error = job.error;
    }
    r.phase = job.phase; r.steps = job.steps; r.peak = job.peak; r.status = job.status; r.detail = job.detail;
    return r;
}

template <class M> std::string json_map(const M& m, const char* format) {
    std::string out = "{";
    char row[96];
    for (auto& kv : m) {
        std::snprintf(row, sizeof row, format, out.size() > 1 ? ", " : "", kv.first.c_str(), kv.second);
        out += row;
    }
    return out + "}";
}

int job_run(const Job& job, const std::string& outdir) {
    Inputs in;
    static Assembly a;
    static mod_patch::Patcher p;
    static mod_content::Builder b;
    const bool loaded = load(job, in);                 // the one-shot reference (absent on a refusal test)
    if (loaded) assemble(job, in, a, p, b);
    const bool reference = loaded && a.input == "ok" && a.link == mod_link::Status::ok && a.patch == mod_patch::Status::ok &&
                           a.content == mod_content::Status::ok;
    static MemFs fs;
    JobSetup s(job, fs);
    static mod_job::Job mj;
    const JobRun r = run_job(s, fs, mj, U32(s.region.size() * 4));
    auto same = [&](const char* path, const Byte* want, size_t n) {
        auto it = fs.written.find(path);
        return it != fs.written.end() && it->second.size() == n && !std::memcmp(it->second.data(), want, n);
    };
    const bool ldr = reference && same(job_outputs[0], a.image.data(), p.total);
    const bool catalog = reference && same(job_outputs[1], a.out.data(), b.catalog_bytes);
    auto it = fs.written.find(job_outputs[2]);           // content list, then info.txt, os.ldr
    const bool list = reference && it != fs.written.end() && it->second.size() >= b.list_bytes &&
                      !std::memcmp(it->second.data(), a.list.data(), b.list_bytes);
    std::vector<std::string> unused;
    for (auto& w : fs.written) write_file(outdir + "/" + w.first.substr(5), w.second.data(), w.second.size(), unused);
    std::printf("{\"error\": \"%s\", \"phase\": \"%s\", \"status\": %u, \"detail\": %s, \"reference\": %s, "
                "\"ldr_identical\": %s, \"catalog_identical\": %s, \"list_identical\": %s, \"files\": %zu, "
                "\"steps\": %u, \"peak\": %u, \"region\": %zu, \"job_state\": %zu, \"bytes\": %u, "
                "\"largest\": %s, \"longest_ms\": %s, \"open_handles\": %u}\n",
                mod_job::error_name(r.error), mod_job::phase_name(r.phase), r.status, quoted(r.detail.c_str()).c_str(),
                reference ? "true" : "false", ldr ? "true" : "false", catalog ? "true" : "false",
                list ? "true" : "false", fs.written.size(), r.steps, r.peak, s.region.size() * 4,
                sizeof(mod_job::Job), p.total, json_map(r.largest, "%s\"%s\": %u").c_str(),
                json_map(r.longest_ms, "%s\"%s\": %.3f").c_str(), fs.open_handles());
    return 0;
}

int job_faults(const Job& job, unsigned stride, unsigned k0) {
    static MemFs fs;
    JobSetup s(job, fs);
    static mod_job::Job mj;
    const U32 full = U32(s.region.size() * 4);
    std::vector<std::string> violations;
    std::map<std::string, unsigned> counts;
    const JobRun base = run_job(s, fs, mj, full);
    if (base.phase != mod_job::Phase::done) {
        std::printf("{\"error\": \"baseline %s %s\"}\n", mod_job::error_name(base.error), base.detail.c_str());
        return 1;
    }
    const std::map<std::string, Bytes> want = fs.written;
    auto clean = [&](const std::string& what, const JobRun& r, mod_job::Error expect) {
        bool left = false;
        for (auto& w : fs.written) left = left || w.first.compare(0, 5, "/out/") == 0;
        if (r.phase != (expect == mod_job::Error::cancelled ? mod_job::Phase::cancelled : mod_job::Phase::failed) ||
            r.error != expect || left || fs.open_handles())
            violations.push_back(what + ": " + mod_job::error_name(r.error) + " " + mod_job::phase_name(r.phase) +
                                 (left ? " output left" : "") + (fs.open_handles() ? " handle open" : "") + " " + r.detail);
    };
    auto identical = [&](const std::string& what, const JobRun& r) {
        if (r.phase != mod_job::Phase::done || fs.written != want || fs.open_handles())
            violations.push_back(what + ": not the baseline (" + mod_job::error_name(r.error) + " " + r.detail + ")");
    };
    for (unsigned k = k0; k <= base.steps; k += stride) {
        for (int mode = 0; mode != 2; ++mode) {
            fs.reset();
            (mode ? fs.fail_write : fs.fail_read) = k;
            const JobRun r = run_job(s, fs, mj, full);
            const std::string what = std::string(mode ? "write" : "read") + " fault at step " + std::to_string(k);
            if (fs.injected) { clean(what, r, mode ? mod_job::Error::write : mod_job::Error::read); ++counts[mode ? "write" : "read"]; }
            else identical(what, r);
        }
        fs.reset();
        const JobRun r = run_job(s, fs, mj, full, k);
        if (k < base.steps) { clean("cancel after step " + std::to_string(k), r, mod_job::Error::cancelled); ++counts["cancel"]; }
    }
    // A region one byte short of the peak, and the peak itself.
    fs.reset();
    clean("region peak - 1", run_job(s, fs, mj, base.peak - 1), mod_job::Error::region);
    fs.reset();
    clean("region 4 KiB", run_job(s, fs, mj, 4096), mod_job::Error::region);
    fs.reset();
    identical("region peak", run_job(s, fs, mj, (base.peak + 3) & ~3u));
    // Missing, truncated and short files.
    const std::string first = job.generation + "/" + job.machines[0] + "/";
    Bytes list;
    slurp(first + "files.sha256", list);
    std::string object, content;
    {
        std::istringstream lines(std::string(list.begin(), list.end()));
        std::string line;
        while (std::getline(lines, line)) {
            if (object.empty() && line.size() > 66 && !line.compare(66, 8, "objects/")) object = first + line.substr(66);
            if (content.empty() && line.size() > 66 && !line.compare(66, 8, "content/")) content = first + line.substr(66);
        }
    }
    struct Case { const char* name; std::function<void()> change; mod_job::Error expect; };
    std::vector<Case> cases = {
        {"missing object", [&] { fs.missing.insert(object); }, mod_job::Error::open},
        {"missing package", [&] { fs.missing.insert(first + "files.sha256"); }, mod_job::Error::open},
        {"missing base edits.bin", [&] { fs.missing.insert(job.generation + "/base/edits.bin"); }, mod_job::Error::open},
        {"missing stock", [&] { fs.missing.insert(job.stock); }, mod_job::Error::open},
        {"truncated object", [&] { Bytes d; slurp(object, d); d.resize(d.size() / 2); fs.replaced[object] = d; }, mod_job::Error::sha256},
        {"truncated files.sha256", [&] { Bytes d = list; d.resize(d.size() - 10); fs.replaced[first + "files.sha256"] = d; }, mod_job::Error::list},
        {"object shorter than its size", [&] { fs.short_size.insert(object); }, mod_job::Error::truncated},
    };
    if (!content.empty()) {                            // a content file the entry copies
        cases.push_back({"missing content file", [&] { fs.missing.insert(content); }, mod_job::Error::open});
        cases.push_back({"truncated content file", [&] { Bytes d; slurp(content, d); d.resize(d.size() / 2); fs.replaced[content] = d; },
                         mod_job::Error::sha256});
    }
    for (auto& c : cases) {
        fs.reset();
        c.change();
        const JobRun r = run_job(s, fs, mj, full);
        clean(c.name, r, c.expect);
        counts[std::string("case ") + c.name] = unsigned(r.error);
    }
    // os.ldr damaged after it was written, its recorded SHA-256 made to match so the read-back passes:
    // the job must refuse it (190: the recorded SHA-256 differs from the patch's own, which the restore
    // check vouched for; start_verify no longer hashes the read-back again), and no os.ldr is left.
    fs.reset();
    bool tampered = false;
    const JobRun image = run_job(s, fs, mj, full, 0, [&](mod_job::Job& j) {
        if (tampered || j.phase != mod_job::Phase::verify || j.out_index != j.out_count - 1 || j.load.active || j.load.ready) return;
        Bytes& ldr = fs.written["/out/os.ldr.part"];
        ldr[ldr.size() / 4] ^= 0x40;
        j.ldr_sha256 = digest(ldr.data(), ldr.size());
        tampered = true;
    });
    clean("tampered os.ldr", image, mod_job::Error::image);
    if (!tampered) violations.push_back("tampered os.ldr: never reached");
    counts["case tampered os.ldr"] = image.status;
    fs.reset();
    identical("reuse after every fault", run_job(s, fs, mj, full));
    std::string v = "[";
    for (auto& x : violations) v += (v.size() > 1 ? ", " : "") + quoted(x.c_str());
    std::printf("{\"steps\": %u, \"stride\": %u, \"peak\": %u, \"counts\": %s, \"violations\": %s}\n", base.steps, stride,
                base.peak, json_map(counts, "%s\"%s\": %u").c_str(), (v + "]").c_str());
    return 0;
}

// The job fed damaged copies of the files it reads (the files.sha256 line, and for the stock or catalog
// the base's needs_os / catalog_sha256 pin and its line, refreshed in 3 of 4 rounds so the damage gets
// past the hash checks). Every round must end done or failed with no output left and no handle open,
// within a step cap; ASan/UBSan (the sanitizer build) catch the rest. Stops after `seconds` of host time.
void relist(MemFs& fs, const std::string& path, const Bytes& data) {
    for (size_t cut = path.rfind('/'); cut != std::string::npos && cut; cut = path.rfind('/', cut - 1)) {
        const std::string list = path.substr(0, cut) + "/files.sha256", rel = path.substr(cut + 1);
        const Bytes* old = fs.find(list);
        if (!old || list == path) continue;
        std::string text(old->begin(), old->end()), out;
        std::istringstream lines(text);
        std::string line;
        bool hit = false;
        while (std::getline(lines, line)) {
            if (line.size() == rel.size() + 66 && !line.compare(66, std::string::npos, rel)) {
                line = hex(digest(data.data(), data.size())) + line.substr(64);
                hit = true;
            }
            out += line + "\n";
        }
        if (!hit) continue;
        fs.replaced[list] = Bytes(out.begin(), out.end());
        return;
    }
}
void repin(MemFs& fs, const std::string& info, const char* key, const Bytes& data) {
    const Bytes* old = fs.find(info);
    if (!old) return;
    std::string text(old->begin(), old->end()), out, line;
    std::istringstream lines(text);
    while (std::getline(lines, line))
        out += (line.compare(0, std::strlen(key) + 1, std::string(key) + "=") ? line
                : std::string(key) + "=" + hex(digest(data.data(), data.size()))) + "\n";
    fs.replaced[info] = Bytes(out.begin(), out.end());
    relist(fs, info, fs.replaced[info]);
}
int job_fuzz(const Job& job, unsigned seed, unsigned count, double seconds) {
    static MemFs fs;
    JobSetup s(job, fs);
    static mod_job::Job mj;
    const U32 full = U32(s.region.size() * 4);
    const JobRun base = run_job(s, fs, mj, full);
    if (base.phase != mod_job::Phase::done) {
        std::printf("{\"error\": \"baseline %s %s\"}\n", mod_job::error_name(base.error), base.detail.c_str());
        return 1;
    }
    std::vector<std::string> paths;
    for (auto& c : fs.cache) paths.push_back(c.first);
    std::mt19937 rng(seed);
    std::map<std::string, unsigned> counts;
    std::vector<std::string> violations;
    const unsigned cap = base.steps * 4 + 1000;
    U32 largest = 0;
    unsigned rounds = 0;
    const auto t0 = std::chrono::steady_clock::now();
    for (; rounds != count; ++rounds) {
        if (std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count() > seconds) break;
        fs.reset();
        const std::string path = paths[rng() % paths.size()];
        Bytes d = *fs.find(path);
        damage(d, rng);
        if (rng() % 4 == 1 && d.size() > 4) d.resize(d.size() + 4096 * (1 + rng() % 64), Byte(rng()));
        fs.replaced[path] = d;
        if (rng() % 4) {
            relist(fs, path, d);
            const std::string info = job.generation + "/base/info.txt";
            if (path == job.stock) repin(fs, info, "needs_os", d);
            if (path == job.catalog) repin(fs, info, "catalog_sha256", d);
        }
        std::string what = path.substr(path.rfind('/') + 1);
        bool over = false;
        const JobRun r = run_job(s, fs, mj, full, 0, [&](mod_job::Job& j) {
            if (j.steps > cap && !over) { over = true; mod_job::cancel(j); }
        });
        for (auto& l : r.largest) largest = std::max(largest, l.second);
        bool left = false;
        for (auto& w : fs.written) left = left || (r.phase != mod_job::Phase::done && w.first.compare(0, 5, "/out/") == 0);
        if (over || left || fs.open_handles() ||
            (r.phase != mod_job::Phase::done && r.phase != mod_job::Phase::failed))
            violations.push_back("round " + std::to_string(rounds) + " " + what + ": " + mod_job::phase_name(r.phase) +
                                 " " + mod_job::error_name(r.error) + (over ? " step cap" : "") +
                                 (left ? " output left" : "") + (fs.open_handles() ? " handle open" : ""));
        ++counts[std::string(mod_job::phase_name(r.phase)) + " " + mod_job::error_name(r.error)];
    }
    std::string v = "[";
    for (size_t i = 0; i < violations.size() && i < 20; ++i) v += (v.size() > 1 ? ", " : "") + quoted(violations[i].c_str());
    std::printf("{\"rounds\": %u, \"files\": %zu, \"largest\": %u, \"counts\": %s, \"violations\": %s}\n", rounds,
                paths.size(), largest, json_map(counts, "%s\"%s\": %u").c_str(), (v + "]").c_str());
    return 0;
}
}

int main(int argc, char** argv) {
    Job job;
    if (argc >= 4 && !std::strcmp(argv[1], "run") && read_job(argv[2], job)) return run(job, argv[3]);
    if (argc >= 4 && !std::strcmp(argv[1], "verify") && read_job(argv[2], job)) return verify_file(job, argv[3]);
    if (argc >= 5 && !std::strcmp(argv[1], "fuzz") && read_job(argv[2], job))
        return fuzz(job, unsigned(std::strtoul(argv[3], 0, 10)), unsigned(std::strtoul(argv[4], 0, 10)));
    if (argc >= 4 && !std::strcmp(argv[1], "job") && read_job(argv[2], job)) return job_run(job, argv[3]);
    if (argc >= 6 && !std::strcmp(argv[1], "jobfuzz") && read_job(argv[2], job))
        return job_fuzz(job, unsigned(std::strtoul(argv[3], 0, 10)), unsigned(std::strtoul(argv[4], 0, 10)),
                        std::strtod(argv[5], 0));
    if (argc >= 3 && !std::strcmp(argv[1], "faults") && read_job(argv[2], job))
        return job_faults(job, argc > 3 ? std::max(1ul, std::strtoul(argv[3], 0, 10)) : 1,
                          argc > 4 ? std::max(1ul, std::strtoul(argv[4], 0, 10)) : 1);
    std::fprintf(stderr, "usage: mod_assemble run JOB OUT | verify JOB IMAGE | fuzz JOB SEED N | job JOB OUT | faults JOB [STRIDE [K]] | jobfuzz JOB SEED N SECONDS\n");
    return 2;
}

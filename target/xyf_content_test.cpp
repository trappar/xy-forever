// Host test for the content switch: the path rule, the record's head, and the job over an in-memory
// file system with a fault, a power cut or A going silent at every request.
#include "xyf_content_job.hpp"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <map>
#include <set>
#include <string>
#include <vector>
using namespace xyf::content;
static unsigned checks;
static void require(bool ok, int line) { ++checks; if (!ok) { fprintf(stderr, "check %u (line %d) failed\n", checks, line); abort(); } }
#define REQUIRE(x) require((x), __LINE__)

static std::string hex(const std::string& data) {
    xyf_core::hash::Sha256 h;
    xyf_core::hash::Digest d;
    h.update(reinterpret_cast<const uint8_t*>(data.data()), uint32_t(data.size()));
    h.finish(d);
    static const char digits[] = "0123456789abcdef";
    std::string out;
    for (int i = 0; i != 32; ++i) { out += digits[d.bytes[i] >> 4]; out += digits[d.bytes[i] & 15]; }
    return out;
}
typedef std::map<std::string, std::string> Files;
struct Tree { Files files; std::set<std::string> dirs; bool operator==(const Tree& o) const { return files == o.files && dirs == o.dirs; } };

struct FakeIo {
    Tree t;
    struct Handle { std::string path; size_t pos; bool write; };
    std::map<int, Handle> open_;
    int next = 3;
    long requests = 0, fail_at = -1, cut_at = -1, silent_at = -1;
    bool silent = false, cut = false;
    std::vector<std::string> written;                      // every path written, renamed onto, removed or made
    std::vector<std::string> reads;                        // every path opened for reading
    bool request() {                                       // false: this request fails
        ++requests;
        if (requests == silent_at) silent = true;
        if (requests == cut_at) cut = true;
        return !silent && !cut && requests != fail_at;
    }
    static std::string parent(const std::string& p) { return p.substr(0, p.rfind('/')); }
    bool folder(const std::string& p) const { return p == "/yaffs2" || t.dirs.count(p); }
    int open(const char* path, bool write) {
        if (!request()) return -1;
        std::string p(path);
        if (write) {
            if (!folder(parent(p)) || t.dirs.count(p)) return -1;
            written.push_back(p);
            t.files[p] = "";
        } else if (reads.push_back(p), !t.files.count(p)) return -2;
        open_[next] = Handle{p, 0, write};
        return next++;
    }
    int read(int fd, uint8_t* out, uint32_t n) {
        if (!request() || !open_.count(fd)) return -1;
        Handle& h = open_[fd];
        const std::string& d = t.files[h.path];
        const size_t k = std::min<size_t>(n, d.size() - h.pos);
        memcpy(out, d.data() + h.pos, k); h.pos += k;
        return int(k);
    }
    int write(int fd, const uint8_t* in, uint32_t n) {
        if (!request() || !open_.count(fd)) return -1;
        t.files[open_[fd].path].append(reinterpret_cast<const char*>(in), n);
        return int(n);
    }
    int close(int fd) { if (!request()) return -1; open_.erase(fd); return 0; }
    int unlink(const char* path) {
        if (!request()) return -1;
        written.push_back(path);
        return t.files.erase(path) ? 0 : 1;
    }
    bool empty(const std::string& p) const {
        for (const auto& f : t.files) if (f.first.compare(0, p.size() + 1, p + "/") == 0) return false;
        for (const auto& d : t.dirs) if (d.compare(0, p.size() + 1, p + "/") == 0) return false;
        return true;
    }
    int rmdir(const char* path) {
        if (!request()) return -1;
        written.push_back(path);
        if (!t.dirs.count(path)) return 0;
        if (!empty(path)) return 1;
        t.dirs.erase(path);
        return 0;
    }
    int mkdir(const char* path) {
        if (!request()) return -1;
        written.push_back(path);
        if (t.files.count(path) || !folder(parent(path))) return -1;
        t.dirs.insert(path);
        return 0;
    }
    int rename(const char* from, const char* to) {
        if (!request()) return -1;
        written.push_back(to);
        if (!t.files.count(from) || t.dirs.count(to) || t.files.count(to)) return -1;   // as A's engine: never replaces
        t.files[to] = t.files[from];
        t.files.erase(from);
        return 0;
    }
    bool lost() const { return silent; }
};

static const std::string C = "/yaffs2/content/", F = "/yaffs2/xy-forever/firmware/";
static std::string list_text(const Files& files) {         // shasum lines, sorted (map order = bytewise)
    std::string out;
    for (const auto& f : files) out += hex(f.second) + "  " + f.first + "\n";
    return out;
}
static const Files official_files = {
    {"display/a.svg", "<svg a/>"}, {"op1_factory.json", "{\"official\": 1}"}, {"presets/p/x.preset", "preset x"},
    {"samples/s.wav", std::string(3000, 's')}};
// An assembled mod set: content files (op1_factory.json among them), info.txt with generation.
static void add_mods(Tree& t, const std::string& id, const Files& content) {
    const std::string e = F + id;
    t.dirs.insert(e);
    Files listed;
    for (const auto& f : content) {
        std::string p = e + "/content/" + f.first;
        for (size_t k = (e + "/content").size(); (k = p.find('/', k + 1)) != std::string::npos;) t.dirs.insert(p.substr(0, k));
        t.dirs.insert(e + "/content");
        t.files[p] = f.second;
        listed["content/" + f.first] = f.second;
    }
    listed["info.txt"] = "type=firmware\nname=" + id + "\nkind=open-xy\ngeneration=0.0.6-test\n";
    listed["os.ldr"] = "LDR";
    t.files[e + "/info.txt"] = listed["info.txt"];
    t.files[e + "/os.ldr"] = "LDR";
    t.files[e + "/files.sha256"] = list_text(listed);
}
static Tree seed() {
    Tree t;
    for (const char* d : {"/yaffs2/content", "/yaffs2/content/display", "/yaffs2/content/presets", "/yaffs2/content/presets/p",
                          "/yaffs2/content/samples", "/yaffs2/content/dlc", "/yaffs2/xy-forever", "/yaffs2/xy-forever/firmware",
                          "/yaffs2/xy-forever/firmware/opxy-121", "/yaffs2/xy-forever/firmware/opxy-121-stock", "/yaffs2/user"})
        t.dirs.insert(d);
    for (const auto& f : official_files) t.files[C + f.first] = f.second;
    t.files[C + "dlc/pack.bin"] = "dlc";
    t.files[C + "stray.txt"] = "unknown";
    t.files["/yaffs2/user/project.xy"] = "user";
    t.files[F + "opxy-121/os.ldr"] = "LDR";
    t.files[F + "opxy-121/info.txt"] = "name=OP-XY 1.2.1\nkind=official\n";
    t.files[F + "opxy-121-stock/os.ldr"] = "LDR";
    t.files[F + "opxy-121-stock/content.sha256"] = list_text(official_files);
    add_mods(t, "mods-a", {{"op1_factory.json", "{\"mods\": \"a\"}"}, {"display/alpha.svg", "<svg alpha/>"},
                           {"display/common.svg", "<svg common/>"}, {"presets/alpha/1.preset", "a1"},
                           {"presets/alpha/2.preset", std::string(2500, 'a')}, {"aux_presets/alpha/x", "ax"}});
    add_mods(t, "mods-b", {{"op1_factory.json", "{\"mods\": \"b\"}"}, {"display/beta.svg", "<svg beta/>"},
                           {"display/common.svg", "<svg common/>"}, {"presets/beta/1.preset", "b1"}});
    return t;
}
static std::vector<uint8_t> work(work_needed + 0x10000);
static const char* const candidates[3] = {"mods-a", "opxy-121", "opxy-121-stock"};
static Job job;
// Runs a switch to target (from the beginning: a new job, as after a restart). Returns the final stage.
static uint32_t run(FakeIo& io, const char* target) {
    REQUIRE(start(job, target, candidates, 3, work.data(), uint32_t(work.size())));
    for (long guard = 0; guard != 100000 && step(job, io) < phase_done && !io.cut; ++guard) {}
    return io.cut ? st_failed : job.stage;
}
static std::string record(const FakeIo& io) {
    auto f = io.t.files.find(record_path);
    return f == io.t.files.end() ? std::string("(none)") : f->second;
}
static State state_of(const FakeIo& io) {
    State s; s.kind = official; s.entry[0] = 0;
    auto f = io.t.files.find(record_path);
    if (f != io.t.files.end()) parse(reinterpret_cast<const uint8_t*>(f->second.data()), uint32_t(f->second.size()), s);
    else if (io.t.files.count(record_old)) s.kind = switching;     // as read_state
    return s;
}
// What content/ and the record must be after a finished switch to target (from seed()).
static Tree expected(const Tree& base, const char* target) {
    Tree t = base;
    for (auto it = t.files.begin(); it != t.files.end();) {   // drop every non-official file XY Forever could install
        const std::string& p = it->first;
        const bool content = p.compare(0, C.size(), C) == 0;
        const std::string rel = content ? p.substr(C.size()) : "";
        if (content && !official_files.count(rel) && rel.compare(0, 4, "dlc/") && rel != "stray.txt") it = t.files.erase(it);
        else ++it;
    }
    for (const char* d : {"/yaffs2/content/presets/alpha", "/yaffs2/content/presets/beta", "/yaffs2/content/aux_presets",
                          "/yaffs2/content/aux_presets/alpha"}) t.dirs.erase(d);
    std::string rec = "state=official\n";
    if (base.files.count(F + target + "/files.sha256")) {
        rec = std::string("state=installed\nentry=") + target + "\n";
        const std::string e = F + target + "/content/";
        for (const auto& f : base.files)
            if (f.first.compare(0, e.size(), e) == 0) {
                const std::string rel = f.first.substr(e.size());
                t.files[C + rel] = f.second;
                if (rel != "op1_factory.json") rec += "file=" + rel + "\t" + hex(f.second) + "\n";
                for (size_t k = 0; (k = rel.find('/', k + 1)) != std::string::npos;) t.dirs.insert(C + rel.substr(0, k));
            }
    }
    t.files[record_path] = rec;
    t.dirs.insert(saved_folder);
    t.files[saved_list] = list_text(official_files);
    t.files[saved_catalog] = official_files.at("op1_factory.json");
    t.files[std::string(xyf::blob::root) + "/" + hex(official_files.at("op1_factory.json"))] = official_files.at("op1_factory.json");
    t.dirs.insert(xyf::blob::root); t.dirs.insert("/yaffs2/xy-forever/tmp");
    return t;
}
static bool same_tree(const Tree& a, const Tree& b, const char* what, long n) {   // equal, else the differences
    if (a == b) return true;
    fprintf(stderr, "%s (request %ld):\n", what, n);
    for (const auto& f : a.files) if (!b.files.count(f.first) || b.files.at(f.first) != f.second) fprintf(stderr, "  got %s\n", f.first.c_str());
    for (const auto& f : b.files) if (!a.files.count(f.first)) fprintf(stderr, "  missing %s\n", f.first.c_str());
    for (const auto& d : a.dirs) if (!b.dirs.count(d)) fprintf(stderr, "  extra folder %s\n", d.c_str());
    for (const auto& d : b.dirs) if (!a.dirs.count(d)) fprintf(stderr, "  missing folder %s\n", d.c_str());
    return false;
}
static bool only_allowed_writes(const FakeIo& io) {
    for (const std::string& p : io.written) {
        const bool ok = allowed(Use::blob, p.c_str(), false) ||
            (p.compare(0, C.size(), C) == 0 ? p.compare(0, C.size() + 4, C + "dlc/") != 0
              : p.compare(0, 35, "/yaffs2/xy-forever/content-official") == 0 ||
                p.compare(0, 33, "/yaffs2/xy-forever/content-state.") == 0);
        if (!ok) { fprintf(stderr, "write outside the rule: %s\n", p.c_str()); return false; }
        const std::string rel = p.compare(0, C.size(), C) == 0 ? p.substr(C.size()) : "";
        std::string base = rel.size() > 5 && rel.compare(rel.size() - 5, 5, ".part") == 0 ? rel.substr(0, rel.size() - 5) : rel;
        if (!rel.empty() && base != "op1_factory.json" && official_files.count(base)) { fprintf(stderr, "official: %s\n", p.c_str()); return false; }
        if (rel == "stray.txt") return false;
    }
    return true;
}

static void path_rule() {
    REQUIRE(allowed(Use::record, record_path, false) && allowed(Use::record, "/yaffs2/xy-forever/content-state.txt.part", false));
    REQUIRE(!allowed(Use::record, "/yaffs2/xy-forever/state-a.txt", false) && !allowed(Use::record, "/yaffs2/xy-forever/content-state.txt.x", false));
    REQUIRE(allowed(Use::record, record_old, false) && !allowed(Use::record, "/yaffs2/xy-forever/content-state.old.part", false));
    REQUIRE(allowed(Use::saved, saved_folder, false) && allowed(Use::saved, saved_list, false) &&
            allowed(Use::saved, "/yaffs2/xy-forever/content-official/op1_factory.json.part", false));
    REQUIRE(!allowed(Use::saved, "/yaffs2/xy-forever/content-official/other", false) && !allowed(Use::saved, record_path, false));
    REQUIRE(allowed(Use::catalog, "/yaffs2/content/op1_factory.json", false) && allowed(Use::catalog, "/yaffs2/content/op1_factory.json.part", false));
    REQUIRE(!allowed(Use::catalog, "/yaffs2/content/op1.json", false) && !allowed(Use::install, "/yaffs2/content/op1_factory.json", false));
    REQUIRE(!allowed(Use::remove_file, "/yaffs2/content/op1_factory.json", false) && !allowed(Use::remove_file, "/yaffs2/content/op1_factory.json.part", false));
    REQUIRE(allowed(Use::install, "/yaffs2/content/display/x.svg", false) && allowed(Use::install, "/yaffs2/content/presets/x/1.preset.part", false));
    REQUIRE(!allowed(Use::install, "/yaffs2/content/display/x.svg", true) && !allowed(Use::remove_file, "/yaffs2/content/display/a.svg", true));
    REQUIRE(allowed(Use::make_folder, "/yaffs2/content/presets", true) && !allowed(Use::remove_folder, "/yaffs2/content/presets", true));
    for (const char* p : {"/yaffs2/content/dlc/x", "/yaffs2/content/dlc", "/yaffs2/content/DLC/x", "/yaffs2/content/Dlc"})
        for (Use u : {Use::install, Use::make_folder, Use::remove_file, Use::remove_folder}) REQUIRE(!allowed(u, p, false));
    REQUIRE(allowed(Use::install, "/yaffs2/content/dlcx/a", false) && allowed(Use::install, "/yaffs2/content/x/dlc/a", false));
    for (const char* p : {"/yaffs2/user/a", "/yaffs2/bootloader.dev", "/yaffs2/content/bootloader.dev", "/yaffs2/content/x/BootLoader",
                          "/yaffs2/content/../user/a", "/yaffs2/content/./a", "/yaffs2/content//a", "/yaffs2/content/", "/yaffs2/content",
                          "/yaffs2/content/a:b", "/yaffs2/content/a\\b", "/yaffs2/content/a\tb", "/content/a", "yaffs2/content/a",
                          "/yaffs2/xy-forever/state-a.txt", "/yaffs2/os.ldr", "/yaffs2/content/a/"})
        for (Use u : {Use::record, Use::saved, Use::catalog, Use::install, Use::make_folder, Use::remove_file, Use::remove_folder})
            REQUIRE(!allowed(u, p, false));
    REQUIRE(!allowed(Use::install, 0, false));
    std::string longest = "/yaffs2/content/" + std::string(239 - 16, 'a');
    REQUIRE(allowed(Use::install, longest.c_str(), false) && !allowed(Use::install, (longest + "a").c_str(), false));
}
static void head() {
    State s;
    const char* texts[] = {"state=official\n", "state=installed\nentry=mods-a\nfile=x\n", "state=switching\ntarget=mods-a\n",
                           "state=installed\nentry=mods-a", "state=installed\nentry=\n", "state=official", "", "state=installed\nentry=a b\n"};
    const uint32_t kinds[] = {official, installed, switching, switching, switching, switching, switching, switching};
    for (int i = 0; i != 8; ++i) {
        parse(reinterpret_cast<const uint8_t*>(texts[i]), uint32_t(strlen(texts[i])), s);
        REQUIRE(s.kind == kinds[i] && (s.kind != installed || !strcmp(s.entry, "mods-a")));
    }
    s.kind = installed; strcpy(s.entry, "mods-a");
    REQUIRE(matches(s, "mods-a", true) && !matches(s, "mods-b", true) && !matches(s, "opxy-121", false));
    s.kind = official;
    REQUIRE(!matches(s, "mods-a", true) && matches(s, "opxy-121", false));
    s.kind = switching;
    REQUIRE(!matches(s, "mods-a", true) && !matches(s, "opxy-121", false));
}
// official -> mods-a -> mods-b -> official, each compared whole.
static void round_trip() {
    FakeIo io;
    io.t = seed();
    const Tree base = io.t;
    REQUIRE(run(io, "mods-a") == st_done && io.t == expected(base, "mods-a") && only_allowed_writes(io));
    REQUIRE(job.done == job.total && job.total == 6 && !strcmp(job.detail, "mods-a"));
    REQUIRE(run(io, "mods-b") == st_done && io.t == expected(base, "mods-b") && only_allowed_writes(io));
    REQUIRE(job.total == 4 + 4);                          // 3 files + catalog, 4 of mods-a's removed (common.svg stays)
    REQUIRE(run(io, "mods-b") == st_done && io.t == expected(base, "mods-b"));   // again: nothing changes
    REQUIRE(run(io, "opxy-121") == st_done && io.t == expected(base, "official") && only_allowed_writes(io));
    Tree back = expected(base, "official");
    REQUIRE(io.t == back && io.open_.empty());
}
// A fault, a power cut and A going silent at every request of mods-a -> mods-b: the record is never absent,
// it says the old entry, switching or the new one; until it says mods-b nothing of mods-b is promised;
// nothing boots while switching; one more switch to mods-b repairs. Also official -> mods-a.
static void faults(const char* from, const char* to) {
    FakeIo io;
    io.t = seed();
    const Tree base = io.t;
    if (strcmp(from, "official")) REQUIRE(run(io, from) == st_done);
    const Tree before = io.t;
    FakeIo count = io;
    count.requests = 0;
    REQUIRE(run(count, to) == st_done);
    const long total = count.requests;
    REQUIRE(total > 20);
    for (int kind = 0; kind != 3; ++kind)
        for (long n = 1; n <= total; ++n) {
            io.t = before; io.written.clear(); io.open_.clear(); io.requests = 0; io.silent = io.cut = false;
            io.fail_at = kind == 0 ? n : -1; io.cut_at = kind == 1 ? n : -1; io.silent_at = kind == 2 ? n : -1;
            const uint32_t end = run(io, to);
            const State s = state_of(io);
            REQUIRE(only_allowed_writes(io));
            const Tree want_end = expected(base, to);
            if (end == st_done) REQUIRE(same_tree(io.t, want_end, kind == 0 ? "fault" : kind == 1 ? "cut" : "silent", n));
            else {
                if (kind == 2) REQUIRE(job.error == e_link);
                if (kind == 0) REQUIRE(job.error != e_link && job.error != e_none);
                const std::string old_record = before.files.count(record_path) ? before.files.at(record_path) : "(none)";
                REQUIRE(record(io) == old_record || s.kind == switching || record(io) == want_end.files.at(record_path));
                if (record(io) == old_record && !io.t.files.count(record_old))   // before the mark: content/ as it was
                    for (const auto& f : before.files) if (!f.first.compare(0, C.size(), C)) REQUIRE(io.t.files.count(f.first) && io.t.files.at(f.first) == f.second);
            }
            io.fail_at = io.cut_at = io.silent_at = -1; io.silent = io.cut = false; io.open_.clear();
            REQUIRE(run(io, to) == st_done);
            Tree want = expected(base, to);
            for (auto& f : io.t.files)                       // a cut's .part may stay (overwritten next time)
                if (!want.files.count(f.first) && f.first.size() > 5 && !f.first.compare(f.first.size() - 5, 5, ".part"))
                    want.files[f.first] = f.second;
            REQUIRE(same_tree(io.t, want, kind == 0 ? "repair after a fault" : kind == 1 ? "repair after a cut" : "repair after silence", n));
        }
}
// Refusals before any change: content not official; an entry replacing an official file; dlc; no catalog.
static void refusals() {
    FakeIo io;
    io.t = seed();
    io.t.files[C + "op1_factory.json"] = "{\"edited\": 1}";
    Tree before = io.t;
    REQUIRE(run(io, "mods-a") == st_failed && job.error == e_content && job.code == r_not_official && io.t == before);
    io.t = seed();
    io.t.files.erase(F + "opxy-121-stock/content.sha256");
    before = io.t;
    REQUIRE(run(io, "mods-a") == st_failed && job.code == r_not_official && io.t == before);
    for (const char* bad : {"samples/s.wav", "dlc/x.bin", "../user/x"}) {
        io.t = seed();
        add_mods(io.t, "mods-a", {{"op1_factory.json", "{}"}, {bad, "evil"}});
        before = io.t;
        REQUIRE(run(io, "mods-a") == st_failed && io.t == before);
        REQUIRE(job.error == (strcmp(bad, "samples/s.wav") ? uint32_t(e_path) : uint32_t(e_content)));
        if (!strcmp(bad, "samples/s.wav")) REQUIRE(job.code == r_official_file && !strcmp(job.detail, "samples/s.wav"));
    }
    io.t = seed();
    add_mods(io.t, "mods-a", {{"display/x.svg", "x"}});
    before = io.t;
    REQUIRE(run(io, "mods-a") == st_failed && job.code == r_no_catalog && io.t == before);
    // A record naming an official file, a dlc file, an unknown path: never removed.
    io.t = seed();
    REQUIRE(run(io, "mods-a") == st_done);
    io.t.files[record_path] += "file=samples/s.wav\nfile=dlc/pack.bin\nfile=op1_factory.json\nfile=../user/project.xy\n";
    REQUIRE(run(io, "opxy-121") == st_done && io.t == expected(seed(), "official") && only_allowed_writes(io));
    // Not the official state and no saved list: refused.
    io.t = seed();
    REQUIRE(run(io, "mods-a") == st_done);
    io.t.files.erase(saved_list);
    before = io.t;
    REQUIRE(run(io, "opxy-121") == st_failed && job.code == r_no_saved && io.t == before);
    // A corrupted entry file: the SHA-256 differs before anything of it is written.
    io.t = seed();
    io.t.files[F + "mods-a/content/display/alpha.svg"] = "<svg tampered/>";
    REQUIRE(run(io, "mods-a") == st_failed && job.error == e_sha256 && state_of(io).kind == switching);
    io.t.files[F + "mods-a/content/display/alpha.svg"] = "<svg alpha/>";
    REQUIRE(run(io, "mods-a") == st_done && io.t == expected(seed(), "mods-a"));
}

// By difference. Writes counted: unchanged files are neither written nor (trusted record) read; one mod
// removed removes only its files; one added writes only its files; a file changed behind our back is repaired
// by switching to the installed entry again (or from an untrusted record).
static size_t count_of(const std::vector<std::string>& v, const std::string& p) { size_t n = 0; for (const auto& x : v) n += x == p; return n; }
static bool content_writes(const FakeIo& io, std::set<std::string> want) {    // the content/ paths written
    std::set<std::string> got;
    for (const auto& p : io.written)
        if (!p.compare(0, C.size(), C) && !io.t.dirs.count(p) && (p.size() < 5 || p.compare(p.size() - 5, 5, ".part"))) got.insert(p.substr(C.size()));
    want.insert("op1_factory.json");
    if (got != want) for (const auto& p : got) fprintf(stderr, "written: %s\n", p.c_str());
    return got == want;
}
static void by_difference() {
    FakeIo io;
    io.t = seed();
    const Files a = {{"op1_factory.json", "{\"mods\": \"a\"}"}, {"display/alpha.svg", "<svg alpha/>"},
                     {"display/common.svg", "<svg common/>"}, {"presets/alpha/1.preset", "a1"},
                     {"presets/alpha/2.preset", std::string(2500, 'a')}, {"aux_presets/alpha/x", "ax"}};
    Files removed = a, added = a;
    removed.erase("aux_presets/alpha/x"); removed["op1_factory.json"] = "{\"mods\": \"a-\"}";
    added["presets/gamma/1.preset"] = "g1"; added["op1_factory.json"] = "{\"mods\": \"a+\"}";
    add_mods(io.t, "mods-c", removed);
    add_mods(io.t, "mods-d", added);
    REQUIRE(run(io, "mods-a") == st_done && job.written == 6 && job.read == 0 && job.compared == 5);
    io.written.clear(); io.reads.clear();
    REQUIRE(run(io, "mods-c") == st_done && content_writes(io, {"aux_presets/alpha/x", "aux_presets/alpha", "aux_presets"}));
    REQUIRE(job.removed == 1 && job.written == 1 && job.read == 0 && job.compared == 4 && job.done == job.total);
    REQUIRE(!count_of(io.reads, C + "display/alpha.svg") && !io.t.files.count(C + "aux_presets/alpha/x"));
    REQUIRE(io.t.files.at(C + "op1_factory.json") == removed.at("op1_factory.json"));
    io.written.clear(); io.reads.clear();
    REQUIRE(run(io, "mods-d") == st_done && content_writes(io, {"aux_presets/alpha/x", "presets/gamma/1.preset"}));
    REQUIRE(job.written == 3 && job.removed == 0 && job.read == 0 && io.t.files.at(C + "presets/gamma/1.preset") == "g1");
    // Changed behind our back: a trusted switch does not read it; switching to the installed entry repairs it.
    io.t.files[C + "display/alpha.svg"] = "<svg changed/>";
    io.t.files.erase(C + "presets/alpha/1.preset");
    io.written.clear(); io.reads.clear();
    REQUIRE(run(io, "mods-d") == st_done && content_writes(io, {"display/alpha.svg", "presets/alpha/1.preset"}));
    REQUIRE(job.read == 5 && job.written == 3 && io.t.files.at(C + "display/alpha.svg") == "<svg alpha/>");
    REQUIRE(io.t.files.at(C + "presets/alpha/1.preset") == "a1");
    // An untrusted record (switching, as after a cut) reads every live file and writes only what differs.
    io.t.files[C + "display/common.svg"] = "<svg changed/>";
    io.t.files[record_path] = "state=switching\ntarget=mods-a\n" + io.t.files[record_path].substr(io.t.files[record_path].find("file="));
    io.written.clear(); io.reads.clear();
    REQUIRE(run(io, "mods-a") == st_done && content_writes(io, {"display/common.svg", "presets/gamma/1.preset", "presets/gamma"}));
    REQUIRE(job.read == 5 && job.removed == 1 && job.written == 2);
    Tree want = expected(io.t, "mods-a");
    REQUIRE(record(io) == want.files.at(record_path));
    // A record whose file= lines are out of order is not trusted (every file read).
    std::string rec = record(io);
    const size_t first = rec.find("file="), second = rec.find("file=", first + 1);
    rec = rec.substr(0, first) + rec.substr(second) + rec.substr(first, second - first);
    io.t.files[record_path] = rec;
    io.reads.clear();
    REQUIRE(run(io, "mods-c") == st_done && job.read == 4 && job.written == 1);
}

static void native_records() {
    FakeIo io; io.t = seed();
    const std::string path = F + "mods-a/manifest.json";
    const std::string native = "{\"schema\":\"xy-forever.package\",\"schema_version\":1,\"type\":\"firmware\","
        "\"name\":\"Native\",\"bytes\":3,\"crc32\":\"00000000\","
        "\"sha256\":\"0000000000000000000000000000000000000000000000000000000000000000\","
        "\"generation\":\"test\",\"needs_os\":\"0000000000000000000000000000000000000000000000000000000000000000\"}";
    io.t.files[path] = native;
    io.t.files.erase(F + "mods-a/info.txt");
    Files listed;
    const std::string prefix = F + "mods-a/";
    for (const auto& f : io.t.files)
        if (!f.first.compare(0, prefix.size(), prefix) && f.first != prefix + "files.sha256")
            listed[f.first.substr(prefix.size())] = f.second;
    io.t.files[prefix + "files.sha256"] = list_text(listed);
    const Tree want = expected(io.t, "mods-a");
    REQUIRE(run(io, "mods-a") == st_done && io.t == want && io.t.files.at(path) == native);
    for (const char* bad : {"", "{", "{}", "{\"schema\":\"xy-forever.package\",\"schema_version\":2}"}) {
        FakeIo refused; refused.t = seed(); refused.t.files[path] = bad;
        const Tree before = refused.t;
        REQUIRE(run(refused, "mods-a") == st_failed && job.error == e_info);
        REQUIRE(refused.written.empty() && refused.t == before); // refusal before any publication
    }
}

int main() {
    path_rule();
    head();
    round_trip();
    faults("official", "mods-a");
    faults("mods-a", "mods-b");
    faults("mods-b", "opxy-121");
    refusals();
    by_difference();
    native_records();
    printf("%u checks passed\n", checks);
    return 0;
}

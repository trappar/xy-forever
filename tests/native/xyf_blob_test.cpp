// target/xyf_blob_gc.hpp: verified blob publication and streaming GC, including every cut boundary.
#include "doctest.h"
#include "xyf_blob_gc.hpp"
#include <algorithm>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <vector>
namespace b = xyf::blob;

namespace {
struct Io {
    struct Handle { std::string path; size_t at; };
    std::map<std::string, std::string> files;
    std::map<int, Handle> handles;
    std::set<std::string> dirs;
    int next = 0;
    int open(const char* p, bool write) {
        if (!write && !files.count(p)) return -2;
        if (write) files[p] = "";
        handles[++next] = {p, 0}; return next;
    }
    int close(int fd) { handles.erase(fd); return 0; }
    int read(int fd, uint8_t* out, uint32_t n) {
        auto& h = handles.at(fd); const auto& s = files.at(h.path);
        const size_t count = std::min<size_t>(n, s.size() - h.at);
        memcpy(out, s.data() + h.at, count); h.at += count; return int(count);
    }
    int write(int fd, const uint8_t* data, uint32_t n) {
        files[handles.at(fd).path].append(reinterpret_cast<const char*>(data), n); return int(n);
    }
    int mkdir(const char* p) { dirs.insert(p); return 0; }
    int unlink(const char* p) { files.erase(p); return 0; }
    int rename(const char* from, const char* to) {
        if (!files.count(from) || files.count(to)) return -1;
        files[to] = files.at(from); files.erase(from); return 0;
    }
    int list_one(const char* folder, uint32_t skip, char* name, uint32_t& kind) {
        std::map<std::string, uint32_t> found;
        const std::string prefix = std::string(folder) + "/";
        for (const auto& f : files) if (!f.first.compare(0, prefix.size(), prefix)) {
            const std::string tail = f.first.substr(prefix.size()); const size_t slash = tail.find('/');
            found[tail.substr(0, slash)] = slash == std::string::npos ? 0 : 1;
        }
        if (skip >= found.size()) return 0;
        auto it = found.begin(); std::advance(it, skip);
        strcpy(name, it->first.c_str()); kind = it->second; return 1;
    }
};
static std::string digest(const std::string& data, uint8_t* out = nullptr) {
    xyf_core::hash::Sha256 h; xyf_core::hash::Digest d;
    h.reset(); h.update(reinterpret_cast<const uint8_t*>(data.data()), uint32_t(data.size())); h.finish(d);
    std::string hex;
    for (int i = 0; i != 32; ++i) {
        if (out) out[i] = d.bytes[i];
        hex += "0123456789abcdef"[d.bytes[i] >> 4]; hex += "0123456789abcdef"[d.bytes[i] & 15];
    }
    return hex;
}
static std::string blob(const std::string& data) { return std::string(b::root) + "/" + digest(data); }
static int copy(Io& io, int stop = -1) {
    b::Copy c; uint8_t expected[32]; digest(io.files.at("/source"), expected);
    REQUIRE(b::begin(c, "/source", "/scratch/blob.part", expected));
    for (int step = 0; step != 1000; ++step) {
        if (step == stop) return step;
        int result = b::step(c, io);
        if (result) return result < 0 ? -1 : step;
    }
    FAIL("no end"); return -1;
}
static int collect(Io& io, int stop = -1) {
    b::Collect g; std::vector<uint8_t> workspace(4096);
    b::collect_begin(g, workspace.data(), uint32_t(workspace.size()));
    for (int step = 0; step != 10000; ++step) {
        if (step == stop) return step;
        int result = b::collect_step(g, io);
        if (result) return result < 0 ? -1 : step;
    }
    FAIL("no end"); return -1;
}
}  // namespace

TEST_CASE("copy, gc, shared references and every cut") {
    Io seed; seed.files["/source"] = std::string(2500, 'x');
    Io completed = seed; const int copies = copy(completed);
    REQUIRE((copies > 10 && completed.files.at(blob(seed.files.at("/source"))) == seed.files.at("/source")));
    for (int cut = 0; cut <= copies; ++cut) {
        Io io = seed; copy(io, cut); io.handles.clear();
        REQUIRE((!io.files.count(blob(seed.files.at("/source"))) || io.files.at(blob(seed.files.at("/source"))) == seed.files.at("/source")));
        REQUIRE((copy(io) >= 0 && io.files.at(blob(seed.files.at("/source"))) == seed.files.at("/source")));
    }
    Io corrupt = seed; corrupt.files[blob(seed.files.at("/source"))] = "wrong";
    REQUIRE(copy(corrupt) == -1); // Never bless an existing blob just because its name matches.
    Io refs;
    const std::string a = "/yaffs2/xy-forever/firmware/a/content.sha256";
    const std::string z = "/yaffs2/xy-forever/firmware/z/files.sha256";
    refs.files[a] = digest("shared") + "  display/shared.svg\n" + digest("old") + "  sounds/old.aif\n";
    refs.files[z] = digest("shared") + "  content/display/shared.svg\n" + digest("metadata") + "  info.txt\n";
    for (const char* data : {"shared", "old", "dead", "metadata"}) refs.files[blob(data)] = data;
    completed = refs; const int steps = collect(completed);
    REQUIRE((steps > 10 && !completed.files.count(blob("metadata")) && !completed.files.count(blob("dead")) && completed.files.count(blob("shared")) && completed.files.count(blob("old"))));
    for (int cut = 0; cut <= steps; ++cut) {
        Io io = refs; collect(io, cut); io.handles.clear();
        REQUIRE((io.files.count(blob("shared")) && io.files.count(blob("old"))));
        REQUIRE((collect(io) >= 0 && io.files.count(blob("shared")) && !io.files.count(blob("dead"))));
    }
    completed.files.erase(a); REQUIRE(collect(completed) >= 0);
    REQUIRE((completed.files.count(blob("shared")) && !completed.files.count(blob("old"))));
    completed.files.erase(z); REQUIRE((collect(completed) >= 0 && !completed.files.count(blob("shared"))));
    { Io io = refs; b::Collect g; uint8_t tiny[32]; b::collect_begin(g, tiny, sizeof(tiny));
      int result = 0; for (int n = 0; n != 1000 && !result; ++n) result = b::collect_step(g, io);
      REQUIRE((result == -1 && io.files.count(blob("dead")) && io.files.count(blob("shared")))); }
    for (const char* invalid : {"version=2\n", "", "bad", "00  ../escape\n"}) {
        Io io = refs; io.files[a] = invalid;
        REQUIRE((collect(io) == -1 && io.files.count(blob("shared")) && io.files.count(blob("old")) && io.files.count(blob("dead"))));
    }
}

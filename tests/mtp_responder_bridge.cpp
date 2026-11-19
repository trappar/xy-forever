// Host-only bridge for tests/test_mtp_responder.py: native/mtp_responder over a temp folder that
// stands for /yaffs2. Frames on stdin: {u8 command, u32 length, payload}; each answer: {u32 length, payload}.
//   R transfer: hand one bulk-OUT transfer (step()s until it is taken)   -> u8 1 taken, 0 stuck
//   I u32 max:  take bulk-IN bytes, step()ing, until max or nothing left  -> the bytes
//   C:          cancel (class request)                                    -> nothing
//   P:          step() until there is nothing to do                       -> nothing
//   S:          u16 device status, u32 open files, u32 handles, u32 most file calls in one step, u32 cache hits,
//               u32 file calls in all
//   X:          invalidate (a change outside MTP)
//   Z u32 bytes: set_piece (#144)                                       -> u32 the piece
//   J u32 max:  as I, but taken as the USB driver takes them (#144): whole 512-byte packets, or the rest of the
//               container at the head; stops when nothing can be taken and nothing is left to do
#include "../native/mtp_responder.hpp"
#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <string>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <unistd.h>
#include <vector>

using namespace xyf_core;
namespace m = xyf_core::mtp;
static std::string root;
static unsigned calls = 0, open_files = 0, total_calls = 0;
static std::string host(const char* path) { return root + (std::strncmp(path, "/yaffs2", 7) == 0 ? path + 7 : "/<bad>"); }
static int fail() {
    switch (errno) {
    case ENOENT: return m::f_not_found;
    case EACCES: case EPERM: return m::f_refused;
    case EEXIST: return m::f_exists;
    case ENOTEMPTY: return m::f_not_empty;
    default: return m::f_storage;
    }
}
static int stat_entry(const std::string& p, m::Entry& e) {
    struct stat st;
    if (::stat(p.c_str(), &st) != 0) return fail();
    e.kind = S_ISDIR(st.st_mode) ? 1 : S_ISREG(st.st_mode) ? 0 : 2;
    e.size = e.kind == 0 ? U32(st.st_size) : 0;
    return 0;
}
static int list(void*, const char* folder, U32 skip, m::Entry* out, U32 room, U32& count, U32& more) {
    ++calls;
    DIR* d = opendir(host(folder).c_str());
    if (!d) return fail();
    std::vector<std::string> names;
    while (dirent* e = readdir(d)) if (std::strcmp(e->d_name, ".") && std::strcmp(e->d_name, "..")) names.push_back(e->d_name);
    closedir(d);
    std::sort(names.begin(), names.end());
    count = 0;
    for (U32 i = skip; i < names.size() && count < room; ++i, ++count) {
        std::snprintf(out[count].name, sizeof(out[count].name), "%s", names[i].c_str());
        stat_entry(host(folder) + "/" + names[i], out[count]);
    }
    more = skip + count < names.size();
    return 0;
}
static int stat_call(void*, const char* path, m::Entry& e) { ++calls; return stat_entry(host(path), e); }
static int space(void*, const char* path, U32& block, U32& blocks, U32& free_blocks) {
    ++calls;
    struct statvfs s;
    if (statvfs(host(path).c_str(), &s) != 0) return fail();
    block = 4096; blocks = 65536; free_blocks = 32768;              // fixed: the test checks the fields
    return 0;
}
static int open_call(void*, const char* path, bool write) {
    ++calls;
    int fd = ::open(host(path).c_str(), write ? O_WRONLY | O_CREAT | O_TRUNC : O_RDONLY, 0644);
    if (fd < 0) return fail();
    ++open_files;
    return fd;
}
static U32 piece_limit = m::default_piece;                            // #144: the largest read or write call (Z)
static int read_call(void*, int fd, Byte* out, U32 n) { ++calls; if (n > piece_limit) return m::f_storage; ssize_t r = ::read(fd, out, n); return r < 0 ? fail() : int(r); }
static int write_call(void*, int fd, const Byte* data, U32 n) { ++calls; if (n > piece_limit) return m::f_storage; ssize_t r = ::write(fd, data, n); return r < 0 ? fail() : int(r); }
static int seek_call(void*, int fd, U32 offset) { ++calls; return lseek(fd, offset, SEEK_SET) < 0 ? fail() : 0; }
static int close_call(void*, int fd) { ++calls; --open_files; return ::close(fd) ? fail() : 0; }
static int mkdir_call(void*, const char* p) { ++calls; return ::mkdir(host(p).c_str(), 0755) ? fail() : 0; }
static int unlink_call(void*, const char* p) { ++calls; return ::unlink(host(p).c_str()) ? fail() : 0; }
static int rmdir_call(void*, const char* p) { ++calls; return ::rmdir(host(p).c_str()) ? fail() : 0; }
static int rename_call(void*, const char* a, const char* b) {         // as A's rename_now: never over a name
    ++calls;
    struct stat st;
    if (!::lstat(host(b).c_str(), &st)) return xyf_core::mtp::f_exists;
    return ::rename(host(a).c_str(), host(b).c_str()) ? fail() : 0;
}

// #4: SendObject's upload, as B's mtp_session: tmp/put-<size>-<tag>, then a rename that replaces (A checks the size).
static std::string upload_path;
static U32 upload_bytes = 0;
static int upload_call(void*, const char* path, U32 size, U32 tag) {   // one call: open_call counts it
    char name[64];
    std::snprintf(name, sizeof name, "/yaffs2/xy-forever/tmp/put-%08x-%08x", unsigned(size), unsigned(tag));
    upload_path = name; upload_bytes = size;
    ::mkdir(host(static_cast<const char*>("/yaffs2/xy-forever")).c_str(), 0755);
    ::mkdir(host(static_cast<const char*>("/yaffs2/xy-forever/tmp")).c_str(), 0755);
    (void)path;
    return open_call(nullptr, name, true);
}
static int commit_call(void*, const char* path, bool keep) {
    ++calls;
    struct stat st;
    if (!keep) return ::unlink(host(upload_path.c_str()).c_str()) ? fail() : 0;
    if (::stat(host(upload_path.c_str()).c_str(), &st) || U32(st.st_size) != upload_bytes) return m::f_storage;
    if (!::stat(host(path).c_str(), &st) && S_ISDIR(st.st_mode)) return m::f_storage;
    return ::rename(host(upload_path.c_str()).c_str(), host(path).c_str()) ? fail() : 0;
}

static m::Responder responder;
static unsigned most = 0;
static bool step() {
    calls = 0;
    bool r = responder.step();
    most = std::max(most, calls);
    total_calls += calls;
    return r;
}
static bool read_all(void* p, size_t n) { return std::fread(p, 1, n, stdin) == n; }
static void answer(const void* p, U32 n) { std::fwrite(&n, 4, 1, stdout); std::fwrite(p, 1, n, stdout); std::fflush(stdout); }
static void put(std::vector<Byte>& v, U32 x, int bytes) { for (int i = 0; i < bytes; ++i) v.push_back(Byte(x >> (8 * i))); }

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    root = argv[1];
    m::FileOps ops = {0, list, stat_call, space, open_call, read_call, write_call, seek_call, close_call,
                      mkdir_call, unlink_call, rmdir_call, rename_call, nullptr, nullptr, upload_call, commit_call};
    responder.init(ops);
    for (;;) {
        Byte command; U32 n;
        if (!read_all(&command, 1) || !read_all(&n, 4)) return 0;
        std::vector<Byte> payload(n);
        if (n && !read_all(payload.data(), n)) return 2;
        if (command == 'R') {
            Byte taken = 0;
            for (unsigned spins = 0; spins < 1000000; ++spins) {
                if (responder.receive(payload.data(), n)) { taken = 1; break; }
                if (!step()) break;
            }
            answer(&taken, 1);
        } else if (command == 'I') {
            U32 max; std::memcpy(&max, payload.data(), 4);
            std::vector<Byte> got;
            Byte buffer[m::buffer_bytes];
            while (got.size() < max) {
                U32 room = std::min<U32>(sizeof(buffer), max - U32(got.size()));
                U32 k = responder.take(buffer, room);
                got.insert(got.end(), buffer, buffer + k);
                if (!k && !step() && !responder.pending()) break;
            }
            answer(got.data(), U32(got.size()));
        } else if (command == 'J') {
            U32 max; std::memcpy(&max, payload.data(), 4);
            std::vector<Byte> got;
            Byte buffer[m::buffer_bytes];
            static U32 left = 0;                                        // of the container at the head
            while (got.size() < max) {
                U32 k = 0;
                const U32 pending = responder.pending();
                if (pending && !left) left = responder.staged_length();
                if (pending && left >= 12) {
                    const U32 n = pending >= left ? left : pending & ~511u;
                    if (n) { k = responder.take(buffer, n); left -= k; }
                }
                got.insert(got.end(), buffer, buffer + k);
                if (!k && !step()) break;
            }
            answer(got.data(), U32(got.size()));
        } else if (command == 'Z') {
            U32 bytes; std::memcpy(&bytes, payload.data(), 4);
            responder.set_piece(bytes);
            const U32 piece = piece_limit = responder.piece_bytes();
            answer(&piece, 4);
        } else if (command == 'C') {
            responder.cancel();
            answer(0, 0);
        } else if (command == 'X') {                                    // speed: a change outside MTP
            responder.invalidate();
            answer(0, 0);
        } else if (command == 'P') {
            while (step()) {}
            answer(0, 0);
        } else if (command == 'S') {
            std::vector<Byte> v;
            put(v, responder.device_status(), 2); put(v, open_files, 4); put(v, responder.handles_used(), 4); put(v, most, 4);
            put(v, responder.cache_hits(), 4); put(v, total_calls, 4);
            answer(v.data(), U32(v.size()));
        } else return 2;
    }
}

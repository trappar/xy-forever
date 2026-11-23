// native/mtp_responder.hpp over a host folder that stands for /yaffs2 (tests/test_mtp_responder.py).
#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <memory>
#include <string>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <tuple>
#include <unistd.h>
#include <vector>
#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/tuple.h>
#include "native/mtp_responder.hpp"

namespace nb = nanobind;
using namespace xyf_core;
namespace m = xyf_core::mtp;

namespace {

// One responder and its storage calls, counted.
struct Bridge {
    std::string root;
    unsigned calls = 0, open_files = 0, total_calls = 0, most = 0;
    U32 piece_limit = m::default_piece;           // #144: the largest read or write call (set_piece)
    std::string upload_path;                      // #4: SendObject's temporary file, as B's mtp_session
    U32 upload_bytes = 0;
    U32 left = 0;                                 // take_packets: of the container at the head
    std::string serial;                           // #288: set_serial's text
    std::unique_ptr<m::Responder> responder{new m::Responder};

    std::string host(const char* path) const {
        return root + (std::strncmp(path, "/yaffs2", 7) == 0 ? path + 7 : "/<bad>");
    }
    bool step() {
        calls = 0;
        const bool r = responder->step();
        most = std::max(most, calls);
        total_calls += calls;
        return r;
    }
};
Bridge& of(void* context) { return *static_cast<Bridge*>(context); }

int fail() {
    switch (errno) {
    case ENOENT: return m::f_not_found;
    case EACCES:
    case EPERM: return m::f_refused;
    case EEXIST: return m::f_exists;
    case ENOTEMPTY: return m::f_not_empty;
    default: return m::f_storage;
    }
}
int stat_entry(const std::string& p, m::Entry& e) {
    struct stat st;
    if (::stat(p.c_str(), &st) != 0) return fail();
    e.kind = S_ISDIR(st.st_mode) ? 1 : S_ISREG(st.st_mode) ? 0 : 2;
    e.size = e.kind == 0 ? U32(st.st_size) : 0;
    return 0;
}
int list(void* c, const char* folder, U32 skip, m::Entry* out, U32 room, U32& count, U32& more) {
    Bridge& b = of(c);
    ++b.calls;
    DIR* d = opendir(b.host(folder).c_str());
    if (!d) return fail();
    std::vector<std::string> names;
    while (dirent* e = readdir(d))
        if (std::strcmp(e->d_name, ".") && std::strcmp(e->d_name, "..")) names.push_back(e->d_name);
    closedir(d);
    std::sort(names.begin(), names.end());
    count = 0;
    for (U32 i = skip; i < names.size() && count < room; ++i, ++count) {
        std::snprintf(out[count].name, sizeof(out[count].name), "%s", names[i].c_str());
        stat_entry(b.host(folder) + "/" + names[i], out[count]);
    }
    more = skip + count < names.size();
    return 0;
}
int stat_call(void* c, const char* path, m::Entry& e) {
    ++of(c).calls;
    return stat_entry(of(c).host(path), e);
}
int space(void* c, const char* path, U32& block, U32& blocks, U32& free_blocks) {
    ++of(c).calls;
    struct statvfs s;
    if (statvfs(of(c).host(path).c_str(), &s) != 0) return fail();
    block = 4096;
    blocks = 65536;
    free_blocks = 32768;              // fixed: the test checks the fields
    return 0;
}
int open_call(void* c, const char* path, bool write) {
    ++of(c).calls;
    int fd = ::open(of(c).host(path).c_str(), write ? O_WRONLY | O_CREAT | O_TRUNC : O_RDONLY, 0644);
    if (fd < 0) return fail();
    ++of(c).open_files;
    return fd;
}
int read_call(void* c, int fd, Byte* out, U32 n) {
    ++of(c).calls;
    if (n > of(c).piece_limit) return m::f_storage;
    ssize_t r = ::read(fd, out, n);
    return r < 0 ? fail() : int(r);
}
int write_call(void* c, int fd, const Byte* data, U32 n) {
    ++of(c).calls;
    if (n > of(c).piece_limit) return m::f_storage;
    ssize_t r = ::write(fd, data, n);
    return r < 0 ? fail() : int(r);
}
int seek_call(void* c, int fd, U32 offset) {
    ++of(c).calls;
    return lseek(fd, offset, SEEK_SET) < 0 ? fail() : 0;
}
int close_call(void* c, int fd) {
    ++of(c).calls;
    --of(c).open_files;
    return ::close(fd) ? fail() : 0;
}
int mkdir_call(void* c, const char* p) {
    ++of(c).calls;
    return ::mkdir(of(c).host(p).c_str(), 0755) ? fail() : 0;
}
int unlink_call(void* c, const char* p) {
    ++of(c).calls;
    return ::unlink(of(c).host(p).c_str()) ? fail() : 0;
}
int rmdir_call(void* c, const char* p) {
    ++of(c).calls;
    return ::rmdir(of(c).host(p).c_str()) ? fail() : 0;
}
int rename_call(void* c, const char* a, const char* b) {             // as A's rename_now: never over a name
    ++of(c).calls;
    struct stat st;
    if (!::lstat(of(c).host(b).c_str(), &st)) return m::f_exists;
    return ::rename(of(c).host(a).c_str(), of(c).host(b).c_str()) ? fail() : 0;
}
// #4: SendObject's upload, as B's mtp_session: tmp/put-<size>-<tag>, then a rename that replaces (A checks the size).
int upload_call(void* c, const char*, U32 size, U32 tag) {          // one call: open_call counts it
    Bridge& b = of(c);
    char name[64];
    std::snprintf(name, sizeof name, "/yaffs2/xy-forever/tmp/put-%08x-%08x", unsigned(size), unsigned(tag));
    b.upload_path = name;
    b.upload_bytes = size;
    ::mkdir(b.host("/yaffs2/xy-forever").c_str(), 0755);
    ::mkdir(b.host("/yaffs2/xy-forever/tmp").c_str(), 0755);
    return open_call(c, name, true);
}
int commit_call(void* c, const char* path, bool keep) {
    Bridge& b = of(c);
    ++b.calls;
    struct stat st;
    if (!keep) return ::unlink(b.host(b.upload_path.c_str()).c_str()) ? fail() : 0;
    if (::stat(b.host(b.upload_path.c_str()).c_str(), &st) || U32(st.st_size) != b.upload_bytes) return m::f_storage;
    if (!::stat(b.host(path).c_str(), &st) && S_ISDIR(st.st_mode)) return m::f_storage;
    return ::rename(b.host(b.upload_path.c_str()).c_str(), b.host(path).c_str()) ? fail() : 0;
}

}  // namespace

void bind_mtp_responder(nb::module_ mod) {
    nb::class_<Bridge>(mod, "Responder")
        // Responder(folder): the folder stands for /yaffs2.
        .def("__init__",
             [](Bridge* b, const std::string& folder) {
                 new (b) Bridge();
                 b->root = folder;
                 const m::FileOps ops = {b,           list,      stat_call,  space,       open_call,   read_call,
                                         write_call,  seek_call, close_call, mkdir_call,  unlink_call, rmdir_call,
                                         rename_call, nullptr,   nullptr,    upload_call, commit_call, nullptr};
                 b->responder->init(ops);
             })
        // receive(transfer) -> taken: hands one bulk-OUT transfer, step()ing until it is taken.
        .def("receive",
             [](Bridge& b, nb::bytes transfer) {
                 const Byte* data = reinterpret_cast<const Byte*>(transfer.c_str());
                 for (unsigned spins = 0; spins < 1000000; ++spins) {
                     if (b.responder->receive(data, static_cast<U32>(transfer.size()))) return true;
                     if (!b.step()) break;
                 }
                 return false;
             })
        // take(most): bulk-IN bytes, step()ing, until most or nothing is left.
        .def("take",
             [](Bridge& b, U32 most) {
                 std::string got;
                 Byte buffer[m::buffer_bytes];
                 while (got.size() < most) {
                     const U32 room = std::min<U32>(sizeof(buffer), most - U32(got.size()));
                     const U32 k = b.responder->take(buffer, room);
                     got.append(reinterpret_cast<const char*>(buffer), k);
                     if (!k && !b.step() && !b.responder->pending()) break;
                 }
                 return nb::bytes(got.data(), got.size());
             })
        // take_packets(most): as take, but taken as the USB driver takes them (#144): whole 512-byte packets, or the
        // rest of the container at the head; stops when nothing can be taken and nothing is left to do.
        .def("take_packets",
             [](Bridge& b, U32 most) {
                 std::string got;
                 Byte buffer[m::buffer_bytes];
                 while (got.size() < most) {
                     U32 k = 0;
                     const U32 pending = b.responder->pending();
                     if (pending && !b.left) b.left = b.responder->staged_length();
                     if (pending && b.left >= 12) {
                         const U32 n = pending >= b.left ? b.left : pending & ~511u;
                         if (n) {
                             k = b.responder->take(buffer, n);
                             b.left -= k;
                         }
                     }
                     got.append(reinterpret_cast<const char*>(buffer), k);
                     if (!k && !b.step()) break;
                 }
                 return nb::bytes(got.data(), got.size());
             })
        // set_piece(bytes) -> the piece the responder took (#144).
        .def("set_piece",
             [](Bridge& b, U32 bytes) {
                 b.responder->set_piece(bytes);
                 return b.piece_limit = b.responder->piece_bytes();
             })
        .def("set_serial",                                                     // #288: DeviceInfo's
             [](Bridge& b, const std::string& text) {
                 b.serial = text;
                 b.responder->set_serial(b.serial.c_str());
             })
        .def("cancel", [](Bridge& b) { b.responder->cancel(); })               // class request
        .def("invalidate", [](Bridge& b) { b.responder->invalidate(); })       // a change outside MTP
        .def("run",
             [](Bridge& b) {
                 while (b.step()) {}
             })                     // step() until there is nothing to do
        // status() -> (device status, open files, handles, most file calls in one step, cache hits, file calls in all).
        .def("status", [](Bridge& b) {
            return std::make_tuple(static_cast<unsigned>(b.responder->device_status()), b.open_files,
                                   b.responder->handles_used(), b.most, b.responder->cache_hits(), b.total_calls);
        });
}

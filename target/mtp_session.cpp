#if defined(XYF_MTP_SESSION)
#include "mtp_session.hpp"
#include "file_client.hpp"
#include "storage_server.hpp"
#include "xyf_clock.hpp"
#include "usb_proto.hpp"
// The responder's FileOps over B's timed file client (mtp_session.hpp). One request per call (list:
// one file.list page, which is opendir + readdirs + stats + closedir, as the serial file.list's page).
// A request past its bound (link) or a lost link sets link: every later call answers f_link at once.
namespace xy_forever { namespace mtp_session {
namespace C = file_client;
namespace P = xyf::usbp;
uint32_t phase_room(const InPhase& p, uint32_t room) {
    const uint32_t left = p.sent < 4 ? 4 - p.sent : p.length > p.sent ? p.length - p.sent : 0;
    return left && left < room ? left : room;
}
bool phase_zlp(InPhase& p, const uint8_t* data, uint32_t n) {
    bool zlp = false;
    for (uint32_t i = 0; i != n;) {
        if (p.sent < 4) {
            p.head[p.sent++] = data[i++];
            if (p.sent == 4) p.length = uint32_t(p.head[0]) | uint32_t(p.head[1]) << 8 | uint32_t(p.head[2]) << 16 |
                                        uint32_t(p.head[3]) << 24;
            continue;
        }
        const uint32_t left = p.length > p.sent ? p.length - p.sent : 0;
        const uint32_t take = n - i < left ? n - i : left;
        p.sent += take; i += take;
        if (p.sent >= p.length) {                               // the container ended
            zlp = (p.length & (packet_bytes - 1)) == 0;
            p.sent = 0; p.length = 0;
        }
    }
    if (p.sent >= 4 && p.sent >= p.length) {                    // a container of only its header (length 4)
        zlp = (p.length & (packet_bytes - 1)) == 0;
        p.sent = 0; p.length = 0;
    }
    return zlp;
}
void files_init(Files& f, volatile uint32_t* mounted, uint32_t (*on_change)()) {
    f.mounted = mounted; f.on_change = on_change;
    f.link = f.window = f.writing = f.last_change_ms = f.requests = f.data_requests = f.data_bytes = 0;
    f.timing = Timing();
    for (uint32_t i = 0; i != max_open; ++i) { f.open[i] = -1; f.open_write[i] = 0; }
}
#if defined(__bfin__) || defined(XYF_SIM)
namespace {
const uint32_t entry_room = 6 + C::max_name;
static_assert(sizeof(Files::page) == M::page * entry_room, "one list page");
Files& of(void* c) { ++static_cast<Files*>(c)->requests; return *static_cast<Files*>(c); }
// #144: one file call's cycles into f.timing (data: a read or write request of bytes, else another call).
struct Span {
    Timing& t;
    const uint64_t start;
    explicit Span(Files& f) : t(f.timing), start(xyf::clock::cycles()) {}
    uint64_t took() const { return xyf::clock::cycles() - start; }
    static uint32_t longest(uint32_t was, uint64_t c) { return c > was ? (c >> 32 ? 0xffffffffu : uint32_t(c)) : was; }
    void read(int32_t got) {
        const uint64_t c = took();
        ++t.reads; t.read_units += uint32_t(c >> 10); t.read_max = longest(t.read_max, c);
        if (got > 0) t.read_bytes += uint32_t(got);
    }
    void write(int32_t put) {
        const uint64_t c = took();
        ++t.writes; t.write_units += uint32_t(c >> 10); t.write_max = longest(t.write_max, c);
        if (put > 0) t.write_bytes += uint32_t(put);
    }
};
struct Call : Span {                                                // every other file call
    explicit Call(Files& f) : Span(f) {}
    ~Call() { ++t.calls; t.call_units += uint32_t(took() >> 10); }
};
int fail(Files& f, const C::Reply& r) {
    if (r.outcome == C::link || r.outcome == C::lost || r.outcome == C::busy) {
        if (r.outcome == C::link) *f.mounted = C::mounted_stale;
        f.link = 1;
        return M::f_link;
    }
    return M::f_refused;                                            // refused: B's policy, nothing was sent
}
int from_status(Files& f, uint32_t code) {
    if (code == P::s_ok) return 0;
    if (code == P::s_link) { f.link = 1; return M::f_link; }
    return code == P::s_not_found ? M::f_not_found : code == P::s_refused ? M::f_refused : M::f_storage;
}
int errno_result(uint32_t error) {
    return error == C::enoent ? M::f_not_found : error == C::eacces ? M::f_refused : error == C::eexist ? M::f_exists :
           error == C::enotempty ? M::f_not_empty : M::f_storage;
}
// The write rule and the engine's policy for this change (the depth rule binds writes, mkdir and
// renames, never removals: #124), then A's window. to: a rename's target.
bool may_change(Files& f, const char* path, file_engine::Operation op, const char* to = 0) {
    if (!M::writable(path) || (to && !M::writable(to))) return false;
    if (to ? !storage_server::valid_rename(path, to) : !storage_server::valid_change_path(path, op)) return false;
    f.last_change_ms = xyf::clock::ms();
    if (f.window) return true;
    if (f.on_change() != P::s_ok) { f.link = 1; return false; }
    f.window = 1;
    return true;
}
void name_copy(char* out, const uint8_t* name, uint32_t n) {
    if (n > M::max_name) n = M::max_name;
    for (uint32_t i = 0; i != n; ++i) out[i] = char(name[i]);
    out[n] = 0;
}
int list(void* c, const char* folder, xyf_core::U32 skip, M::Entry* out, xyf_core::U32 room, xyf_core::U32& count, xyf_core::U32& more) {
    Files& f = of(c);
    const Call span(f);
    count = more = 0;
    if (f.link) return M::f_link;
    if (room > M::page) room = M::page;
    uint32_t n = 0, bytes = 0, more_out = 0;
    uint8_t* const page_buffer = f.page;
    const uint32_t code = C::list(folder, skip, page_buffer, room * entry_room, n, more_out, bytes, *f.mounted);
    more = more_out;
    if (code != P::s_ok) return from_status(f, code);
    uint32_t at = 0;
    for (uint32_t i = 0; i != n && count != room && at + 6 <= bytes; ++i) {
        const uint8_t* e = page_buffer + at;
        const uint32_t name_bytes = e[5];
        if (at + 6 + name_bytes > bytes) break;
        out[count].kind = e[0];
        out[count].size = uint32_t(e[1]) | uint32_t(e[2]) << 8 | uint32_t(e[3]) << 16 | uint32_t(e[4]) << 24;
        name_copy(out[count].name, e + 6, name_bytes);
        ++count;
        at += 6 + name_bytes;
    }
    if (count < n) more = 1;                                        // the rest comes with the next page
    return 0;
}
int stat(void* c, const char* path, M::Entry& out) {
    Files& f = of(c);
    const Call span(f);
    if (f.link) return M::f_link;
    uint8_t record[C::record_bytes];
    const C::Reply r = C::stat(path, record);
    if (r.outcome) return fail(f, r);
    if (r.value != 0) return errno_result(r.error);
    const uint32_t mode = P::get32(record + 8) & 0xf000u;
    out.kind = mode == 0x4000u ? 1u : mode == 0x8000u ? 0u : 2u;
    out.size = out.kind == 0 ? P::get32(record + 0x1c) : 0u;
    out.name[0] = 0;
    return 0;
}
// statfs's 0x0c-byte record read as {block size, total blocks, free blocks} (the stock layout is not yet
// proven on the device; a zero block answers storage, so the responder reports no capacity).
int space(void* c, const char* path, xyf_core::U32& block, xyf_core::U32& blocks, xyf_core::U32& free_blocks) {
    Files& f = of(c);
    const Call span(f);
    if (f.link) return M::f_link;
    uint8_t record[C::space_bytes];
    const C::Reply r = C::statfs(path, record);
    if (r.outcome) return fail(f, r);
    if (r.value != 0) return errno_result(r.error);
    block = P::get32(record); blocks = P::get32(record + 4); free_blocks = P::get32(record + 8);
    return block ? 0 : M::f_storage;
}
int open(void* c, const char* path, bool write) {
    Files& f = of(c);
    const Call span(f);
    if (f.link) return M::f_link;
    uint32_t slot = 0;
    while (slot != max_open && f.open[slot] >= 0) ++slot;
    if (slot == max_open) return M::f_storage;
    if (write && !may_change(f, path, file_engine::op_write)) return f.link ? M::f_link : M::f_refused;
    const C::Reply r = C::open(path, write ? storage_server::write_open_flags : 0u);   // op 0x29
    if (r.outcome) return fail(f, r);
    if (r.value < 0) return errno_result(r.error);
    f.open[slot] = r.value; f.open_write[slot] = write ? 1 : 0;
    if (write) ++f.writing;
    return r.value;
}
int read(void* c, int fd, xyf_core::Byte* out, xyf_core::U32 n) {
    Files& f = of(c);
    if (f.link) return M::f_link;
    if (n > C::piece_max) n = C::piece_max;
    Span span(f);
    const C::Reply r = C::read(fd, out, n);                        // op 0x2a
    span.read(r.outcome ? 0 : r.value);
    if (r.outcome) return fail(f, r);
    if (r.value < 0 || uint32_t(r.value) > n) return M::f_storage;
    ++f.data_requests; f.data_bytes += uint32_t(r.value);
    return r.value;
}
int write(void* c, int fd, const xyf_core::Byte* data, xyf_core::U32 n) {
    Files& f = of(c);
    if (f.link) return M::f_link;
    if (n > C::piece_max) n = C::piece_max;
    f.last_change_ms = xyf::clock::ms();
    Span span(f);
    const C::Reply r = C::write(fd, data, n);                      // op 0x2b
    span.write(r.outcome ? 0 : r.value);
    if (r.outcome) return fail(f, r);
    if (r.value < 0 || uint32_t(r.value) > n) return M::f_storage;
    ++f.data_requests; f.data_bytes += uint32_t(r.value);
    return r.value;
}
int seek(void* c, int fd, xyf_core::U32 offset) {
    Files& f = of(c);
    if (f.link) return M::f_link;
    const C::Reply r = C::seek(fd, int(offset));                   // op 0x2c, SEEK_SET
    if (r.outcome) return fail(f, r);
    return r.value < 0 ? M::f_storage : 0;
}
int close(void* c, int fd) {
    Files& f = of(c);
    const Call span(f);
    bool wrote = false;
    for (uint32_t i = 0; i != max_open; ++i)
        if (f.open[i] == fd) {
            f.open[i] = -1;
            if (f.open_write[i]) { f.open_write[i] = 0; --f.writing; wrote = true; }
        }
    if (wrote) f.last_change_ms = xyf::clock::ms();
    if (f.link) return M::f_link;
    const C::Reply r = C::close(fd);                               // op 0x2d
    if (r.outcome) return fail(f, r);
    return r.value ? M::f_storage : 0;
}
int change(void* c, uint32_t op, const char* path) {
    Files& f = of(c);
    const Call span(f);
    if (f.link) return M::f_link;
    const file_engine::Operation policy = op == 0x25u ? file_engine::op_unlink : op == 0x27u ? file_engine::op_rmdir :
                                          file_engine::op_mkdir;
    if (!may_change(f, path, policy)) return f.link ? M::f_link : M::f_refused;
    const C::Reply r = op == 0x25u ? C::unlink(path) : op == 0x27u ? C::rmdir(path) : C::mkdir(path);
    if (r.outcome) return fail(f, r);
    return r.value >= 0 ? 0 : errno_result(r.error);
}
int mkdir(void* c, const char* path) { return change(c, 0x26u, path); }
int unlink(void* c, const char* path) { return change(c, 0x25u, path); }
int rmdir(void* c, const char* path) { return change(c, 0x27u, path); }
int move(void* c, const char* from, const char* to, bool over) {
    Files& f = of(c);
    const Call span(f);
    if (f.link) return M::f_link;
    if (!may_change(f, from, file_engine::op_rename, to)) return f.link ? M::f_link : M::f_refused;
    uint32_t error = 0;
    const uint32_t code = C::rename(from, to, error, *f.mounted, over);
    if (code == P::s_ok) return 0;
    if (code == P::s_link) { f.link = 1; return M::f_link; }
    return error ? errno_result(error) : from_status(f, code);
}
int rename(void* c, const char* from, const char* to) { return move(c, from, to, false); }
// #4 SendObject: the upload is tmp/put-<size>-<handle> (file_engine::upload_name; its folders made as FILE_WRITE's),
// renamed onto path with rename_over (A checks its size against the name), or unlinked.
static_assert(sizeof(Files::upload) == file_engine::upload_bytes, "an upload's name");
int upload(void* c, const char* path, xyf_core::U32 size, xyf_core::U32 tag) {
    Files& f = of(c);
    const Call span(f);
    if (f.link) return M::f_link;
    uint32_t slot = 0;
    while (slot != max_open && f.open[slot] >= 0) ++slot;
    if (slot == max_open) return M::f_storage;
    file_engine::upload_name(f.upload, size, tag);
    if (!may_change(f, path, file_engine::op_write) || !may_change(f, f.upload, file_engine::op_write))
        return f.link ? M::f_link : M::f_refused;
    int fd = -1;
    const uint32_t code = C::open_write(f.upload, fd, *f.mounted);   // mkdir -p, op 0x29 "wb"
    if (code == P::s_link) { f.link = 1; return M::f_link; }
    if (code != P::s_ok) return from_status(f, code);
    f.open[slot] = fd; f.open_write[slot] = 1; ++f.writing;
    return fd;
}
int commit(void* c, const char* path, bool keep) {
    return keep ? move(c, of(c).upload, path, true) : change(c, 0x25u, of(c).upload);
}
}
M::FileOps file_ops(Files& f) {
    M::FileOps ops = {&f, list, stat, space, open, read, write, seek, close, mkdir, unlink, rmdir, rename, 0, 0,
                      upload, commit};
    return ops;
}
#endif
} }
#endif

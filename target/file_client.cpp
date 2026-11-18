#include "file_client.hpp"
#include "file_engine.hpp"
#include "usb_proto.hpp"
// Processor B's file client (see file_client.hpp).
namespace xy_forever { namespace file_client {
uint32_t bound_ms(uint32_t op, uint32_t argument) {
    const bool change = op == 8 || op == 9 || op == 0x25 || op == 0x26 || op == 0x27 || op == 0x2b || op == 0x2d ||
                        (op == 0x29 && argument) || (op == 0x21 && argument == file_engine::rename_to);
    return change ? change_ms : request_ms;
}
uint32_t status(uint32_t outcome) { return outcome == refused ? xyf::usbp::s_refused : xyf::usbp::s_link; }
uint32_t change_status(uint32_t error) {
    return error == enoent ? xyf::usbp::s_not_found : error == eacces ? xyf::usbp::s_refused :
           error == enotempty ? xyf::usbp::s_state : xyf::usbp::s_storage;
}
uint32_t rename_status(uint32_t error) { return error == eexist ? xyf::usbp::s_state : change_status(error); }
uint32_t list_entry(const uint8_t* entry, bool have_record, const uint8_t* record, uint8_t* out, uint32_t room) {
    uint32_t n = 0;
    while (n != max_name && entry[11 + n]) ++n;
    if (6 + n > room) return 0;
    uint32_t kind = entry[10] == 4 ? 1 : entry[10] == 8 ? 0 : 2;          // yaffs DT_DIR, DT_REG
    uint32_t size = 0;
    if (have_record) {
        const uint32_t mode = xyf::usbp::get32(record + 8) & 0xf000u;
        kind = entry[10] == 10 ? 2 : mode == 0x4000u ? 1 : mode == 0x8000u ? 0 : 2;   // a link (DT_LNK) stays
    }                                                                     // other: stat follows it, removals never do
    if (have_record && !kind) size = xyf::usbp::get32(record + 0x1c);
    out[0] = uint8_t(kind);
    xyf::usbp::put32(out + 1, size);
    out[5] = uint8_t(n);
    for (uint32_t i = 0; i != n; ++i) out[6 + i] = entry[11 + i];
    return 6 + n;
}
uint32_t readdir_result(int32_t value) { return !value ? folder_end : value == -1 ? folder_error : folder_entry; }
bool child_path(const char* folder, const char* name, char* out) {
    uint32_t k = 0;
    for (; folder[k]; ++k) { if (k + 1 >= file_engine::max_path) return false; out[k] = folder[k]; }
    if (!k || out[k - 1] != '/') { if (k + 1 >= file_engine::max_path) return false; out[k++] = '/'; }
    for (uint32_t i = 0; name[i]; ++i) { if (k + 1 >= file_engine::max_path) return false; out[k++] = name[i]; }
    out[k] = 0;
    return true;
}
} }

#if defined(__bfin__)
#include "peer_services.hpp"
#include "storage_server.hpp"
#include "xyf_boot.hpp"
#include "xyf_crc32.hpp"
#include "key_ui.hpp"             // where B waits (#114)
namespace xy_forever { namespace file_client {
namespace {
using peer_services::Stock121PeerSession;
typedef unsigned int U32;
// One noinline, noclone instantiation per stock entry (straight-line call audit). The builder
// allows the five request builders only through this namespace's wrappers (xyf_build.py).
template<U32 A, class R, class... Args> __attribute__((noinline, noclone)) R stock(Args... args) {
    return reinterpret_cast<R(*)(Args...)>(A)(args...);
}
void lines(U32 address, U32 bytes, bool invalidate) {
    for (U32 a = address & ~31u; a < address + bytes; a += 32) {
        if (invalidate) __asm__ volatile("FLUSHINV [%0];" : : "a"(a) : "memory");
        else __asm__ volatile("FLUSH [%0];" : : "a"(a) : "memory");
    }
    __asm__ volatile("SSYNC;" : : : "memory");
}
uint8_t* data() { return reinterpret_cast<uint8_t*>(data_buffer); }
const char* path_copy() { return reinterpret_cast<const char*>(path_buffer); }
// The stock code the client's requests run through: the peer session's own spans 0 and 1 (the
// request builders and the master's request path 889f4d00..889f9300, the request class tables).
const U32 spans[][3] = {
    {0x889f4d00u, 0x4600u, 0x291f969du},
    {0x11906eecu, 0xbc8u, 0x05c25906u},
};
U32 crc(U32 address, U32 count) {
    return xyf::crc::update_volatile(0, reinterpret_cast<const volatile uint8_t*>(address), count);
}

Reply outcome(uint32_t o) { const Reply r = {o, -1, 0}; return r; }
// The session with its slot free and a zeroed request object, or why not.
U32* claim(Stock121PeerSession*& session, Reply& why) {
    session = xyf_boot::link_claim();
    U32* request = session ? session->request_slot() : 0;
    if (!request) why = outcome(xyf_boot::link_lost() ? lost : busy);
    return request;
}
// Submit the built request (done: it left), or busy.
Reply submit(Stock121PeerSession& session, U32 op) {
    if (session.send_request(op) != peer_services::Result::waiting) return outcome(busy);
    xyf_boot::link_sent();
    const Reply r = {done, 0, 0};
    return r;
}
// A's answer once it came; else one poll: lost, link past bound ms since start
// (the request stays out), or busy (still waiting).
Reply answer(Stock121PeerSession& session, U32 start, U32 bound) {
    if (!session.filing()) { const Reply r = {done, int32_t(session.request_value()), session.request_error()}; return r; }
    if (!xyf_boot::link_step()) return outcome(lost);
    return outcome(xyf_boot::clock_ms() - start > bound ? link : busy);
}
// Wait for A's answer to the request that just left, at most bound ms.
Reply await_answer(Stock121PeerSession& session, U32 bound) {
    const key_ui::Where where(xyf::where::file_answer);
    const U32 start = xyf_boot::clock_ms();
    for (;;) {
        const Reply r = answer(session, start, bound);
        if (r.outcome != busy) return r;
        where.wait();
    }
}
// Submit the built request and wait for A's answer.
Reply send(Stock121PeerSession& session, U32 op, U32 bound) {
    const Reply s = submit(session, op);
    return s.outcome ? s : await_answer(session, bound);
}
bool allowed(const char* path, file_engine::Operation op) {
    return file_engine::decide_engine(op, path) == file_engine::allow;   // never sent otherwise
}
// The path into the client's buffer, only once the slot is claimed: a request left out after its
// bound still points at path_buffer, and the stock driver reads the path (and its length) only when
// it sends it, which may be later.
void place(const char* path) {
    char* to = reinterpret_cast<char*>(path_buffer);
    U32 k = 0;
    for (; path[k]; ++k) to[k] = path[k];                               // < max_path: decide_engine checked
    to[k] = 0;
}

// A partition-class request built and submitted (done), or why not.
Reply partition_out(Stock121PeerSession*& session, U32 op, U32 a0, U32 a1, U32 a2) {
    Reply why;
    U32* request = claim(session, why);
    if (!request) return why;
    stock<0x889f87d4u, void>(request, op, a0, a1, a2);                  // partition class
    return submit(*session, op);
}
Reply partition(U32 op, U32 a0, U32 a1, U32 a2) {
    Stock121PeerSession* session = 0;
    const Reply s = partition_out(session, op, a0, a1, a2);
    return s.outcome ? s : await_answer(*session, bound_ms(op, a0));
}
// The op 9 left by unmount_start (its session and when it left; 0: none).
Stock121PeerSession* volatile closing_session __attribute__((section(".data"))) = 0;
volatile U32 closing_start __attribute__((section(".data"))) = 0;
// A path-class request (the only call site of its builder, behind the policy check). left: only
// submitted (*left: its session; the caller polls the answer), else A's answer awaited.
Reply path_request(const char* path, file_engine::Operation policy, U32 op, U32 argument,
                   Stock121PeerSession** left = 0) {
    if (!allowed(path, policy)) return outcome(refused);
    Stock121PeerSession* session; Reply why;
    U32* request = claim(session, why);
    if (!request) return why;
    place(path);
    stock<0x889f8d22u, void>(request, op, path_copy(), argument, 0u);   // path class (flushes the path)
    if (left) { *left = session; return submit(*session, op); }
    return send(*session, op, bound_ms(op, argument));
}
Reply query(const char* path, U32 op, uint8_t* out, U32 bytes) {
    if (!allowed(path, file_engine::op_stat)) return outcome(refused);
    Stock121PeerSession* session; Reply why;
    U32* request = claim(session, why);
    if (!request) return why;
    place(path);
    stock<0x889f8e36u, void>(request, op, path_copy(), 0u, 0u, data(), bytes);   // query class
    const Reply r = send(*session, op, bound_ms(op, 0));
    if (r.outcome == done) {
        lines(data_buffer, bytes, true);
        for (U32 i = 0; i != bytes; ++i) out[i] = data()[i];
    }
    return r;
}
}

uint32_t identity() {
    uint32_t bits = 0;
    for (U32 i = 0; i != sizeof(spans) / sizeof(spans[0]); ++i)
        if (crc(spans[i][0], spans[i][1]) != spans[i][2]) bits |= 1u << i;
    return bits;
}

Reply mount() { return partition(8, 0, 0, 0); }
Reply unmount() { return partition(9, 0, 0, 0); }
Reply unmount_start() {
    Stock121PeerSession* session = 0;
    const Reply s = partition_out(session, 9, 0, 0, 0);
    if (!s.outcome) { closing_session = session; closing_start = xyf_boot::clock_ms(); }
    return s;
}
Reply unmount_poll() {
    Stock121PeerSession* session = closing_session;
    if (!session) return outcome(refused);
    const Reply r = answer(*session, closing_start, bound_ms(9, 0));
    if (r.outcome != busy) closing_session = 0;
    return r;
}
Reply mkdir_start(const char* path) {
    Stock121PeerSession* session = 0;
    const Reply s = path_request(path, file_engine::op_mkdir, 0x26, 0, &session);   // as mkdir, not awaited
    if (!s.outcome) { closing_session = session; closing_start = xyf_boot::clock_ms(); }   // unmount_poll's slot
    return s;
}
Reply seek(int fd, int offset) { return partition(0x2c, U32(fd), U32(offset), 0); }
Reply close(int fd) { return partition(0x2d, U32(fd), 0, 0); }
Reply closedir(uint32_t folder) { return partition(0x30, folder, 0, 0); }
Reply open(const char* path, uint32_t flags) {
    return path_request(path, flags ? file_engine::op_write : file_engine::op_read, 0x29, flags);
}
Reply mkdir(const char* path) { return path_request(path, file_engine::op_mkdir, 0x26, 0); }
Reply unlink(const char* path) { return path_request(path, file_engine::op_unlink, 0x25, 0); }
Reply rmdir(const char* path) { return path_request(path, file_engine::op_rmdir, 0x27, 0); }
Reply opendir(const char* path) { return path_request(path, file_engine::op_list, 0x2e, 0); }
Reply stat(const char* path, uint8_t* record) { return query(path, 0x22, record, record_bytes); }
Reply statfs(const char* path, uint8_t* space) { return query(path, 0x23, space, space_bytes); }
Reply read(int fd, uint8_t* out, uint32_t count) {
    if (!count || count > chunk) return outcome(refused);
    Stock121PeerSession* session; Reply why;
    U32* request = claim(session, why);
    if (!request) return why;
    lines(data_buffer, count, true);
    stock<0x889f6c5cu, void>(request, 0x2au, U32(fd), U32(count), data(), 0x1000u);   // read class
    const Reply r = send(*session, 0x2a, bound_ms(0x2a, 0));
    if (r.outcome == done && r.value > 0) {
        lines(data_buffer, count, true);
        const U32 n = U32(r.value) < count ? U32(r.value) : count;
        for (U32 i = 0; i != n; ++i) out[i] = data()[i];
    }
    return r;
}
Reply write(int fd, const uint8_t* bytes, uint32_t count) {
    if (!count || count > chunk) return outcome(refused);
    Stock121PeerSession* session; Reply why;
    U32* request = claim(session, why);
    if (!request) return why;
    for (U32 i = 0; i != count; ++i) data()[i] = bytes[i];
    stock<0x889f8c10u, void>(request, 0x2bu, U32(fd), U32(count), data(), U32(count));   // write class (flushes)
    return send(*session, 0x2b, bound_ms(0x2b, 0));
}
Reply readdir(uint32_t folder, uint8_t* entry) {
    Stock121PeerSession* session; Reply why;
    U32* request = claim(session, why);
    if (!request) return why;
    stock<0x889f88acu, void>(request, 0x2fu, folder, 0u, data(), entry_bytes);   // sector class (invalidates)
    const Reply r = send(*session, 0x2f, bound_ms(0x2f, 0));
    if (r.outcome == done) {
        lines(data_buffer, entry_bytes, true);
        for (U32 i = 0; i != entry_bytes; ++i) entry[i] = data()[i];
    }
    return r;
}

volatile U32 command_sequence __attribute__((section(".data"))) = 0;
uint32_t serving() {
    // The low three bits stay 0 (A's bootloader's select takes the argument & 7: the user area).
    const U32 value = U32(Stock121PeerSession::command_tag) << 16 | (storage_server::command_item & 0xffu) << 8 |
                      (++command_sequence << 3 & 0xf8u);
    const Reply r = partition(0x18, value, 0, 0);                        // a command item, bounded
    if (r.outcome) return status(r.outcome);
    return U32(r.value) == peer_services::command_ack(value) ? xyf::usbp::s_ok : xyf::usbp::s_link;
}

// B's op-8 state before a request: a request past its bound (stale) is closed first (op 9: A closes
// B's files and folders), then op 8 (A's read-only mount) when not mounted.
uint32_t mount_ready(volatile uint32_t& mounted) {
    if (mounted == mounted_stale) {                                      // a request ran out its bound
        const Reply u = unmount();                                       // op 9: A closes B's files and folders
        if (u.outcome) return status(u.outcome);
        mounted = 0;
    }
    if (!mounted) {
        const Reply m = mount();                                         // op 8 (a: read-only mount)
        if (m.outcome) return status(m.outcome);
        if (m.value) return xyf::usbp::s_storage;
        mounted = 1;
    }
    return xyf::usbp::s_ok;
}

uint32_t read_file(const char* path, uint32_t offset, bool ranged, uint8_t* out, uint32_t capacity,
                   uint32_t& bytes, volatile uint32_t& mounted, Open* left) {
    bytes = 0;
    if (left) { left->fd = -1; left->probe = false; }
    if (!storage_server::valid_path(path)) return xyf::usbp::s_bad_request;
    if (file_engine::decide_engine(file_engine::op_read, path) != file_engine::allow) return xyf::usbp::s_refused;
    const uint32_t ready = mount_ready(mounted);
    if (ready != xyf::usbp::s_ok) return ready;
    // Past its bound a request stays out: A may still answer it (an open: a handle B never closes).
    auto failed = [&mounted](const Reply& r) -> uint32_t { if (r.outcome == link) mounted = mounted_stale; return status(r.outcome); };
    const Reply o = open(path, 0);                                       // op 0x29, O_RDONLY
    if (o.outcome) return failed(o);
    if (o.value < 0) return o.error == emfile ? xyf::usbp::s_storage : xyf::usbp::s_not_found;
    const int fd = o.value;
    U32 code = xyf::usbp::s_ok;
    if (offset) {
        const Reply k = seek(fd, int(offset));                           // op 0x2c SEEK_SET
        if (k.outcome) return failed(k);
        if (k.value != int(offset)) code = xyf::usbp::s_range;
    }
    for (U32 guard = 0; guard != 4096 + (capacity >> 10) && code == xyf::usbp::s_ok; ++guard) {   // 7 MiB copies
        const U32 room = capacity - bytes;
        uint8_t probe[4];
        // Range full: the 1-byte probe still runs (Test 041: ranged reads that closed right after
        // a full read hung a after four; reads ending with a probe or EOF never did). A ranged
        // read's probe decides nothing: with left, it runs in finish_read, before the close.
        if (!room && ranged && left) { left->fd = fd; left->probe = true; return code; }
        const Reply g = read(fd, room ? out + bytes : probe, room ? (room < chunk ? room : chunk) : 1u);   // op 0x2a
        if (g.outcome) return failed(g);
        if (g.value < 0) { code = xyf::usbp::s_storage; break; }
        if (!g.value) break;
        if (!room) { code = ranged ? xyf::usbp::s_ok : xyf::usbp::s_too_large; break; }
        bytes += U32(g.value);
    }
    if (left) { left->fd = fd; return code; }                            // closed in finish_read
    const Reply c = close(fd);                                           // op 0x2d
    if (c.outcome) return failed(c);
    return code;
}
void finish_read(const Open& left, volatile uint32_t& mounted) {
    if (left.fd < 0) return;
    uint8_t probe[4];
    if (left.probe && read(left.fd, probe, 1).outcome == link) { mounted = mounted_stale; return; }   // op 0x2a
    if (close(left.fd).outcome == link) mounted = mounted_stale;                                       // op 0x2d
}

uint32_t list(const char* path, uint32_t skip, uint8_t* out, uint32_t capacity, uint32_t& count, uint32_t& more,
              uint32_t& bytes, volatile uint32_t& mounted) {
    count = 0; more = 0; bytes = 0;
    if (file_engine::decide_engine(file_engine::op_list, path) != file_engine::allow) return xyf::usbp::s_refused;
    const uint32_t ready = mount_ready(mounted);
    if (ready != xyf::usbp::s_ok) return ready;
    auto failed = [&mounted](const Reply& r) -> uint32_t { if (r.outcome == link) mounted = mounted_stale; return status(r.outcome); };
    const Reply d = opendir(path);                                      // op 0x2e
    if (d.outcome) return failed(d);
    if (!d.value || d.value == -1) return d.error == emfile ? xyf::usbp::s_storage : xyf::usbp::s_not_found;   // NULL
    const U32 folder = U32(d.value);
    uint8_t entry[entry_bytes + 4], record[record_bytes];
    char child[file_engine::max_path];
    U32 code = xyf::usbp::s_ok;
    for (U32 seen = 0; seen != 0x10000u; ++seen) {
        const Reply r = readdir(folder, entry);                          // op 0x2f: A's dirent address, 0 the end
        if (r.outcome) return failed(r);
        const U32 got = readdir_result(r.value);
        if (got == folder_end) break;
        if (got == folder_error) { code = xyf::usbp::s_storage; break; }
        if (seen < skip) continue;
        if (count == max_page) { more = 1; break; }
        entry[11 + max_name] = 0;
        bool have = false;
        if (child_path(path, reinterpret_cast<const char*>(entry + 11), child)) {
            const Reply q = stat(child, record);                         // op 0x22 (a refused name: no record)
            if (q.outcome == link || q.outcome == busy || q.outcome == lost) return failed(q);
            have = q.outcome == done && q.value == 0;
        }
        const U32 n = list_entry(entry, have, record, out + bytes, capacity - bytes);
        if (!n) { more = 1; break; }
        bytes += n; ++count;
    }
    const Reply c = closedir(folder);                                    // op 0x30
    if (c.outcome) return failed(c);
    return code;
}

void release(volatile uint32_t& mounted) {
    if (!mounted) return;
    unmount();                                                           // op 9 (a link error: A closes on its own)
    mounted = 0;
}

#if defined(XYF_USB_FILE_WRITE)
uint32_t write_file(const char* path, const uint8_t* bytes, uint32_t size, uint32_t crc,
                    uint32_t& written, uint32_t& readback_crc, volatile uint32_t& mounted) {
    written = 0; readback_crc = 0;
    // file_write checks the path first; the rest cannot happen from it (staged data). Size 0 (a copy
    // of an empty file) opens, writes nothing and closes.
    if (!storage_server::valid_write_path(path) || !bytes) return xyf::usbp::s_refused;
    if (mounted == mounted_stale) return xyf::usbp::s_state;            // file_write sends op 9 first
    if (!mounted) {
        const Reply m = mount();                                         // op 8: a's read-only mount
        if (m.outcome) return status(m.outcome);
        if (m.value) return xyf::usbp::s_storage;
        mounted = 1;
    }
    // a opens its read-write window on the first change below and keeps it for the next
    // writes; b closes it (op 9) once no write came for a while (usb_service). Past a request's
    // bound: op 9 before the next file command (a closes b's handles and the window).
    auto failed = [&mounted](const Reply& r) -> uint32_t { mounted = mounted_stale; return status(r.outcome); };
    // Test 050: create the missing folders below the write area's root (mkdir -p; existing ones fail
    // EEXIST, which is fine: the open below decides).
    const U32 root_bytes = storage_server::write_root(path);             // 8: /yaffs2/
    char folder[file_engine::max_path];
    U32 k = 0;
    for (; path[k]; ++k) {
        folder[k] = path[k];
        if (path[k] != '/' || k < root_bytes) continue;
        folder[k] = 0;
        const Reply d = mkdir(folder);                                   // op 0x26, mode 0
        if (d.outcome == link || d.outcome == busy || d.outcome == lost) return failed(d);
        folder[k] = '/';
    }
    U32 code = xyf::usbp::s_ok;
    Reply o = open(path, storage_server::write_open_flags);              // op 0x29, "wb"
    if (o.outcome) return failed(o);
    if (o.value < 0) code = xyf::usbp::s_storage;
    else {
        while (written != size) {
            const U32 n = size - written < chunk ? size - written : chunk;
            const Reply w = write(o.value, bytes + written, n);          // op 0x2b
            if (w.outcome) return failed(w);
            if (w.value != int(n)) { code = xyf::usbp::s_storage; break; }
            written += n;
        }
        const Reply c = close(o.value);                                  // op 0x2d
        if (c.outcome) return failed(c);
        if (c.value && code == xyf::usbp::s_ok) code = xyf::usbp::s_storage;
    }
    if (code == xyf::usbp::s_ok) {                                       // read back to EOF (the open window)
        o = open(path, 0);                                               // op 0x29, read-only
        if (o.outcome) return failed(o);
        if (o.value < 0) code = xyf::usbp::s_not_found;
        else {
            U32 total = 0, c = 0;
            uint8_t chunk_bytes[chunk];
            for (U32 guard = 0; guard != (size >> 10) + 4; ++guard) {
                const Reply g = read(o.value, chunk_bytes, chunk);       // op 0x2a
                if (g.outcome) return failed(g);
                if (g.value < 0) { code = xyf::usbp::s_storage; break; }
                if (!g.value) break;
                c = xyf::crc::update(c, chunk_bytes, U32(g.value));
                total += U32(g.value);
            }
            const Reply cl = close(o.value);
            if (cl.outcome) return failed(cl);
            readback_crc = c;
            if (code == xyf::usbp::s_ok && (total != size || c != crc)) code = xyf::usbp::s_crc;
        }
    }
    return code;
}

uint32_t copy_target(const char* path, uint32_t& error, volatile uint32_t& mounted) {
    error = 0;
    const uint32_t ready = mount_ready(mounted);                         // stale: op 9 first; op 8
    if (ready != xyf::usbp::s_ok) return ready;
    char parent[file_engine::max_path];
    U32 n = 0, cut = 0;
    for (; path[n] && n + 1 != file_engine::max_path; ++n) { parent[n] = path[n]; if (path[n] == '/') cut = n; }
    parent[cut] = 0;
    uint8_t record[record_bytes];
    const Reply t = stat(path, record);                                  // op 0x22, as file.list
    if (t.outcome == refused) return xyf::usbp::s_refused;
    if (t.outcome) { mounted = mounted_stale; return status(t.outcome); }
    if (t.value == 0) { error = eexist; return xyf::usbp::s_state; }
    const Reply p = stat(parent, record);
    if (p.outcome == refused) return xyf::usbp::s_refused;
    if (p.outcome) { mounted = mounted_stale; return status(p.outcome); }
    if (p.value != 0) { error = enoent; return xyf::usbp::s_not_found; }
    return xyf::usbp::s_ok;
}

uint32_t remove_partial(const char* path, volatile uint32_t& mounted) {
    if (mounted == mounted_stale) return 1;                              // a request is still out
    const Reply u = unlink(path);                                        // op 0x25, as FILE_DELETE
    if (u.outcome == done && u.value >= 0) return 0;
    if (u.outcome) { if (u.outcome != refused) mounted = mounted_stale; return 1; }
    uint8_t record[record_bytes];
    const Reply q = stat(path, record);                                  // the unlink failed: is there one?
    if (q.outcome) { if (q.outcome != refused) mounted = mounted_stale; return 1; }
    return q.value == 0 ? 1 : 0;
}

uint32_t change(uint32_t op, const char* path, uint32_t& error, volatile uint32_t& mounted) {
    error = 0;
    if (mounted == mounted_stale) return xyf::usbp::s_state;            // the caller sends op 9 first
    const uint32_t ready = mount_ready(mounted);                         // op 8: a's read-only mount
    if (ready != xyf::usbp::s_ok) return ready;
    const Reply r = op == 0x25 ? unlink(path) : op == 0x27 ? rmdir(path) : mkdir(path);   // a opens its window
    if (r.outcome == refused) return xyf::usbp::s_refused;
    if (r.outcome) { mounted = mounted_stale; return status(r.outcome); }
    if (r.value >= 0) return xyf::usbp::s_ok;
    error = r.error;
    return change_status(error);
}

namespace {
// The depth rule for a folder moved deeper (#124, file_engine.hpp): the target and every folder below
// the source must stay within max_folders after the move. Walks the source's folders only (readdir's
// type: DT_DIR 4; a stat for an entry that says neither folder, file nor link), down to the first
// one too deep, one folder handle at a time (a level resumes after the subfolder it went into).
// s_ok (also when nothing gets deeper or the source is not a folder: the rename answers for it),
// s_refused, else the request's status.
uint32_t deeper_ok(const char* from, const char* to, volatile uint32_t& mounted) {
    const uint32_t s = file_engine::depth(from), t = file_engine::depth(to);
    if (t <= s) return xyf::usbp::s_ok;                                  // nothing gets deeper (both parse)
    auto failed = [&mounted](const Reply& r) -> uint32_t { if (r.outcome == link) mounted = mounted_stale; return status(r.outcome); };
    uint8_t record[record_bytes], entry[entry_bytes + 4];
    const Reply q = stat(from, record);                                  // op 0x22
    if (q.outcome) return failed(q);
    if (q.value != 0 || (xyf::usbp::get32(record + 8) & 0xf000u) != 0x4000u) return xyf::usbp::s_ok;
    if (t > file_engine::max_folders) return xyf::usbp::s_refused;
    const U32 room = file_engine::max_folders - t;                       // folder levels allowed below the target
    char path[file_engine::max_path], child[file_engine::max_path];
    for (U32 k = 0; k != file_engine::max_path; ++k) if (!(path[k] = from[k])) break;   // < max_path: it parsed
    U32 skip[file_engine::max_folders + 1], level = 0;
    skip[0] = 0;
    for (;;) {
        const Reply d = opendir(path);                                   // op 0x2e
        if (d.outcome) return failed(d);
        if (!d.value || d.value == -1) return d.error == emfile ? xyf::usbp::s_storage : xyf::usbp::s_not_found;
        U32 code = xyf::usbp::s_ok, seen = 0;
        bool down = false;
        for (; seen != 0x10000u && !down; ++seen) {
            const Reply r = readdir(U32(d.value), entry);                // op 0x2f
            if (r.outcome) return failed(r);
            const U32 got = readdir_result(r.value);
            if (got == folder_end) break;
            if (got == folder_error) { code = xyf::usbp::s_storage; break; }
            if (seen < skip[level]) continue;
            entry[11 + max_name] = 0;
            if (!child_path(path, reinterpret_cast<const char*>(entry + 11), child)) { code = xyf::usbp::s_refused; break; }
            bool folder = entry[10] == 4;
            if (entry[10] != 4 && entry[10] != 8 && entry[10] != 10) {   // not said: stat it
                const Reply e = stat(child, record);
                if (e.outcome) return failed(e);
                folder = e.value == 0 && (xyf::usbp::get32(record + 8) & 0xf000u) == 0x4000u;
            }
            if (!folder) continue;
            if (level + 1 > room) code = xyf::usbp::s_refused;           // this folder would sit too deep
            skip[level] = seen + 1;
            down = true;
        }
        const Reply c = closedir(U32(d.value));                          // op 0x30
        if (c.outcome) return failed(c);
        if (code != xyf::usbp::s_ok) return code;
        if (down) {
            for (U32 k = 0; k != file_engine::max_path; ++k) if (!(path[k] = child[k])) break;
            skip[++level] = 0;
            continue;
        }
        if (!level) return xyf::usbp::s_ok;
        U32 cut = 0;                                                     // back up to the parent folder
        for (U32 k = 0; path[k]; ++k) if (path[k] == '/') cut = k;
        path[cut] = 0;
        --level;
    }
}
}

uint32_t rename(const char* from, const char* to, uint32_t& error, volatile uint32_t& mounted) {
    error = 0;
    if (!storage_server::valid_rename(from, to)) return xyf::usbp::s_refused;   // file_rename checks it first
    if (mounted == mounted_stale) return xyf::usbp::s_state;            // the caller sends op 9 first
    const uint32_t ready = mount_ready(mounted);                         // op 8: a's read-only mount
    if (ready != xyf::usbp::s_ok) return ready;
    const uint32_t deep = deeper_ok(from, to, mounted);                  // #124: a folder moved deeper
    if (deep != xyf::usbp::s_ok) return deep;
    const U32 tags[2] = {file_engine::rename_from, file_engine::rename_to};
    const char* paths[2] = {from, to};
    for (U32 i = 0; i != 2; ++i) {                                      // the pair checked above: op_stat here
        const Reply r = path_request(paths[i], file_engine::op_stat, 0x21, tags[i]);   // a opens its window at rename_to
        if (r.outcome == refused) return xyf::usbp::s_refused;
        if (r.outcome) { mounted = mounted_stale; return status(r.outcome); }
        if (r.value < 0) { error = r.error; return rename_status(error); }
    }
    return xyf::usbp::s_ok;
}
#endif
} }
#endif

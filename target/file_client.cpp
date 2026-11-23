#include "file_client.hpp"
#include "file_engine.hpp"
#include "usb_proto.hpp"
#include "boot_partition_probe.hpp"
#include "managed_blob_link.hpp"
#include "tfw_role_probe.hpp"
#include "tfw_hash_rpc.hpp"
#include "boot_services.hpp"
// Processor B's file client (see file_client.hpp).
namespace xy_forever { namespace file_client {
uint32_t bound_ms(uint32_t op, uint32_t argument) {
    const bool change = op == 8 || op == 9 || op == 0x25 || op == 0x26 || op == 0x27 || op == 0x2b || op == 0x2d ||
                        (op == 0x29 && argument) ||
                        (op == 0x21 && (argument == file_engine::rename_to || argument == file_engine::rename_over ||
                                        argument == file_engine::rename_upload));
    return change ? change_ms : request_ms;
}
uint32_t status(uint32_t outcome) { return outcome == refused ? xyf::usbp::s_refused : xyf::usbp::s_link; }
uint32_t change_status(uint32_t error) {
    return error == enoent    ? xyf::usbp::s_not_found :
           error == eacces    ? xyf::usbp::s_refused :
           error == enotempty ? xyf::usbp::s_state :
                                xyf::usbp::s_storage;
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
    for (; folder[k]; ++k) {
        if (k + 1 >= file_engine::max_path) return false;
        out[k] = folder[k];
    }
    if (!k || out[k - 1] != '/') {
        if (k + 1 >= file_engine::max_path) return false;
        out[k++] = '/';
    }
    for (uint32_t i = 0; name[i]; ++i) {
        if (k + 1 >= file_engine::max_path) return false;
        out[k++] = name[i];
    }
    out[k] = 0;
    return true;
}
}}

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
    return reinterpret_cast<R (*)(Args...)>(A)(args...);
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

Reply outcome(uint32_t o) {
    const Reply r = {o, -1, 0};
    return r;
}
// #144: the one request the client leaves out for its owner to collect (unmount_start, mkdir_start, write_start):
// its session, when it left and its bound (left_session 0: none). Any other request's claim collects it first
// (link_claim waits, bounded, for the slot) and keeps its answer here (left_kept) until the owner asks: the slot
// and B's copy of the last answer (xyf_boot::link_answer) are reused by the next request.
Stock121PeerSession* volatile left_session __attribute__((section(".data"))) = 0;
volatile U32 left_start __attribute__((section(".data"))) = 0;
volatile U32 left_bound __attribute__((section(".data"))) = 0;
volatile U32 left_kept __attribute__((section(".data"))) = 0;
volatile uint32_t left_value __attribute__((section(".data"))) = 0;
volatile uint32_t left_error __attribute__((section(".data"))) = 0;
// The session with its slot free and a zeroed request object, or why not.
U32* claim(Stock121PeerSession*& session, Reply& why) {
    session = xyf_boot::link_claim();                   // an outstanding request first (bounded: answer_ms)
    if (session && left_session && !left_kept && !session->filing()) {   // the left one answered: kept for its owner
        uint32_t value, error;
        xyf_boot::link_answer(value, error);
        left_value = value;
        left_error = error;
        left_kept = 1;
    }
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
    if (!session.filing()) {                                            // B's poll kept its answer (#144)
        uint32_t value, error;
        xyf_boot::link_answer(value, error);
        const Reply r = {done, int32_t(value), error};
        return r;
    }
    if (!xyf_boot::link_step()) return outcome(lost);
    return outcome(xyf_boot::clock_ms() - start > bound ? link : busy);
}
// Wait for A's answer to the request that left at start (default: just now), at most bound ms after it.
Reply await_answer(Stock121PeerSession& session, U32 bound, U32 start = xyf_boot::clock_ms()) {
    const key_ui::Where where(xyf::where::file_answer);
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
// A request built and submitted, left out for its owner (left_poll), or why not (busy: one is left already).
Reply leave(Stock121PeerSession* session, const Reply& submitted, U32 bound) {
    if (!submitted.outcome) {
        left_session = session;
        left_start = xyf_boot::clock_ms();
        left_bound = bound;
        left_kept = 0;
    }
    return submitted;
}
// A path-class request (the only call site of its builder, behind the policy check). left: only
// submitted (*left: its session; the caller polls the answer), else A's answer awaited. sent: the bytes that go
// instead of path (rename_upload's, file_engine::join_upload; path is its target, checked).
Reply path_request(const char* path, file_engine::Operation policy, U32 op, U32 argument,
                   Stock121PeerSession** left = 0, const char* sent = 0, U32 bound = 0) {
    if (!allowed(path, policy)) return outcome(refused);
    Stock121PeerSession* session;
    Reply why;
    U32* request = claim(session, why);
    if (!request) return why;
    if (sent) path = sent;                                              // (its target passed the check above)
    place(path);
    stock<0x889f8d22u, void>(request, op, path_copy(), argument, 0u);   // path class (flushes the path)
    if (left) {
        *left = session;
        return submit(*session, op);
    }
    return send(*session, op, bound ? bound : bound_ms(op, argument));
}
Reply query(const char* path, U32 op, uint8_t* out, U32 bytes) {
    if (!allowed(path, file_engine::op_stat)) return outcome(refused);
    Stock121PeerSession* session;
    Reply why;
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
bool left_out() { return left_session != 0; }
Reply left_poll(bool wait) {
    Stock121PeerSession* session = left_session;
    if (!session) return outcome(refused);
    Reply r;
    if (left_kept) {
        r.outcome = done;
        r.value = int32_t(left_value);
        r.error = left_error;
    } else if (wait) r = await_answer(*session, left_bound, left_start);
    else r = answer(*session, left_start, left_bound);
    if (r.outcome != busy) {
        left_session = 0;
        left_kept = 0;
    }   // past its bound (link): out, no owner now
    return r;
}
Reply unmount_start() {
    if (left_session) return outcome(busy);
    Stock121PeerSession* session = 0;
    const Reply s = partition_out(session, 9, 0, 0, 0);
    return leave(session, s, bound_ms(9, 0));
}
Reply unmount_poll() { return left_poll(false); }
Reply mkdir_start(const char* path) {
    if (left_session) return outcome(busy);
    Stock121PeerSession* session = 0;
    const Reply s = path_request(path, file_engine::op_mkdir, 0x26, 0, &session);   // as mkdir, not awaited
    return leave(session, s, bound_ms(9, 0));                            // collected by unmount_poll
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
// A read request into dest (count bytes, the client's own buffer: data_buffer or stream_buffer) built and submitted
// (done), or why not. A streams the reply back in 0x1000-byte bodies.
Reply read_out(Stock121PeerSession*& session, int fd, U32 dest, uint32_t count) {
    Reply why;
    U32* request = claim(session, why);
    if (!request) return why;
    lines(dest, count, true);                                            // no dirty line over what A sends
    stock<0x889f6c5cu, void>(request, 0x2au, U32(fd), U32(count), reinterpret_cast<uint8_t*>(dest), 0x1000u);
    return submit(*session, 0x2a);
}
Reply read(int fd, uint8_t* out, uint32_t count) {
    if (!count || count > piece_max) return outcome(refused);
    Stock121PeerSession* session = 0;
    const Reply s = read_out(session, fd, data_buffer, count);
    const Reply r = s.outcome ? s : await_answer(*session, bound_ms(0x2a, 0));
    if (r.outcome == done && r.value > 0) {
        lines(data_buffer, count, true);
        const U32 n = U32(r.value) < count ? U32(r.value) : count;
        for (U32 i = 0; i != n; ++i) out[i] = data()[i];
    }
    return r;
}
// A write request built and submitted (done), or why not.
Reply write_out(Stock121PeerSession*& session, int fd, const uint8_t* bytes, uint32_t count) {
    if (!count || count > piece_max) return outcome(refused);
    Reply why;
    U32* request = claim(session, why);
    if (!request) return why;
    for (U32 i = 0; i != count; ++i) data()[i] = bytes[i];
    stock<0x889f8c10u, void>(request, 0x2bu, U32(fd), U32(count), data(), U32(count));   // write class (flushes)
    return submit(*session, 0x2b);
}
Reply write(int fd, const uint8_t* bytes, uint32_t count) {
    Stock121PeerSession* session = 0;
    const Reply s = write_out(session, fd, bytes, count);
    return s.outcome ? s : await_answer(*session, bound_ms(0x2b, 0));
}
Reply write_start(int fd, const uint8_t* bytes, uint32_t count) {
    if (left_session) return outcome(busy);
    Stock121PeerSession* session = 0;
    const Reply s = write_out(session, fd, bytes, count);
    return leave(session, s, bound_ms(0x2b, 0));                         // left_poll collects it
}
Reply read_start(int fd, uint32_t at, uint32_t count) {
    if (!count || count > stream_max || at % 32 || at > stream_capacity - count) return outcome(refused);
    if (left_session) return outcome(busy);
    Stock121PeerSession* session = 0;
    const Reply s = read_out(session, fd, stream_buffer + at, count);
    return leave(session, s, bound_ms(0x2a, 0));                         // left_poll collects it
}
const uint8_t* stream(uint32_t at, uint32_t count) {
    if (at <= stream_capacity && count <= stream_capacity - at) lines(stream_buffer + at, count, true);
    return reinterpret_cast<const uint8_t*>(stream_buffer + at);
}
Reply a_timing(uint32_t k) {
    return path_request("/yaffs2", file_engine::op_stat, 0x21, file_engine::timing_tag | (k & 0xffu));
}
Reply tfw_probe(const char* path, uint32_t op) {
    return path_request(path, file_engine::op_stat, 0x21, tfw_role_probe::tag | op, 0, 0, 5000u);
}
#if defined(XYF_TFW_CHECK)
Reply tfw_hash_send(const char* path, uint32_t operation) {
    namespace th = tfw_hash_rpc;
    if (left_session) return outcome(busy);
    if ((operation != th::start && operation != th::status && operation != th::cancel &&
         (operation < th::digest || operation >= th::digest + th::digest_words)) ||
        !services::loader_path(path))
        return outcome(refused);
    Stock121PeerSession* session = 0;
    const Reply sent = path_request(path, file_engine::op_read, 0x21, th::tag | operation, &session);
    return leave(session, sent, request_ms);
}
#endif
#if defined(XYF_USB_FILE_WRITE)
Reply stage_blob_link(const char* temporary, const uint8_t* hash, bool seal) {
    namespace ml = managed_blob_link;
    char frame[ml::frame_bytes];
    if (!ml::encode(temporary, hash, frame, seal)) return outcome(refused);
    const uint32_t op = seal ? ml::seal : ml::stage;
    return path_request(temporary, file_engine::op_write, 0x21, ml::version << 24 | ml::tag | op, 0, frame, change_ms);
}
Reply copy_blob_piece() {
    namespace ml = managed_blob_link;
    return path_request("/yaffs2", file_engine::op_stat, 0x21, ml::version << 24 | ml::tag | ml::copy, 0, 0, change_ms);
}
#endif
Reply boot_probe(uint32_t op) {
    return path_request("/yaffs2", file_engine::op_stat, 0x21, boot_partition_probe::tag | (op & 0xffu));
}
Reply boot_select(uint32_t p) { return p <= 2 ? partition(0x18, p, 0, 0) : outcome(refused); }
Reply boot_sector(uint32_t at, uint8_t* out) {
    if (at > boot_partition_probe::max_bytes / 512u) return outcome(refused);
    Stock121PeerSession* session;
    Reply why;
    U32* request = claim(session, why);
    if (!request) return why;
    stock<0x889f8872u, void>(request, 0x11u, at, 1u, 0u, data(), 512u); // same sector builder as peer_services
    const Reply r = send(*session, 0x11, request_ms);
    if (!r.outcome && !r.value) {
        lines(data_buffer, 512, true);
        for (U32 i = 0; i != 512; ++i) out[i] = data()[i];
    }
    return r;
}
Reply readdir(uint32_t folder, uint8_t* entry) {
    Stock121PeerSession* session;
    Reply why;
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

uint32_t close_name_scan(NameScan& scan, volatile uint32_t& mounted) {
    if (!scan.folder) return xyf::usbp::s_ok;
    const Reply r = closedir(scan.folder);
    scan.folder = 0;
    if (r.outcome == link) mounted = mounted_stale;
    return r.outcome ? status(r.outcome) : r.value ? xyf::usbp::s_storage : xyf::usbp::s_ok;
}

uint32_t scan_name(const char* path, NameScan& scan, volatile uint32_t& mounted) {
    namespace P = xyf::usbp;
    const char* name = path;
    for (const char* p = path; *p; ++p)
        if (*p == '/') name = p + 1;
    if (name == path || !*name || uint32_t(name - path) > file_engine::max_path) return P::s_bad_request;
    if (!scan.folder) {
        char parent[file_engine::max_path];
        const uint32_t n = uint32_t(name - path) - 1;
        for (U32 i = 0; i != n; ++i) parent[i] = path[i];
        parent[n] = 0;
        const Reply r = opendir(parent);
        if (r.outcome == link) mounted = mounted_stale;
        if (r.outcome) return status(r.outcome);
        if (!r.value || r.value == -1) return change_status(r.error);
        scan.folder = uint32_t(r.value);
        scan.seen = 0;
        return scan_again;
    }
    uint8_t entry[entry_bytes + 4];
    const Reply r = readdir(scan.folder, entry);
    if (r.outcome == link) {
        mounted = mounted_stale;
        scan.folder = 0;
        return P::s_link;
    }
    uint32_t code = r.outcome ? status(r.outcome) : uint32_t(P::s_storage);
    if (!r.outcome) {
        const U32 got = readdir_result(r.value);
        if (got == folder_end) code = P::s_not_found;
        else if (got == folder_entry) {
            entry[11 + max_name] = 0;
            const char* text = reinterpret_cast<const char*>(entry + 11);
            while (*text && *name == *text) {
                ++text;
                ++name;
            }
            if (*text == *name) code = P::s_ok;
            else if (++scan.seen != 0x10000u) return scan_again;
        }
    }
    const uint32_t closed = close_name_scan(scan, mounted);
    return closed == P::s_ok ? code : closed;
}

volatile U32 command_sequence __attribute__((section(".data"))) = 0;
Reply serving_start(uint32_t& expected_ack) {
    if (left_session) return outcome(busy);
    const U32 value = U32(Stock121PeerSession::command_tag) << 16 | (storage_server::command_item & 0xffu) << 8 |
                      (++command_sequence << 3 & 0xf8u);
    expected_ack = peer_services::command_ack(value);
    Stock121PeerSession* session = 0;
    const Reply s = partition_out(session, 0x18, value, 0, 0);
    return leave(session, s, request_ms);
}
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

uint32_t read_file(const char* path, uint32_t offset, bool ranged, uint8_t* out, uint32_t capacity, uint32_t& bytes,
                   volatile uint32_t& mounted, Open* left) {
    bytes = 0;
    if (left) {
        left->fd = -1;
        left->probe = false;
    }
    if (!storage_server::valid_path(path)) return xyf::usbp::s_bad_request;
    if (file_engine::decide_engine(file_engine::op_read, path) != file_engine::allow) return xyf::usbp::s_refused;
    const uint32_t ready = mount_ready(mounted);
    if (ready != xyf::usbp::s_ok) return ready;
    // Past its bound a request stays out: A may still answer it (an open: a handle B never closes).
    auto failed = [&mounted](const Reply& r) -> uint32_t {
        if (r.outcome == link) mounted = mounted_stale;
        return status(r.outcome);
    };
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
        if (!room && ranged && left) {
            left->fd = fd;
            left->probe = true;
            return code;
        }
        const Reply g = read(fd, room ? out + bytes : probe, room ? (room < chunk ? room : chunk) : 1u);   // op 0x2a
        if (g.outcome) return failed(g);
        if (g.value < 0) {
            code = xyf::usbp::s_storage;
            break;
        }
        if (!g.value) break;
        if (!room) {
            code = ranged ? xyf::usbp::s_ok : xyf::usbp::s_too_large;
            break;
        }
        bytes += U32(g.value);
    }
    if (left) {
        left->fd = fd;
        return code;
    }                            // closed in finish_read
    const Reply c = close(fd);                                           // op 0x2d
    if (c.outcome) return failed(c);
    return code;
}
void finish_read(const Open& left, volatile uint32_t& mounted) {
    if (left.fd < 0) return;
    uint8_t probe[4];
    if (left.probe && read(left.fd, probe, 1).outcome == link) {
        mounted = mounted_stale;
        return;
    }   // op 0x2a
    if (close(left.fd).outcome == link) mounted = mounted_stale;                                       // op 0x2d
}

uint32_t list(const char* path, uint32_t skip, uint8_t* out, uint32_t capacity, uint32_t& count, uint32_t& more,
              uint32_t& bytes, volatile uint32_t& mounted) {
    count = 0;
    more = 0;
    bytes = 0;
    if (file_engine::decide_engine(file_engine::op_list, path) != file_engine::allow) return xyf::usbp::s_refused;
    const uint32_t ready = mount_ready(mounted);
    if (ready != xyf::usbp::s_ok) return ready;
    auto failed = [&mounted](const Reply& r) -> uint32_t {
        if (r.outcome == link) mounted = mounted_stale;
        return status(r.outcome);
    };
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
        if (got == folder_error) {
            code = xyf::usbp::s_storage;
            break;
        }
        if (seen < skip) continue;
        if (count == max_page) {
            more = 1;
            break;
        }
        entry[11 + max_name] = 0;
        bool have = false;
        if (child_path(path, reinterpret_cast<const char*>(entry + 11), child)) {
            const Reply q = stat(child, record);                         // op 0x22 (a refused name: no record)
            if (q.outcome == link || q.outcome == busy || q.outcome == lost) return failed(q);
            have = q.outcome == done && q.value == 0;
        }
        const U32 n = list_entry(entry, have, record, out + bytes, capacity - bytes);
        if (!n) {
            more = 1;
            break;
        }
        bytes += n;
        ++count;
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
#include "file_client_write.ipp"   // the write commands
#endif
}}
#endif

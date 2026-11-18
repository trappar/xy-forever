#include "storage_server.hpp"
#include "cold_heap.hpp"
#if defined(XYF_USB_SERVICE)
#include "usb_service.hpp"
#endif
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
#include "mtp_spans.hpp"
#endif
#if defined(XYF_MTP)
#include "xyf_boot.hpp"                 // clock_ms(): a's session clock for run_engine
#endif
// Processor a's storage server and the file engine (see storage_server.hpp). Stock addresses:
// the authenticated 1.2.1 bootloader, mapped offline (docs/hardware/storage-server.md, Stock storage server requests).
namespace xy_forever { namespace storage_server {
#if defined(__bfin__)
// Initialized data, not BSS: the cold link has no zero-initialization.
Result result __attribute__((section(".data"))) = {magic};
#endif

namespace {
bool prefix(const char* s, const char* p) { for (; *p; ++p, ++s) if (*s != *p) return false; return true; }
}

bool valid_path(const char* path) {
    if (!path || !prefix(path, "/yaffs2/")) return false;
    uint32_t component = 8;
    for (uint32_t i = 8; i != max_path; ++i) {
        const char c = path[i];
        if (c == '/' || !c) {
            const uint32_t n = i - component;
            if (!n || (n == 1 && path[component] == '.') ||
                (n == 2 && path[component] == '.' && path[component + 1] == '.')) return false;
            if (!c) return true;
            component = i + 1;
        }
    }
    return false;                                   // no NUL within max_path
}

static_assert(max_path == file_engine::max_path && path_buffer == file_engine::path_buffer &&
              partition_class == file_engine::partition_class && path_class == file_engine::path_class &&
              read_class == file_engine::read_class && sector_class == file_engine::sector_class &&
              write_class == file_engine::write_class, "the engine decides on the server's request shapes");

#if defined(XYF_USB_FILE_WRITE)
// The write rule (since 1.13): the USB write area is all of /yaffs2; decide_engine refuses
// the marker's changes and /yaffs2/upgrade (file_engine.hpp).
uint32_t write_root(const char* path) { return valid_path(path) ? 8 : 0; }
uint32_t own_area(const char* path) {
    static const char xyf[] = "/yaffs2/xy-forever/", content[] = "/yaffs2/content/";
    if (!valid_path(path)) return 0;
    const uint32_t root = prefix(path, xyf) ? sizeof(xyf) - 1 : prefix(path, content) ? sizeof(content) - 1 : 0;
    if (!root || !path[root]) return 0;
    for (uint32_t i = 8; path[i]; ++i) {                  // no name holding "bootloader" (any case)
        static const char word[] = "bootloader";
        uint32_t k = 0;
        while (word[k] && (path[i + k] | 0x20) == word[k]) ++k;
        if (!word[k]) return 0;
    }
    return root;
}
bool valid_change_path(const char* path, file_engine::Operation op) {
    return write_root(path) && file_engine::decide_engine(op, path) == file_engine::allow;
}
bool valid_write_path(const char* path) { return valid_change_path(path, file_engine::op_write); }
bool valid_rename(const char* from, const char* to) {
    return write_root(from) && write_root(to) &&
           file_engine::decide_engine(file_engine::op_rename, from, to) == file_engine::allow;
}
#endif

#if defined(XYF_USB_SERVICE)
InstallAction decide_install(uint32_t vtable, uint32_t op, uint32_t argument, uint32_t selected) {
    const bool boot = selected == 1 || selected == 2;
    if (vtable == partition_class) {
        if (op == 0x18) return argument <= 2 ? install_select : install_refuse_partition;
        if (op == 0x14) return boot ? install_trim : install_refuse_selected;
        return boot ? install_refuse_files : install_none;     // 8/9/0x2c/0x2d: read-only allowlist
    }
    if (vtable == sector_class) return (op == 0x11 || op == 0x12) ? install_read : install_refuse_op;
    if (vtable == write_class) {
        if (op != 0x15 && op != 0x16) return install_refuse_op;
        return boot ? install_write : install_refuse_selected;
    }
    return boot ? install_refuse_files : install_none;         // path/read classes only on partition 0
}
#endif
} }

#if defined(__bfin__)
namespace xy_forever { namespace storage_server { namespace a_side {
typedef peer_services::U32 U32;      // unsigned int (uint32_t is unsigned long on bfin)
// One noinline, noclone instantiation per stock entry (straight-line call audit). Named (not
// anonymous): the builder scopes a's own posix calls (the marker check) to these wrappers.
template<U32 A, class R, class... Args> __attribute__((noinline, noclone)) R stock(Args... args) {
    return reinterpret_cast<R(*)(Args...)>(A)(args...);
}
#if defined(XYF_USB_FILE_WRITE)
// The admitted yaffs rename (xyf_build.py A_RENAME_CALLS): 889fa834(old, new)
// -> yaffsfs rename 889fa604 -> yaffs_rename_obj 88a01afa (1.2.1 listing). Stock convention: 0 done,
// -1 failed with the negative error number in yaffs's error word (889f998c stores it at 0x8806f940,
// 889f9982 reads it). Called only while every engine span matches (mtp_spans.hpp: they cover
// 889fa604..889fa848 and 88a01afa with what they reach); else -2 and nothing ran. noinline: the
// builder allows the call only from this namespace.
__attribute__((noinline)) int rename_object(const char* from, const char* to) {
    if (engine_identity()) return -2;
    return stock<0x889fa834u, int>(from, to);
}
#endif
} } }
namespace {
using namespace xy_forever;
using namespace xy_forever::storage_server;
using peer_services::Stock121PeerSession;
using a_side::stock;
typedef peer_services::U32 U32;
volatile U32& word(U32 address) { return *reinterpret_cast<volatile U32*>(address); }
volatile uint8_t& byte(U32 address) { return *reinterpret_cast<volatile uint8_t*>(address); }
U32 crc(U32 address, U32 count) {
    U32 value = 0xffffffffu;
    for (U32 i = 0; i < count; ++i) {
        value ^= byte(address + i);
        for (U32 b = 0; b < 8; ++b) value = (value >> 1) ^ (0xedb88320u & (0u - (value & 1u)));
    }
    return ~value;
}
// Stock code reached through the served/client calls, beyond the peer
// session's own identity() spans (which cover 889f4d00..889f9300 with the
// executors 889f76ca/889f6fea/889f6cd6, the slave factory and the class
// tables). Ranges equal the image in all 18 cold census files (017-024),
// excluding the mutable words 0x88a2ef28 and L2 data 0x0803ca38..0x0803cb5c.
// Never L1 instruction memory (0x11a00000..0x11a0c000).
const U32 spans[][3] = {
    {0x889efd00u, 0x5000u, 0xa9bc819cu},   // posix open/read/close/lseek, provider methods, registry
    {0x889f9300u, 0x2e00u, 0x2782f027u},   // yaffs core (open/read/close/lseek/unmount)
    {0x88a2ec00u, 0x328u, 0x1833ea69u},    // allocator (operator new/malloc)
    {0x88a2ef2cu, 0x12d4u, 0xecc12af7u},   // allocator, operator new 88a2f6e8
    {0x0803c000u, 0xa38u, 0x25ff9e48u},    // L2 libc code
    {0x0803cb5cu, 0x150cu, 0x97eecfe0u},   // L2 libc code
    {0x11906448u, 0x40u, 0xd0dcc25eu},     // stock yaffs provider vtable
};
U32 mismatch() {
    U32 bits = 0;
    for (U32 i = 0; i != sizeof(spans) / sizeof(spans[0]); ++i)
        if (crc(spans[i][0], spans[i][1]) != spans[i][2]) bits |= 1u << i;
    return bits;
}

// Role 0 state (initialized data; one mount per boot).
volatile U32 provider_object __attribute__((section(".data"))) = 0;    // stock: malloc(4) = {vtable}
volatile U32 mounted __attribute__((section(".data"))) = 0;
volatile U32 used __attribute__((section(".data"))) = 0;
// Test 065: a (file boot) owns one fresh read-only storage session for the whole link session;
// the server adopts it: op 8 registers the stock provider once, op 9 only closes b's files.
volatile U32 adopted __attribute__((section(".data"))) = 0;
volatile U32 provider_registered __attribute__((section(".data"))) = 0;
// The files and folders a opened for b (the engine admits handle requests only for these).
volatile int fds[max_fds] __attribute__((section(".data"))) = {-1, -1, -1, -1};
volatile U32 dirs[max_fds] __attribute__((section(".data"))) = {0, 0, 0, 0};       // stock DIR pointers

bool known(int fd) { for (U32 i = 0; i != max_fds; ++i) if (fd >= 0 && fds[i] == fd) return true; return false; }
bool space() { for (U32 i = 0; i != max_fds; ++i) if (fds[i] < 0) return true; return false; }
void track(int fd) { for (U32 i = 0; i != max_fds; ++i) if (fds[i] < 0) { fds[i] = fd; ++result.open_fds; return; } }
void untrack(int fd) { for (U32 i = 0; i != max_fds; ++i) if (fds[i] == fd) { fds[i] = -1; --result.open_fds; } }
bool dir_known(U32 dir) { for (U32 i = 0; i != max_fds; ++i) if (dir && dirs[i] == dir) return true; return false; }
bool dir_space() { for (U32 i = 0; i != max_fds; ++i) if (!dirs[i]) return true; return false; }
void track_dir(U32 dir) { for (U32 i = 0; i != max_fds; ++i) if (!dirs[i]) { dirs[i] = dir; return; } }
void untrack_dir(U32 dir) { for (U32 i = 0; i != max_fds; ++i) if (dirs[i] == dir) dirs[i] = 0; }
// b's leftovers, through the stock posix layer (noinline: the builder scopes a's posix close here).
__attribute__((noinline)) void close_handles() {
    for (U32 i = 0; i != max_fds; ++i)
        if (fds[i] >= 0) { stock<0x889f285au, int>(int(fds[i])); fds[i] = -1; }   // stock posix close
#if defined(XYF_MTP)
    for (U32 i = 0; i != max_fds; ++i)
        if (dirs[i]) { stock<0x889f44f4u, int>(U32(dirs[i])); dirs[i] = 0; }      // stock posix closedir
#endif
    result.open_fds = 0;
}

void note(U32 op, U32 action, U32 value) {
    Result& r = result;
    if (r.log_count < max_log) r.log[r.log_count] = (op & 0xffu) | action << 8 | (value & 0xffffu) << 16;
    ++r.log_count;
}

U32 heap_break() {
    cold_heap::Snapshot heap; cold_heap::Result code;
    return cold_heap::guard(heap, code) ? heap.brk : 0;
}

bool mount(cold_storage::Stock121ReadOnlySession& storage) {
    Result& r = result;
    if (adopted) {
        if (mounted) return true;
        if (!storage.ready()) return false;
        if (!provider_registered) {
            r.identity_mismatch = mismatch();
            if (r.identity_mismatch) return false;
            if (word(0x8803f6b8u) || word(0x8803f6bcu) || word(0x8803f6c0u) || word(0x8803f6c4u) ||
                word(0x1190733cu) != 0x11906448u)
                return false;
            provider_object = 0x11906448u;
            stock<0x889f3910u, void>(&provider_object, 0x119073acu);          // "/yaffs2"
            const U32 begin = word(0x8803f6bcu), end = word(0x8803f6c0u);
            r.registered = end - begin == 8 && word(begin) == reinterpret_cast<U32>(&provider_object) &&
                           word(begin + 4) == 0x119073acu;
            if (!r.registered) return false;
            provider_registered = 1;
        }
        used = 1; mounted = 1;
        return true;
    }
    if (used) return mounted;
    used = 1;
    r.identity_mismatch = mismatch();
    if (r.identity_mismatch) return false;
    // Stock registry at 0x8803f6b8: {0, begin, end, capacity} (889f2afc reads
    // begin at +4, 889f2b14 end at +8; Test 033 a: {0, 88b189c0, 88b189c8,
    // 88b189c8} after one registration). All zero at every cold entry (Tests
    // 023/024/031/032), and the stock op-8 provider vtable must be the
    // CRC-checked one.
    if (word(0x8803f6b8u) || word(0x8803f6bcu) || word(0x8803f6c0u) || word(0x8803f6c4u) ||
        word(0x1190733cu) != 0x11906448u)
        return false;
    r.heap_before = heap_break();
    r.mount_begin = U32(storage.begin_fresh(services::Phase::cold_after_teardown));
    if (r.mount_begin != U32(cold_storage::Result::ok) || !storage.ready()) {
        r.mount_end = U32(storage.end());
        return false;
    }
    provider_object = 0x11906448u;
    stock<0x889f3910u, void>(&provider_object, 0x119073acu);              // "/yaffs2"
    const U32 begin = word(0x8803f6bcu), end = word(0x8803f6c0u);       // Test 033: was +0/+4 (always failed)
    r.registered = end - begin == 8 && word(begin) == reinterpret_cast<U32>(&provider_object) &&
                   word(begin + 4) == 0x119073acu;
    if (!r.registered) { r.mount_end = U32(storage.end()); return false; }
    mounted = 1;
    return true;
}

void unmount(cold_storage::Stock121ReadOnlySession& storage) {
    Result& r = result;
    if (!mounted) return;
    close_handles();
    if (adopted) { mounted = 0; return; }            // Test 065: the file boot ends the shared mount
    r.mount_end = U32(storage.end());
    r.mount_safe = storage.safe_to_handoff() && r.mount_end == U32(cold_storage::Result::ok);
    r.denied_writes = r.storage.denied_writes;
    r.heap_after = heap_break();
    mounted = 0;
}

#if defined(XYF_USB_SERVICE)
// Install mode (a): exact Track-5 request set, see storage_server.hpp.
volatile U32 install_enabled __attribute__((section(".data"))) = 0;
volatile U32 selected_partition __attribute__((section(".data"))) = 0;
// A select of a boot partition was attempted and no select of 0 has succeeded since. The
// stock select may have switched the chip's partition register before it failed, so a failed
// select counts as selected for storage (boot_selected); boot writes still need a confirmed one.
volatile U32 select_attempted __attribute__((section(".data"))) = 0;

bool serve_install(U32 request, U32 vtable, U32 op, Stock121PeerSession& session) {
    usb_service::Result& u = usb_service::result;
    const InstallAction action = decide_install(vtable, op, word(request + 8), selected_partition);
    if (action == install_none) return false;
    const U32 executor = action == install_select || action == install_trim ? 0x889f76cau :
                         action == install_read ? 0x889f6b7cu : action == install_write ? 0x889f6dcau : 0;
    if (!executor || word(vtable + 0x14) != executor) {
        session.reply(request, 0xffffffffu, error_access);
        ++u.a_refused; u.a_last_refused = op | vtable << 8;
        note(op, 0x40u + U32(action), 0xfffeu);
        return true;
    }
    switch (action) {
    case install_select: {
        const U32 argument = word(request + 8);
        if (argument) select_attempted = 1;                     // before the stock call, whatever it answers
        stock<0x889f76cau, void>(request);
        // Stock reply = 88a08710's result: 0 = switched (Test 036: the writer only
        // continues on 0; "nonzero = selected" refused every trim after it). A failed select
        // leaves the chip's partition unknown: no boot write until a select succeeds.
        if (word(request + 0x10) == 0) {
            selected_partition = argument;
            if (!argument) select_attempted = 0;
        } else selected_partition = 0;
        ++u.a_selects; u.a_selected = selected_partition | select_attempted << 8;
        break;
    }
    case install_trim: stock<0x889f76cau, void>(request); ++u.a_trims; break;
    case install_read: stock<0x889f6b7cu, void>(request); ++u.a_reads; break;
    default: stock<0x889f6dcau, void>(request); ++u.a_writes; break;
    }
    ++result.executed; note(op, 0x40u + U32(action), word(request + 0x10));   // 0x4n: install
    return true;
}
#endif
// Install mode has a boot partition selected. yaffs (partition 0) is then not what the
// chip's partition register addresses: no engine request, no window, no state write.
bool boot_selected() {
#if defined(XYF_USB_SERVICE)
    return selected_partition != 0 || select_attempted != 0;
#else
    return false;
#endif
}

// The file engine (a): see storage_server.hpp. Entries: 0 none, 1 read-only, 2 one write
// window (file_write_item), 3 held window (engine_run_item, run_engine).
const U32 entry_none = 0, entry_read = 1, entry_write = 2, entry_run = 3;
volatile U32 engine __attribute__((section(".data"))) = 0;
volatile U32 engine_latched __attribute__((section(".data"))) = 0;    // commands_seen of the entry
volatile U32 engine_refused __attribute__((section(".data"))) = 0;    // the stock code check failed
// a's own copy of a path or query request's path. The stock request stage receives every
// such body into the one stock buffer from the interrupt (889f8d94 stage 1 sets +0x18 to
// 0x8803f6e0), so a later request could change it between the check and the executor. a decides
// on this copy and points the request at it: both executors read the path only through +0x18
// (889f6fea, 889f72e4), and the next request's stage 1 sets +0x18 afresh.
char path_copy[max_path] __attribute__((section(".data"))) = {0};
#if defined(XYF_USB_FILE_WRITE)
// file.rename's source (a's copy, from its rename_from request), armed for the next request only.
char rename_source[max_path] __attribute__((section(".data"))) = {0};
volatile U32 rename_armed __attribute__((section(".data"))) = 0;
#endif
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
volatile U32 window_open __attribute__((section(".data"))) = 0;
volatile U32 window_step __attribute__((section(".data"))) = 0;
volatile U32 window_result __attribute__((section(".data"))) = 0;
// A request reached the open window since a's loop last looked (tick), a's clock then, and a
// window opened since the last rescan (library_changed).
volatile U32 window_touched __attribute__((section(".data"))) = 0;
volatile U32 window_since __attribute__((section(".data"))) = 0;
volatile U32 library_dirty __attribute__((section(".data"))) = 0;
#endif
#if defined(XYF_MTP)
static const U32 dirent_bytes = 0x10cu;                              // stock readdir reply body
volatile U32 dirent_zero[0x120 / 4] __attribute__((section(".data"), aligned(32))) = {};   // 128
#endif

// Latch the entry b's latest command names (one latch per command; the run entry ends only
// in run_engine). Writable entries need the engine's stock code to match (mtp_spans.hpp).
void latch(Stock121PeerSession& session) {
    Result& r = result;
    const U32 seen = session.commands_seen(), item = session.last_command() >> 8 & 0xffu;
    if (!seen || seen == engine_latched || engine == entry_run) return;
    U32 entry = item == command_item && engine == entry_none ? entry_read : entry_none;
#if defined(XYF_USB_FILE_WRITE)
    if (item == file_write_item && engine != entry_write) entry = entry_write;   // Test 066: once per item
#endif
#if defined(XYF_MTP)
    if (item == engine_run_item) entry = entry_run;
#endif
    if (entry == entry_none) return;
    engine_latched = seen;
    engine_refused = 0;
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
    if (entry != entry_read) {
        r.identity_mismatch = engine_identity();
        engine_refused = r.identity_mismatch != 0;
    }
#endif
    engine = entry;
    r.enabled = entry | (engine_refused ? 0x10u : 0u);
}

#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
// The read-write window on a's read-only mount (fresh, or the adopted one): op 8 in the run entry;
// in the write entry the first request that changes storage. The write entry pauses a's
// guard around the read-write mount and unmount (Test 049: a long mount or checkpoint is not a
// hang); the run entry keeps it (097).
__attribute__((noinline)) bool open_window(cold_storage::Stock121ReadOnlySession& storage) {   // noinline: builder scope
    if (window_open) return window_result == U32(cold_storage::Result::ok);   // not after a failed close
    if (boot_selected()) return false;                         // never read-write on a boot partition
    // (009: the window opens only with nothing open, else busy): files or folders b opened in
    // the read-only entry would make the read-only unmount fail and end a's storage for this boot.
    // They stay b's; op 9 closes them.
    if (held_files()) return false;
    if ((used && !adopted) || !mount(storage)) return false;   // v3: the adopted mount is reusable
    const bool was = engine == entry_write ? usb_service::guard_pause() : false;
    services::U32 step = 0;
    const cold_storage::Result w = storage.open_write_window(step);
    window_step = step; window_result = U32(w);
    // A failed read-write mount has already restored the gates. Restore the shared read-only
    // mount too; identity, refusal or unmount failures must not attempt a new mount.
    if (w == cold_storage::Result::mount && adopted) {
        const cold_storage::Result resumed = storage.reopen_read_only();
        if (resumed != cold_storage::Result::ok) window_result = U32(resumed);
    }
    usb_service::guard_resume(was);
    if (w != cold_storage::Result::ok) { unmount(storage); return false; }
    window_open = 1; window_touched = 1;
    if (engine == entry_write) library_dirty = 1;              // (the run entry rescans after itself)
    return true;
}
#endif

// Op 9 (or any end): close b's leftovers, close the read-write window (yaffs unmount writes its
// checkpoint; gates back; the shared read-only mount again), then end the session (end; not
// when a closes the window between requests). A window that fails to close stays mounted (never
// hand off read-write). noinline: the builder finds a's guard pause only here and in open_window.
__attribute__((noinline)) void close_engine(cold_storage::Stock121ReadOnlySession& storage, bool end = true) {
    if (!mounted) return;
    close_handles();
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
    if (window_open) {
        const bool was = engine == entry_write ? usb_service::guard_pause() : false;
        window_result = U32(storage.close_write_window());
        if (window_result == U32(cold_storage::Result::ok)) {
            window_open = 0;
            if (adopted) window_result = U32(storage.reopen_read_only());
        }
        usb_service::guard_resume(was);
        note(9, 0x20u, window_result);                       // 0x20: a window close (any trigger)
        if (window_open) return;
    }
#endif
    if (end) unmount(storage);
}
#if defined(XYF_USB_FILE_WRITE)
// a closes the window between requests (idle, another command item). A failed close keeps it
// open (reported in a's log; retried after the next idle time; the restart paths still reset, the
// boot paths refuse); a failed read-only reopen ends the session's mount, as op 9 reports it.
void close_between(cold_storage::Stock121ReadOnlySession& storage) {
    const bool had = window_open != 0;
    close_engine(storage, false);
    if (had && !window_open && window_result != U32(cold_storage::Result::ok)) unmount(storage);
}
#endif

#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
// The dev-menu marker around every unlink and rmdir (the operations that remove a name;
// and rename, rename_now). The policy checks the spelling, but yaffs follows symlinks and hardlinks inside a
// path (/yaffs2/s/bootloader.dev with s -> /yaffs2). "The marker exists" = the stock posix stat of
// marker_path succeeds: whatever object the name resolves to (file, folder, links followed), as the
// 1.2.1 bootloader's access("bootloader.dev", R_OK) (88a1d7e6) finds it. Before the operation: a
// path that stats to the marker's own record is refused. After it: a marker that existed before and
// is gone is re-created (empty; stock "wb", mode 0666). A marker that did not exist before (removed
// through the stock dev menu, the owner's right) is never created. Lowercase, the name the
// bootloader asks for (88a202b0): yaffs compares names case-sensitively (file_engine.hpp).
const char marker_path[] = "/yaffs2/bootloader.dev";
static const U32 record_words = 0x80 / 4;                             // >= the stock stat record (0x34)
U32 marker_record[2][record_words] __attribute__((section(".data"))) = {};
__attribute__((noinline)) bool stat_into(const char* path, U32* record) {   // noinline: the builder's scope
    for (U32 i = 0; i != record_words; ++i) record[i] = 0;
    return stock<0x889f3516u, int>(path, record) == 0;                 // stock posix stat (on a: yaffs)
}
bool same_object() {
    for (U32 i = 0; i != record_words; ++i) if (marker_record[0][i] != marker_record[1][i]) return false;
    return true;
}
// The marker vanished although its spellings are refused: put it back. The executor has already
// replied (it queues its own answer), so the refusal is in a's log (0xfff9 restored, 0xfff8 not).
__attribute__((noinline)) void restore_marker(U32 op) {
    const int fd = stock<0x889f3b50u, int>(0u, marker_path, 0x8301u);   // stock posix open, "wb"
    if (fd >= 0) stock<0x889f285au, int>(fd);                          // stock posix close
    ++result.refused;
    note(op, file_engine::refuse_protected_path, stat_into(marker_path, marker_record[1]) ? 0xfff9u : 0xfff8u);
}
#endif

#if defined(XYF_USB_FILE_WRITE)
// The two write-rule exceptions through links (yaffs follows them inside a path): a change whose path stats to
// the marker, or whose path or any folder above it stats to /yaffs2/upgrade, is refused. The path is cut in place
// at each '/' and restored.
const char inbox_path[] = "/yaffs2/upgrade";
__attribute__((noinline)) bool reaches_protected(char* path) {
    if (stat_into(marker_path, marker_record[0]) && stat_into(path, marker_record[1]) && same_object()) return true;
    if (!stat_into(inbox_path, marker_record[0])) return false;
    for (U32 i = 9; i != max_path; ++i) {
        const char c = path[i];
        if (c != '/' && c) continue;
        path[i] = 0;
        const bool inside = stat_into(path, marker_record[1]) && same_object();
        path[i] = c;
        if (inside) return true;
        if (!c) break;
    }
    return false;
}
// file.rename on a, after rename_to passed decide_request (the pair's policy; the write window is
// open): both paths in the write area again, the source must exist (ENOENT) and the target must not
// (EEXIST: the stock rename would replace it), the marker by another name is refused and a marker that
// vanishes is re-created (as for unlink and rmdir), then the yaffs rename. 0, or the error number.
const U32 yaffs_error = 0x8806f940u;                                  // 889f998c / 889f9982
const U32 error_noent = 2, error_io = 5, error_exist = 17;
U32 rename_now(char* to) {
    const char* from = rename_source;
    if (!valid_rename(from, to) || reaches_protected(rename_source) || reaches_protected(to)) return error_access;
    if (!stat_into(from, marker_record[1])) return error_noent;
    if (stat_into(to, marker_record[1])) return error_exist;
    const bool marker = stat_into(marker_path, marker_record[0]);
    if (marker && stat_into(from, marker_record[1]) && same_object()) {
        note(0x21, file_engine::refuse_protected_path, 0xfffau);
        return error_access;
    }
    const int r = a_side::rename_object(from, to);
    U32 error = 0;
    if (r == -2) { note(0x21, file_engine::refuse_off, 0xfff7u); error = error_io; }   // spans differ: not called
    else if (r < 0) { error = U32(-int32_t(word(yaffs_error))); if (!error || error > 0xffffu) error = error_io; }
    if (marker && !stat_into(marker_path, marker_record[1])) restore_marker(0x21);
    return error;
}
#endif

// Every request in the engine: file_engine::decide_request (with the path policy) first, then
// the class's stock executor, which must be the CRC-checked one (vtable +0x14).
void serve_engine(U32 request, U32 vtable, U32 op, cold_storage::Stock121ReadOnlySession& storage,
                  Stock121PeerSession& session) {
    Result& r = result;
    const bool writable = engine == entry_write || engine == entry_run;
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
    const bool window = writable && mounted && window_open && window_result == U32(cold_storage::Result::ok);
#else
    const bool window = false;
#endif
    const U32 path_pointer = vtable == path_class || vtable == file_engine::query_class ? word(request + 0x18) : 0;
    if (path_pointer == path_buffer) {
        for (U32 i = 0; i != max_path; ++i) if (!(path_copy[i] = char(byte(path_buffer + i)))) break;
        word(request + 0x18) = reinterpret_cast<U32>(path_copy);
    }
    const U32 handle = word(request + 8);
#if defined(XYF_USB_FILE_WRITE)
    const char* source = rename_armed ? rename_source : 0;           // the request just before only
    rename_armed = 0;
#else
    const char* source = 0;
#endif
    file_engine::State state;
    state.on = engine != entry_none && !engine_refused;
    state.session = engine == entry_run ? window : mounted != 0;      // the write entry opens it lazily
    state.window = window;
    state.fd_known = known(int(handle)); state.fd_space = space();
#if defined(XYF_MTP)
    state.dir_known = dir_known(handle); state.dir_space = dir_space();
#else
    state.dir_known = 0; state.dir_space = 0;                // no folder requests without XYF_MTP
#endif
    state.partition = boot_selected();
    file_engine::Action action;
    for (;;) {
        action = file_engine::decide_request(vtable, op, handle, path_pointer,
            path_pointer == path_buffer ? path_copy : 0, state, source);
#if defined(XYF_USB_FILE_WRITE)
        // The write entry's first change opens the window (the policy has passed:
        // refuse_read_only comes after it); the request is then decided again with the window open.
        if (action == file_engine::refuse_read_only && engine == entry_write && !state.window && open_window(storage)) {
            state.window = true;
            continue;
        }
#endif
        break;
    }
#if defined(XYF_USB_FILE_WRITE)
    if (window_open) window_touched = 1;
#endif
    if (action == file_engine::refuse_raw_layer && engine == entry_run) {
        // b's bootloader (b restarted alone): no answer. run_engine closes storage and restarts
        // a; the request, still in flight on b, is then answered by a's bootloader.
        session.follow(request); ++r.refused; note(op, action, 0xfffbu);
        return;
    }
#if defined(XYF_USB_FILE_WRITE)
    // B's write area on A too, outside the run entry (diag.engine_test, MTP): open for
    // writing, unlink, mkdir and rmdir only where write_root passes and no link reaches the marker or the inbox
    // (reaches_protected; rename: rename_now), whatever B checked.
    const bool change = (action == file_engine::run_open && handle) ||
                        (action == file_engine::run_path && (op == 0x25u || op == 0x26u || op == 0x27u));
    if (change && engine != entry_run && (!write_root(path_copy) || reaches_protected(path_copy))) {   // (on path_copy)
        session.reply(request, 0xffffffffu, error_access); ++r.refused; note(op, file_engine::refuse_outside_path, 0xfff6u);
        return;
    }
#endif
    const U32 executor = action == file_engine::run_file || action == file_engine::run_closedir ? 0x889f76cau :
        action == file_engine::run_open || action == file_engine::run_opendir || action == file_engine::run_path ?
            0x889f6feau :
        action == file_engine::run_query ? 0x889f72e4u : action == file_engine::run_read ? 0x889f6cd6u :
        action == file_engine::run_readdir ? 0x889f6b7cu : action == file_engine::run_write ? 0x889f6dcau : 0;
    if (executor && word(vtable + 0x14) != executor) {
        session.reply(request, 0xffffffffu, error_access); ++r.refused; note(op, file_engine::refuse_class, 0xffffu);
        return;
    }
    switch (action) {
    case file_engine::run_mount: {
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
        const bool ok = engine == entry_run ? open_window(storage) : mount(storage);
#else
        const bool ok = mount(storage);
#endif
        session.reply(request, ok ? 0 : 0xffffffffu, ok ? 0 : held_files() ? error_busy : error_access);   // 0 = mounted
        ++r.executed; note(op, action, ok ? 0 : 0xffffu);
        return;
    }
    case file_engine::run_unmount: {
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
        const bool had = window_open != 0;
        close_engine(storage);
        // As build 154: failure while the window is still open, and when this close's
        // read-only reopen failed (a's storage is then unmounted).
        const bool ok = !window_open && (!had || window_result == U32(cold_storage::Result::ok));
        if (!window_open && engine == entry_write) engine = entry_read;   // Test 066: one write per item
#else
        close_engine(storage);
        const bool ok = true, had = false;
#endif
        // Stock success is 1 (889f7710); 3 when this op 9 closed a window.
        session.reply(request, ok ? (had ? 3 : 1) : 0, ok ? 0 : error_access);
        ++r.executed; note(op, action, mounted ? 0xfffeu : 1);
        return;
    }
    case file_engine::run_file:
        if (op == 0x2d) untrack(int(handle));
        stock<0x889f76cau, void>(request);                                    // queues its own reply
        break;
    case file_engine::run_open: {
        stock<0x889f6feau, void>(request);
        const int opened = int(word(request + 0x10));                         // fd (stock reply value)
        if (opened >= 0) track(opened);
        break;
    }
    case file_engine::run_path: {                                             // access, unlink, mkdir, rmdir
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
        const bool marker = (op == 0x25u || op == 0x27u) && stat_into(marker_path, marker_record[0]);
        if (marker && stat_into(path_copy, marker_record[1]) && same_object()) {
            session.reply(request, 0xffffffffu, error_access);
            ++r.refused; note(op, file_engine::refuse_protected_path, 0xfffau);   // the marker by another name
            return;
        }
        stock<0x889f6feau, void>(request);
        if (marker && !stat_into(marker_path, marker_record[1])) restore_marker(op);
#else
        stock<0x889f6feau, void>(request);
#endif
        break;
    }
#if defined(XYF_USB_FILE_WRITE)
    case file_engine::run_rename_from: {                                      // keep the source
        const bool ok = writable && write_root(path_copy);
        if (ok) { for (U32 i = 0; i != max_path; ++i) if (!(rename_source[i] = path_copy[i])) break; rename_armed = 1; }
        session.reply(request, ok ? 0 : 0xffffffffu, ok ? 0 : error_access);
        ++(ok ? r.executed : r.refused); note(op, action, ok ? 0 : 0xffffu);
        return;
    }
    case file_engine::run_rename_to: {                                        // rename it
        const U32 error = rename_now(path_copy);
        session.reply(request, error ? 0xffffffffu : 0, error);
        ++r.executed; note(op, action, error ? 0xff00u | error : 0);
        return;
    }
#endif
    case file_engine::run_read: stock<0x889f6cd6u, void>(request); break;     // may re-post itself (889f66fc)
    case file_engine::run_write: stock<0x889f6dcau, void>(request); break;
#if defined(XYF_MTP)
    case file_engine::run_closedir:
        stock<0x889f76cau, void>(request);
        untrack_dir(handle);
        break;
    case file_engine::run_opendir: {
        stock<0x889f6feau, void>(request);
        const U32 dir = word(request + 0x10);                                 // stock DIR pointer, 0 = failed
        if (dir) track_dir(dir);
        break;
    }
    case file_engine::run_query: stock<0x889f72e4u, void>(request); break;
    case file_engine::run_readdir: stock<0x889f6b7cu, void>(request); break;
#endif
    default: {
        const U32 error = action == file_engine::refuse_handle || action == file_engine::refuse_mount ? error_badf :
                          action == file_engine::refuse_full ? error_mfile :
                          action == file_engine::refuse_read_only ? error_rofs : error_access;
#if defined(XYF_MTP)
        if (vtable == file_engine::query_class) {
            // 131: the query class always answers with a body too (stock 889f72e4: 0x22 stat
            // 0x34 bytes, 0x23 statfs 0x0c, 0x31 a 64-bit value 8). Refuse with a zeroed one
            // of that size (stat/statfs -1 with the errno; 0x31 value 0).
            const U32 bytes = op == 0x23u ? 0x0cu : op == 0x31u ? 8u : 0x34u;
            for (U32 i = 0; i != dirent_bytes / 4; ++i) dirent_zero[i] = 0;
            stock<0x889f4f10u, void>(reinterpret_cast<U32>(dirent_zero), bytes);
            word(request + 0x20) = reinterpret_cast<U32>(dirent_zero); word(request + 0x24) = bytes;
            session.reply(request, op == 0x31u ? 0u : 0xffffffffu, error);
            ++r.refused; note(op, action, 0xfffcu);
            return;
        }
        if (vtable == sector_class && op == 0x2fu) {
            // 128: b's readdir always waits for a 0x10c-byte entry (stock 889f6b7c sends one
            // even at the end of a folder). A refusal without it froze b inside its USB
            // interrupt (126/127). Refuse with a zeroed entry, the errno and result -1 (not
            // the stock end of folder, 0, so B's file.list reports it; the stock result is A's
            // dirent address, any nonzero word, so the body travels the same way).
            for (U32 i = 0; i != dirent_bytes / 4; ++i) dirent_zero[i] = 0;
            stock<0x889f4f10u, void>(reinterpret_cast<U32>(dirent_zero), dirent_bytes);   // as stock (cache)
            word(request + 0x20) = reinterpret_cast<U32>(dirent_zero); word(request + 0x24) = dirent_bytes;
            session.reply(request, 0xffffffffu, error);
            ++r.refused; note(op, action, 0xfffdu);
            return;
        }
#endif
        session.reply(request, 0xffffffffu, error);
        ++r.refused; note(op, action, 0xffffu);
        return;
    }
    }
    ++r.executed; note(op, action, word(request + 0x10));
}

void serve(void* context, U32 request, Stock121PeerSession& session) {
    Result& r = result;
    cold_storage::Stock121ReadOnlySession& storage = *static_cast<cold_storage::Stock121ReadOnlySession*>(context);
    ++r.requests;
    latch(session);
    const U32 vtable = word(request), op = byte(request + 5);
#if defined(XYF_USB_SERVICE)
    if (!install_enabled && session.commands_seen() && (session.last_command() >> 8 & 0xffu) == install_item) {
        usb_service::result.a_identity = usb_service::identity();
        install_enabled = usb_service::result.a_identity == 0;
        usb_service::result.a_install_enabled = install_enabled;
        if (install_enabled && engine == entry_none) {     // install mode also serves reads
            engine = entry_read; r.enabled = entry_read;
        }
    }
    // The exemption from F1 holds only while the boot-partition writer is active (b's latest
    // command is install_item; b sends command_item once the install ends, failed or not) or a boot
    // partition is selected, not for the rest of the boot.
    const bool install_mode = install_enabled && engine != entry_write && engine != entry_run &&
        ((session.last_command() >> 8 & 0xffu) == install_item || boot_selected());
#else
    const bool install_mode = false;
#endif
    // F1: outside install mode a boot request is b's stock bootloader (b
    // restarted alone): no answer; the session holds it and the owner closes storage and restarts a.
    if (!install_mode && peer_services::boot_request(vtable, op, word(request + 8), word(request + 0x18))) {
        session.follow(request); ++r.refused; note(op, file_engine::refuse_raw_layer, 0xfffbu);
        return;
    }
#if defined(XYF_USB_SERVICE)
    // Install mode answers the boot-partition writer's requests (and refuses file requests
    // while a boot partition is selected), except while a writable engine entry is open.
    if (install_enabled && engine != entry_write && engine != entry_run && serve_install(request, vtable, op, session))
        return;
#endif
    serve_engine(request, vtable, op, storage, session);
}

}

namespace xy_forever { namespace storage_server {
void install(Stock121PeerSession& session, cold_storage::Stock121ReadOnlySession& storage) {
    result.version = 1;
    result.role = 0;
    session.serve(serve, &storage);
}

void finish(cold_storage::Stock121ReadOnlySession& storage) { close_engine(storage); }   // the window too
void adopt() { adopted = 1; }
void close_files(cold_storage::Stock121ReadOnlySession& storage) {
    close_engine(storage);                // the read-write window first (checkpoint), then b's handles
    close_handles();
    mounted = 0;
}
bool ensure_mounted(cold_storage::Stock121ReadOnlySession& storage) { return !boot_selected() && mount(storage); }
uint32_t held_files() {
    uint32_t n = 0;
    for (U32 i = 0; i != max_fds; ++i) n += (fds[i] >= 0 ? 1u : 0u) + (dirs[i] ? 1u : 0u);
    return n;
}
#if defined(XYF_USB_FILE_WRITE)
void tick(cold_storage::Stock121ReadOnlySession& storage, uint32_t now) {
    if (!window_open || engine != entry_write) return;      // the run entry holds its own window (097)
    if (window_touched) { window_touched = 0; window_since = now; return; }
    if (now - window_since < window_idle_ms) return;
    window_since = now;                                     // a failed close is tried again after another idle time
    close_between(storage);
}
void end_writes(cold_storage::Stock121ReadOnlySession& storage) {
    if (engine != entry_write) return;
    if (window_open) close_between(storage);
    if (!window_open) { engine = entry_read; result.enabled = entry_read; }
}
bool library_changed() {
    if (window_open || !library_dirty) return false;
    library_dirty = 0;
    return true;
}
#endif

#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
uint32_t engine_identity() {
    uint32_t bits = 0;
    for (U32 i = 0; i != xyf::mtp_spans::count; ++i) {
        const uint32_t* s = xyf::mtp_spans::table[i];
        if (crc(s[0], s[1]) != s[2]) bits |= 1u << (i & 31u);
    }
    return bits;
}
#endif
#if defined(XYF_MTP)
void run_engine(Stock121PeerSession& session, cold_storage::Stock121ReadOnlySession& storage) {
    // v3: the boot session (RPC mode, no cap) serves b's requests through the serve hook, with
    // no idle window (a refreshes its own activity). engine_leave_item ends the run entry and
    // returns to the read-only entry. Every other way out restarts a, with storage closed first
    // (see storage_server.hpp: the raw layer). a's guard, if armed, is reloaded on every pass.
    U32 seen = session.commands_seen();
    for (;;) {
        usb_service::guard_reload();
        session.touch(xyf_boot::clock_ms());
        const peer_services::Result s = session.poll(xyf_boot::clock_ms());
        if (s != peer_services::Result::waiting && s != peer_services::Result::choose_image) break;
        if (session.follow_now(xyf_boot::clock_ms())) break;   // b restarted: follow it (F1)
        if (session.commands_seen() != seen) {
            seen = session.commands_seen();
            const U32 item = session.last_command() >> 8 & 0xffu;
            if (item == engine_leave_item) {
                close_engine(storage);
                if (window_open) continue;            // still read-write: stay in the run entry
                engine = entry_read;                  // home read-only serving again
                result.enabled = entry_read;
                return;
            }
        }
    }
    close_engine(storage);                // the read-write window first, always
    usb_service::reset_self();            // never returns
}
#endif

} }
#endif

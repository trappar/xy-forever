#include "cold_storage.hpp"
#include "retained_lookup.hpp"
namespace xy_forever { namespace cold_storage {
namespace {
Result controller_ready(const Snapshot& s) {
    if (!s.references || s.references == 0xffffffffu || s.handle != 0x11b00200u)
        return Result::lifecycle;
    if (s.completion != 0x88072d8cu || s.completion_initialized != 1 ||
        (s.adma_control & 0x80u) || s.transfer_address || s.transfer_bytes || s.transfer_error)
        return Result::lifecycle;
    if (!(s.interrupt_mask & (1u << 11)) || (s.source_control & 5u) != 5u ||
        !s.vector_index || s.vector_index >= 96 ||
        s.vector_handler != 0x88a061d6u || s.vector_context != 0x11b00200u)
        return Result::irq;
    return Result::ok;
}
}
Result check_fresh(const Snapshot& s) {
    if (s.role != 0) return Result::role;
    if (s.stock_mounted || s.local_mounted || s.initialized != 1 || s.mounted || s.readonly)
        return Result::lifecycle;
    const Result controller = controller_ready(s);
    if (controller != Result::ok) return controller;
    return s.device ? Result::device : Result::ok;
}
Result check_fresh_active(const Snapshot& before, const Snapshot& after) {
    if (after.role || after.stock_mounted || after.local_mounted || after.initialized != 1 ||
        after.references != before.references || after.handle != before.handle ||
        !before.device || after.device != before.device || after.mounted != 1 || after.readonly != 1)
        return Result::lifecycle;
    return controller_ready(after);
}
Stock121ReadOnlySession::Stock121ReadOnlySession(Diagnostic& diagnostic)
    : diagnostic_(diagnostic), device_(0), guarded_(false), activated_(false), mounted_(false),
      attempted_(false), fresh_(false), built_(false), mismatch_(0xffffffffu), mount_status_(0), direct_ok_(false) {
    volatile services::Byte* bytes = reinterpret_cast<volatile services::Byte*>(&diagnostic_);
    for (U32 i = 0; i != sizeof(Diagnostic); ++i) bytes[i] = 0;
    diagnostic_.magic = 0x53444358u; diagnostic_.version = 1;
    diagnostic_.stage = Stage::idle; diagnostic_.result = Result::unavailable;
    diagnostic_.denied_writes = diagnostic_.handles = 0;
    for (U32 i = 0; i != 5; ++i) saved_[i] = 0;
    for (U32 i = 0; i != 4; ++i) saved_logs_[i] = 0;
}
Result Stock121ReadOnlySession::fail(Result result) {
    diagnostic_.stage = Stage::failed; diagnostic_.result = result; return result;
}
bool Stock121ReadOnlySession::safe_to_handoff() const {
    return !guarded_ && !activated_ && !mounted_ && !diagnostic_.handles;
}
#if defined(__bfin__)
namespace {
template<class F> F* call(U32 address) { return reinterpret_cast<F*>(address); }
volatile U32& word(U32 address) { return *reinterpret_cast<volatile U32*>(address); }
volatile services::Byte& byte(U32 address) {
    return *reinterpret_cast<volatile services::Byte*>(address);
}
// Initialized data, deliberately outside BSS. The experimental cold link has no
// runtime zero-initialization. A single owning session may guard the old device.
volatile U32 owner = 0xffffffffu;
volatile U32 denied = 0xffffffffu;
const U32 slots[5] = {0x80, 0x88, 0x8c, 0x9c, 0xa8};
int deny_media_change() {
    if (denied != 0xffffffffu) ++denied;
    return 0; // YAFFS_FAIL, never invoke the saved media operation.
}
U32 crc32(U32 address, U32 size) {
    U32 crc = 0xffffffffu;
    for (U32 i = 0; i != size; ++i) {
        crc ^= byte(address + i);
        for (U32 b = 0; b != 8; ++b)
            crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}
bool identity() {
    if (word(0x88a2a4c8u) != 96 || word(0x88a2a4ccu) != 96) return false;
    static const U32 spans[][3] = {
        {0x889fa0deu, 0x16u, 0xa3e32eadu},
        {0x889fae2eu, 0xf8u, 0x752c26d1u},
        {0x889fb02cu, 0xb6u, 0x5b1975f5u},
        {0x88a3434eu, 0x1d4u, 0x00862135u},
        {0x88a3454au, 0x15cu, 0xc0bc9a34u},
        {0x88a063fcu, 0x13cu, 0x2a890f9fu},
        {0x88a01de4u, 0x216u, 0xf3ea3114u},
        {0x88a041d2u, 0x2eu, 0xa9f1b6f9u},
        {0x0803c3a4u, 0x7au, 0xa2a05ee7u},
        {0x0803c480u, 0xeeu, 0xe965d88fu},
        {0x0803c570u, 0x32u, 0x6e382449u},
        {0x0803c228u, 0xeu, 0x80c211b7u},
        {0x889f41e6u, 0xceu, 0xc567f175u},
        {0x889f439au, 0x88u, 0x20f07d28u},
        {0x889f44f4u, 0x90u, 0xeb71de59u},
        // Stock logger body 0x11a01d96 (0x19c, CRC 0xaf007159) lives in L1
        // instruction SRAM. It is not CRC-checked with core data loads; every
        // stalled test (002/005/006) did so. Verify it another way before any
        // storage activation relies on the retained logger.
        {0x889f9974u, 0x4u, 0x3b9f9ad2u},
        {0x889f9524u, 0x11au, 0xefff9ec0u},
        {0x88a030a0u, 0x5au, 0xc65f756fu},
        {0x88a03324u, 0x80u, 0x9c32b1d4u},
        {0x88a01cc8u, 0x76u, 0x38ef81bdu}
    };
    for (U32 i = 0; i != sizeof(spans) / sizeof(spans[0]); ++i)
        if (crc32(spans[i][0], spans[i][1]) != spans[i][2]) return false;
    return true;
}
bool media_guarded(U32 dev) {
    for (U32 i = 0; i != 5; ++i)
        if (word(dev + slots[i]) != reinterpret_cast<U32>(&deny_media_change)) return false;
    return true;
}
bool irq_ready() {
    const U32 index = byte(0x11b00b0cu);
    return index && index < 96 && (word(0x1fc02104u) & (1u << 11)) &&
           (word(0x20004a40u) & 5u) == 5u &&
           word(0x11b008d0u + index * 4) == 0x88a061d6u &&
           word(0x11b005d4u + index * 8) == 0x11b00200u;
}
bool logs_muted() {
    for (U32 i = 0; i != 4; ++i)
        if (word(0x8803d06cu + i * 4)) return false;
    return true;
}
}
bool capture_state(Snapshot& s) {
    s.role = byte(0x8807cac4u); s.stock_mounted = byte(0x88a1f3bcu);
    s.local_mounted = byte(0x8804f6e4u); s.initialized = byte(0x11907ab4u);
    s.references = word(0x11b0029cu); s.handle = word(0x88073000u);
    s.interrupt_mask = word(0x1fc02104u); s.source_control = word(0x20004a40u);
    s.vector_index = byte(0x11b00b0cu);
    s.vector_handler = s.vector_context = 0;
    if (s.vector_index && s.vector_index < 96) {
        s.vector_handler = word(0x11b008d0u + s.vector_index * 4);
        s.vector_context = word(0x11b005d4u + s.vector_index * 8);
    }
    s.rsi_control = word(0x20076000u); s.rsi_mask = word(0x20076024u);
    s.rsi_status = word(0x20076044u); s.rsi_configuration = word(0x2007604cu);
    s.rsi_clock = word(0x20076064u);
    s.completion = word(0x11b00298u);
    s.completion_initialized = s.completion_count = 0;
    if (s.completion == 0x88072d8cu) {
        s.completion_initialized = byte(s.completion);
        s.completion_count = word(s.completion + 4);
    }
    s.adma_control = word(0x20076080u); s.adma_pointer = word(0x20076088u);
    s.transfer_address = word(0x11b002a4u); s.transfer_bytes = word(0x11b002a8u);
    s.transfer_error = word(0x11b002acu);
    for (U32 i = 0; i != 4; ++i) s.log_callbacks[i] = word(0x8803d06cu + i * 4);
    s.device = s.mounted = s.readonly = 0;
    return true;
}
bool Stock121ReadOnlySession::capture(Snapshot& s) {
    if (!identity() || !capture_state(s)) return false;
    if (retained_lookup::stock121_yaffs_device(s.device) != retained_lookup::Result::ok) return false;
    s.mounted = s.readonly = 0;
    if (!(s.device & 3u) && s.device >= 0x80000000u && s.device <= 0x8ffff16cu) {
        s.mounted = word(s.device + 0xe4); s.readonly = word(s.device + 0xe8);
    }
    return true;
}
namespace {
// Constructor chain reached by 889f963e: yaffs_configure_device, calloc (L2),
// the self-contained heap block (context/sbrk/grow/free/malloc/calloc_heap),
// yaffs_register_device and its list insert. All data-readable (L2/DDR).
// The heap block is split around 0x88a2ef28: a clock-frequency global written
// by clock setup (88a30fbc) and read by timing code (88a34280), never by the
// allocator. Test011 showed it as the only difference (-1 -> 400000000).
bool construct_identity() {
    static const U32 spans[][3] = {
        {0x889f963eu, 0xc2u, 0x4b24e1b6u},
        {0x0803e06cu, 0x18u, 0xf1da0affu},
        {0x88a2ec6cu, 0x2bcu, 0x49120387u},
        {0x88a2ef2cu, 0x33cu, 0x1f3931ddu},
        {0x889fb24eu, 0x6cu, 0x36019333u},
        {0x889f9ad2u, 0x10u, 0x15234e82u}
    };
    for (U32 i = 0; i != sizeof(spans) / sizeof(spans[0]); ++i)
        if (crc32(spans[i][0], spans[i][1]) != spans[i][2]) return false;
    return true;
}
}
U32 fresh_shape_mismatch(U32 d) {
    // Offsets/values from raw 1.2.1 disassembly of 889f963e and 889fb24e.
    // Tagger slots +9c/+a4/+a8 stay 0 until mount installs only empty slots.
    static const U32 expected[][2] = {
        {0x00, 0x11907af4u}, {0x04, 1}, {0x08, 0x1000}, {0x0c, 0x80}, {0x14, 0},
        {0x18, 0x11ff}, {0x1c, 5}, {0x20, 10}, {0x28, 1}, {0x30, 1}, {0x34, 1},
        {0x3c, 1000}, {0x7c, 1}, {0x80, 0x88a3454au}, {0x84, 0x88a345f4u},
        {0x88, 0x88a34522u}, {0x8c, 0x88a34348u}, {0x90, 0x88a3434au},
        {0x94, 0x88a3434eu}, {0x98, 0}, {0x9c, 0}, {0xa0, 0x889f9524u}, {0xa4, 0},
        {0xa8, 0}, {0x54, 0x889fb350u}, {0x58, 0}, {0xe4, 0}, {0xe8, 0}
    };
    static_assert(sizeof(expected) / sizeof(expected[0]) <= 32, "Mismatch mask width");
    U32 mask = 0;
    for (U32 i = 0; i != sizeof(expected) / sizeof(expected[0]); ++i)
        if (word(d + expected[i][0]) != expected[i][1]) mask |= 1u << i;
    return mask;
}
Result construct_fresh(U32& device, U32& shape_mismatch) {
    device = 0; shape_mismatch = 0xffffffffu;
    if (!identity() || !construct_identity()) return Result::identity;
    Snapshot s;
    if (!capture_state(s)) return Result::identity;
    const Result fresh = check_fresh(s);
    if (fresh != Result::ok) return fresh;
    U32 found = 0;
    retained_lookup::Diagnostic trace;
    if (retained_lookup::stock121_yaffs_device(found, trace) != retained_lookup::Result::missing ||
        trace.visited) return Result::busy;
    const U32 made = call<U32(U32, U32, U32)>(0x889f963eu)(0x11907af4u, 0, 0x11ff);
    if (!made) return Result::activate;
    if (retained_lookup::stock121_yaffs_device(found, trace) != retained_lookup::Result::ok ||
        found != made || trace.visited != 1) return Result::device;
    shape_mismatch = fresh_shape_mismatch(made);
    if (shape_mismatch) return Result::device;
    device = made;
    return Result::ok;
}
namespace {
// Entry points of the direct YAFFS read API (raw 1.2.1 disassembly; each ends
// in RTS or a backward branch). Lock/unlock 889f9974/6 are RTS stubs.
bool direct_identity() {
    static const U32 spans[][3] = {
        {0x889fbfe6u, 0x16u, 0x2019eeb2u},  // yaffs_open
        {0x889fb928u, 0x18u, 0x3b226de9u},  // yaffs_read -> do_read 889fb760 (889fbb0c is WRITE)
        {0x889fb6fau, 0x66u, 0xc096fb35u},  // yaffs_close
        {0x889fb50cu, 0x12u, 0xc6d84162u},  // yaffs_opendir
        {0x889fb4e2u, 0x2au, 0xe78a1e19u},  // opendir_inner
        {0x889fb5e6u, 0x22u, 0xbecae3a6u},  // yaffs_readdir
        {0x889fbffcu, 0x22u, 0xaf772651u},  // yaffs_closedir
        {0x889f9982u, 0xau, 0x317d0c54u}    // yaffs_get_error
    };
    for (U32 i = 0; i != sizeof(spans) / sizeof(spans[0]); ++i)
        if (crc32(spans[i][0], spans[i][1]) != spans[i][2]) return false;
    return true;
}
}
int Stock121ReadOnlySession::direct_open(const char* path) {
    if (!direct_ready() || !services::loader_path(path)) return -1;
    const int fd = call<int(const char*, int, int)>(0x889fbfe6u)(path, 0, 0);
    if (fd >= 0) ++diagnostic_.handles;
    return fd;
}
int Stock121ReadOnlySession::direct_read(int fd, services::Byte* out, U32 count) {
    return direct_ready() ? call<int(int, void*, U32)>(0x889fb928u)(fd, out, count) : -1;
}
int Stock121ReadOnlySession::direct_close(int fd) {
    if (!mounted_ || !direct_ok_ || !diagnostic_.handles) return -1;
    const int result = call<int(int)>(0x889fb6fau)(fd);
    if (!result) --diagnostic_.handles;
    return result;
}
U32 Stock121ReadOnlySession::direct_opendir(const char* path) {
    if (!direct_ready()) return 0;
    const U32 dir = call<U32(const char*)>(0x889fb50cu)(path);
    if (dir) ++diagnostic_.handles;
    return dir;
}
const services::Byte* Stock121ReadOnlySession::direct_readdir(U32 dir) {
    return direct_ready() ? call<const services::Byte*(U32)>(0x889fb5e6u)(dir) : 0;
}
int Stock121ReadOnlySession::direct_closedir(U32 dir) {
    if (!mounted_ || !direct_ok_ || !diagnostic_.handles) return -1;
    const int result = call<int(U32)>(0x889fbffcu)(dir);
    if (!result) --diagnostic_.handles;
    return result;
}
int Stock121ReadOnlySession::direct_error() const {
    return direct_ok_ ? call<int()>(0x889f9982u)() : 0;
}
#if defined(XYF_STATE_WRITE) || defined(XYF_USB_SET_DEFAULT) || defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
namespace {
// Stock code only the read-write window reaches (raw 1.2.1 disassembly; each
// span runs to its final RTS). yaffs_mount/unmount and the media write
// 88a3454a / query 88a03324 are already covered by identity().
bool write_identity() {
    static const U32 spans[][3] = {
        {0x889fbfd0u, 0x16u, 0xcfaeff2du},  // yaffs_open (sharing wrapper)
        {0x889fbb24u, 0x4acu, 0xc8a2d144u},  // yaffs_open implementation (flags 0x241)
        {0x889fbb0cu, 0x18u, 0xfd4d646eu},  // yaffs_write
        {0x889fb940u, 0x1ccu, 0xd1b12e2du},  // yaffs do_write
        {0x88a34522u, 0x28u, 0xcbc835c8u},  // media erase (dev +0x88)
        {0x88a34348u, 0x6u, 0xb7a27f39u},  // media mark-bad (dev +0x8c; stock self-loop)
        {0x88a0310cu, 0xb8u, 0x6d4ff08fu},  // tags marshall write (dev +0x9c after mount)
        {0x88a030fau, 0x12u, 0xc80f666du}   // tags marshall mark-bad (dev +0xa8 after mount)
    };
    for (U32 i = 0; i != sizeof(spans) / sizeof(spans[0]); ++i)
        if (crc32(spans[i][0], spans[i][1]) != spans[i][2]) return false;
    return true;
}
// One noinline wrapper per stock entry: each address load stays next to its
// call, so the builder's straight-line call audit resolves every target.
__attribute__((noinline)) int y_mount(U32 device, int read_only) {
    return call<int(U32, const char*, int, int)>(0x889fae2eu)(device, "/yaffs2", read_only, 0);
}
__attribute__((noinline)) int y_unmount(U32 device) {
    return call<int(U32, const char*, int)>(0x889fb02cu)(device, "/yaffs2", 0);
}
}
#endif
#if defined(XYF_STATE_WRITE) || defined(XYF_USB_SET_DEFAULT)
namespace {
__attribute__((noinline)) int y_open(const char* path, int flags, int mode) {
    return call<int(const char*, int, int)>(0x889fbfe6u)(path, flags, mode);
}
__attribute__((noinline)) int y_write(int fd, const void* data, U32 count) {
    return call<int(int, const void*, U32)>(0x889fbb0cu)(fd, data, count);
}
__attribute__((noinline)) int y_read(int fd, void* out, U32 count) {
    return call<int(int, void*, U32)>(0x889fb928u)(fd, out, count);
}
__attribute__((noinline)) int y_close(int fd) { return call<int(int)>(0x889fb6fau)(fd); }
}
#endif
Result Stock121ReadOnlySession::begin_fresh(services::Phase phase) {
    if (phase != services::Phase::cold_after_teardown) return fail(Result::unavailable);
    if (attempted_ || guarded_ || activated_ || mounted_ || owner != 0xffffffffu) return fail(Result::busy);
    attempted_ = fresh_ = true;
    if (!identity() || !capture_state(diagnostic_.before)) return fail(Result::identity);
    direct_ok_ = direct_identity();
    diagnostic_.stage = Stage::captured;
    Result result = check_fresh(diagnostic_.before);
    if (result != Result::ok) return fail(result);
    result = construct_fresh(device_, mismatch_);
    if (result != Result::ok) return fail(result);
    built_ = true;
    return mount_guarded();
}
// 191: a read-only mount that failed (its cleanup ok: unmounted, the stock callbacks back) is tried
// again on the device begin_fresh constructed (it stays registered), with the same checks and gates.
Result Stock121ReadOnlySession::retry_fresh() {
    if (!fresh_ || !built_ || diagnostic_.stage != Stage::failed || guarded_ || activated_ || mounted_ ||
        diagnostic_.handles || owner != 0xffffffffu || word(device_ + 0xe4)) return Result::busy;
    U32 found = 0;
    if (!identity() || !capture_state(diagnostic_.before)) return fail(Result::identity);
    if (retained_lookup::stock121_yaffs_device(found) != retained_lookup::Result::ok || found != device_)
        return fail(Result::device);
    const Result result = check_fresh(diagnostic_.before);
    if (result != Result::ok) return fail(result);
    return mount_guarded();
}
Result Stock121ReadOnlySession::mount_guarded() {
    Result result = Result::ok;
    diagnostic_.before.device = device_;
    owner = reinterpret_cast<U32>(this); denied = 0;
    for (U32 i = 0; i != 4; ++i) {
        saved_logs_[i] = word(0x8803d06cu + i * 4);
        word(0x8803d06cu + i * 4) = 0;
    }
    // Tag slots +9c/+a8 are still 0; stock install fills only empty slots, so
    // these deny thunks survive mount. +8c stock is a self-loop (hang).
    for (U32 i = 0; i != 5; ++i) {
        saved_[i] = word(device_ + slots[i]);
        word(device_ + slots[i]) = reinterpret_cast<U32>(&deny_media_change);
    }
    __asm__ volatile("ssync;" ::: "memory");
    guarded_ = true; diagnostic_.stage = Stage::guarded;
    if (!media_guarded(device_) || !logs_muted()) {
        const Result cleanup = end();
        return fail(cleanup == Result::ok ? Result::device : cleanup);
    }
    mount_status_ = call<int(U32, const char*, int, int)>(0x889fae2eu)(device_, "/yaffs2", 1, 0);
    mounted_ = word(device_ + 0xe4) != 0;
    capture_state(diagnostic_.after);
    diagnostic_.after.device = device_;
    diagnostic_.after.mounted = word(device_ + 0xe4);
    diagnostic_.after.readonly = word(device_ + 0xe8);
    diagnostic_.denied_writes = denied;
    result = check_fresh_active(diagnostic_.before, diagnostic_.after);
    if (mount_status_ || result != Result::ok || denied || !media_guarded(device_)) {
        const Result failure = denied ? Result::write_attempt :      // 191: the mount's own failure first
            (mount_status_ || result == Result::ok ? Result::mount : result);
        const Result cleanup = end();
        return fail(cleanup == Result::ok ? failure : cleanup);
    }
    diagnostic_.stage = Stage::mounted; diagnostic_.result = Result::ok;
    return Result::ok;
}
bool Stock121ReadOnlySession::ready() const {
    // Every session is fresh (begin_fresh is the only way in). The second return below and
    // the activated_ branches in end() belong to the removed non-fresh session and never
    // run; they stay because removing them changes the candidate (docs/ARCHITECTURE.md, "Build modes").
    if (fresh_)
        return diagnostic_.stage == Stage::mounted && diagnostic_.result == Result::ok &&
               guarded_ && mounted_ && !activated_ && owner == reinterpret_cast<U32>(this) &&
               !denied && word(device_ + 0xe4) == 1 && word(device_ + 0xe8) == 1 &&
               byte(0x11907ab4u) == 1 && byte(0x8807cac4u) == 0 && media_guarded(device_) &&
               logs_muted() && irq_ready() && word(0x88073000u) == 0x11b00200u &&
               word(0x11b0029cu) == diagnostic_.before.references &&
               word(0x11b00298u) == 0x88072d8cu && byte(0x88072d8cu) == 1 &&
               !(word(0x20076080u) & 0x80u) && !word(0x11b002a4u) &&
               !word(0x11b002a8u) && !word(0x11b002acu);
    return diagnostic_.stage == Stage::mounted && diagnostic_.result == Result::ok &&
           guarded_ && activated_ && mounted_ && owner == reinterpret_cast<U32>(this) &&
           !denied && word(device_ + 0xe4) == 1 && word(device_ + 0xe8) == 1 &&
           byte(0x11907ab4u) == 1 && byte(0x8807cac4u) == 0 && media_guarded(device_) && logs_muted() &&
           irq_ready() && word(0x88073000u) == 0x11b00200u &&
           word(0x11b0029cu) == diagnostic_.before.references + 1 &&
           word(0x11b00298u) == 0x88072d8cu && byte(0x88072d8cu) == 1 &&
           !(word(0x20076080u) & 0x80u) && !word(0x11b002a4u) &&
           !word(0x11b002a8u) && !word(0x11b002acu);
}
#if defined(XYF_STATE_WRITE) || defined(XYF_USB_SET_DEFAULT)
// Only in the XY Forever modes that offer "set default" (--xyf-boot, --xyf-usb):
// every other image keeps the builder's ban on yaffs_write.
Result Stock121ReadOnlySession::write_state_slot(const char* path, const services::Byte* data, U32 count,
                                                 services::Byte* readback, U32& step) {
    step = 0;
    if (!state_slot_path(path) || !data || !readback || !count || count > 4096) return Result::write_refused;
    if (!fresh_ || !ready() || diagnostic_.handles) return Result::write_refused;
    step = 1;
    if (!identity() || !direct_identity() || !write_identity()) return Result::identity;
    step = 2;                                            // folder must exist (no mkdir)
    const U32 folder = direct_opendir("/yaffs2/xy-forever");
    if (!folder) return Result::write_refused;
    if (direct_closedir(folder)) return Result::write_refused;
    step = 3;                                            // unmount read-only, gates still on
    if (y_unmount(device_) || word(device_ + 0xe4))
        return fail(Result::unmount);
    mounted_ = false;
    // Stock media callbacks for the write window only (+9c/+a8 are 0 and are
    // filled by mount, exactly as the stock read-write mount does).
    for (U32 i = 0; i != 5; ++i) word(device_ + slots[i]) = saved_[i];
    __asm__ volatile("ssync;" ::: "memory");
    step = 4;
    Result outcome = Result::ok;
    const int rw = y_mount(device_, 0);
    if (rw || word(device_ + 0xe4) != 1 || word(device_ + 0xe8) != 0) outcome = Result::mount;
    if (outcome == Result::ok) {
        step = 5;
        const int fd = y_open(path, 0x241, 0x180);
        if (fd < 0) outcome = Result::write_failed;
        else {
            step = 6;
            const int wrote = y_write(fd, data, count);
            const int closed = y_close(fd);
            if (wrote != int(count) || closed) outcome = Result::write_failed;
        }
        if (outcome == Result::ok) {
            step = 7;                                    // read back what yaffs now holds
            const int rd = y_open(path, 0, 0);
            if (rd < 0) outcome = Result::verify_failed;
            else {
                U32 got = 0;
                for (U32 guard = 0; guard != 16 && got <= count; ++guard) {
                    const int n = y_read(rd, readback + got, count + 1 - got);
                    if (n <= 0) break;
                    got += U32(n);
                }
                if (y_close(rd)) outcome = Result::verify_failed;
                if (got != count) outcome = Result::verify_failed;
                for (U32 i = 0; i != count && outcome == Result::ok; ++i)
                    if (readback[i] != data[i]) outcome = Result::verify_failed;
            }
        }
    }
    step = 8;                                            // unmount read-write (checkpoint)
    if (word(device_ + 0xe4) &&
        (y_unmount(device_) || word(device_ + 0xe4))) {
        // Cannot leave a read-write mount behind: keep the stock callbacks, no
        // further storage use; the caller must not hand off (safe_to_handoff false).
        diagnostic_.stage = Stage::failed; diagnostic_.result = Result::unmount;
        return Result::unmount;
    }
    step = 9;                                            // gates back on, then read-only again
    for (U32 i = 0; i != 5; ++i) word(device_ + slots[i]) = reinterpret_cast<U32>(&deny_media_change);
    __asm__ volatile("ssync;" ::: "memory");
    denied = 0;
    mount_status_ = y_mount(device_, 1);
    mounted_ = word(device_ + 0xe4) != 0;
    if (mount_status_ || !mounted_ || word(device_ + 0xe8) != 1 || denied || !media_guarded(device_)) {
        if (mounted_ && !y_unmount(device_) &&
            !word(device_ + 0xe4)) mounted_ = false;
        return outcome == Result::ok ? Result::remount : outcome;
    }
    step = 10;
    return outcome;
}
#endif
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
// MTP mode (a): the state write's read-only -> read-write switch, but the
// window stays open while the stock posix layer serves b's MTP requests. The
// write gates are on whenever the window is closed, so media_guarded() is the
// window state (no new member: every other image keeps this class's layout).
Result Stock121ReadOnlySession::open_write_window(U32& step) {
    step = 0;
    if (!fresh_ || !ready() || diagnostic_.handles) return Result::write_refused;
    step = 1;
    if (!identity() || !direct_identity() || !write_identity()) return Result::identity;
    step = 2;                                            // unmount read-only, gates still on
    if (y_unmount(device_) || word(device_ + 0xe4))
        return fail(Result::unmount);
    mounted_ = false;
    // Stock media callbacks (+9c/+a8 are 0 and are filled by mount, exactly
    // as the stock read-write mount does).
    for (U32 i = 0; i != 5; ++i) word(device_ + slots[i]) = saved_[i];
    __asm__ volatile("ssync;" ::: "memory");
    step = 3;
    const int rw = y_mount(device_, 0);
    mounted_ = word(device_ + 0xe4) != 0;
    if (!rw && mounted_ && word(device_ + 0xe8) == 0) { step = 4; return Result::ok; }
    // Not mounted read-write: back to the gated, unmounted state (end() ok).
    if (mounted_ && (y_unmount(device_) || word(device_ + 0xe4))) {
        diagnostic_.stage = Stage::failed; diagnostic_.result = Result::unmount;
        return Result::unmount;                          // stock callbacks stay; never hand off
    }
    mounted_ = false;
    for (U32 i = 0; i != 5; ++i) word(device_ + slots[i]) = reinterpret_cast<U32>(&deny_media_change);
    __asm__ volatile("ssync;" ::: "memory");
    return Result::mount;
}
Result Stock121ReadOnlySession::close_write_window() {
    if (!fresh_ || media_guarded(device_)) return Result::lifecycle;       // window not open
    if (word(device_ + 0xe4) && (y_unmount(device_) || word(device_ + 0xe4))) {
        diagnostic_.stage = Stage::failed; diagnostic_.result = Result::unmount;
        return Result::unmount;                          // still read-write: caller keeps it mounted
    }
    mounted_ = false;
    for (U32 i = 0; i != 5; ++i) word(device_ + slots[i]) = reinterpret_cast<U32>(&deny_media_change);
    __asm__ volatile("ssync;" ::: "memory");
    denied = 0;
    return Result::ok;
}
#if defined(XYF_ONE_SESSION)
Result Stock121ReadOnlySession::reopen_read_only() {
    if (!fresh_ || !guarded_ || diagnostic_.stage == Stage::failed || diagnostic_.handles ||
        mounted_ || word(device_ + 0xe4) || !media_guarded(device_)) return Result::lifecycle;
    denied = 0;
    mount_status_ = y_mount(device_, 1);
    mounted_ = word(device_ + 0xe4) != 0;
    if (mount_status_ || !mounted_ || word(device_ + 0xe8) != 1 || denied || !media_guarded(device_)) {
        if (mounted_ && !y_unmount(device_) && !word(device_ + 0xe4)) mounted_ = false;
        return Result::remount;
    }
    return Result::ok;
}
// 192 (incident 2026-11-16). After an unclean shutdown yaffs has no checkpoint and its mount repairs
// (erases blocks, removes deleted objects); our read-only mount denies that and fails with
// write_attempt on every power cycle until TE's dev menu mounts read-write. So XY Forever does that
// same repair itself when, and only when, the read-only open fails for that reason: it uses functions
// already in the build and writes only the filesystem's own housekeeping (changing this needs the
// developer's explicit yes, docs/SAFETY.md). The write window's stock calls only (y_mount 0 /
// y_unmount); no bound inside them (yaffs's own repair time); the caller runs it once per boot.
Result Stock121ReadOnlySession::repair_fresh(U32& step) {
    step = 0;
    if (!fresh_ || !built_ || diagnostic_.stage != Stage::failed || diagnostic_.result != Result::write_attempt ||
        guarded_ || activated_ || mounted_ || diagnostic_.handles || owner != 0xffffffffu || word(device_ + 0xe4))
        return Result::busy;
    step = 1;
    U32 found = 0;
    if (!identity() || !direct_identity() || !write_identity() || !capture_state(diagnostic_.before))
        return fail(Result::identity);
    if (retained_lookup::stock121_yaffs_device(found) != retained_lookup::Result::ok || found != device_)
        return fail(Result::device);
    const Result checked = check_fresh(diagnostic_.before);
    if (checked != Result::ok) return fail(checked);
    step = 2;                                            // stock media callbacks (end() restored them)
    U32 callbacks[5];
    owner = reinterpret_cast<U32>(this);
    for (U32 i = 0; i != 5; ++i) callbacks[i] = word(device_ + slots[i]);
    for (U32 i = 0; i != 4; ++i) {                       // the logger muted, as for every mount of ours
        saved_logs_[i] = word(0x8803d06cu + i * 4);
        word(0x8803d06cu + i * 4) = 0;
    }
    __asm__ volatile("ssync;" ::: "memory");             // as the write window, before every stock call
    Result outcome = Result::ok;
    if (!logs_muted()) outcome = Result::device;
    else {
        step = 3;                                        // read-write: yaffs repairs
        const int rw = y_mount(device_, 0);
        const bool was_mounted = word(device_ + 0xe4) != 0;
        if (rw || !was_mounted || word(device_ + 0xe8) != 0) outcome = Result::mount;
        step = 4;                                        // unmount: the checkpoint
        if (was_mounted && (y_unmount(device_) || word(device_ + 0xe4))) {
            // Still mounted read-write: the stock callbacks stay; never hand off (safe_to_handoff).
            mounted_ = true;
            for (U32 i = 0; i != 4; ++i) word(0x8803d06cu + i * 4) = saved_logs_[i];
            owner = 0xffffffffu;
            __asm__ volatile("ssync;" ::: "memory");
            return fail(Result::unmount);
        }
    }
    for (U32 i = 0; i != 5; ++i) word(device_ + slots[i]) = callbacks[i];   // +9c/+a8 empty again
    for (U32 i = 0; i != 4; ++i) word(0x8803d06cu + i * 4) = saved_logs_[i];
    owner = 0xffffffffu;
    __asm__ volatile("ssync;" ::: "memory");
    if (outcome != Result::ok) return fail(outcome);
    step = 5;                                            // read-only again, gated as begin_fresh
    return mount_guarded();
}
#endif
#endif
Result Stock121ReadOnlySession::end() {
    if (diagnostic_.handles) return fail(Result::open_handles);
    if (guarded_ && !media_guarded(device_)) return fail(Result::device);
    if (activated_ && !irq_ready()) return fail(Result::irq);
    if (mounted_) {
        // Keep every write gate installed through flush/checkpoint/deinit.
        const int result = call<int(U32, const char*, int)>(0x889fb02cu)(device_, "/yaffs2", 0);
        if (result || word(device_ + 0xe4)) return fail(Result::unmount);
        mounted_ = false;
    }
    if (activated_) {
        if (call<int()>(0x88a344a8u)() != 1) return fail(Result::shutdown);
        activated_ = false;
    }
    if (guarded_) {
        diagnostic_.denied_writes = denied;
        for (U32 i = 0; i != 5; ++i) word(device_ + slots[i]) = saved_[i];
        for (U32 i = 0; i != 4; ++i) word(0x8803d06cu + i * 4) = saved_logs_[i];
        __asm__ volatile("ssync;" ::: "memory");
        guarded_ = false; owner = 0xffffffffu;
    }
    diagnostic_.stage = Stage::closed; diagnostic_.result = Result::ok;
    return Result::ok;
}
#else
bool capture_state(Snapshot&) { return false; }
U32 fresh_shape_mismatch(U32) { return 0xffffffffu; }
Result construct_fresh(U32& device, U32& shape_mismatch) {
    device = 0; shape_mismatch = 0xffffffffu; return Result::unavailable;
}
bool Stock121ReadOnlySession::capture(Snapshot&) { return false; }
Result Stock121ReadOnlySession::begin_fresh(services::Phase) { return fail(Result::unavailable); }
Result Stock121ReadOnlySession::retry_fresh() { return fail(Result::unavailable); }
Result Stock121ReadOnlySession::mount_guarded() { return fail(Result::unavailable); }
int Stock121ReadOnlySession::direct_open(const char*) { return -1; }
int Stock121ReadOnlySession::direct_read(int, services::Byte*, U32) { return -1; }
int Stock121ReadOnlySession::direct_close(int) { return -1; }
U32 Stock121ReadOnlySession::direct_opendir(const char*) { return 0; }
const services::Byte* Stock121ReadOnlySession::direct_readdir(U32) { return 0; }
int Stock121ReadOnlySession::direct_closedir(U32) { return -1; }
int Stock121ReadOnlySession::direct_error() const { return 0; }
bool Stock121ReadOnlySession::ready() const { return false; }
Result Stock121ReadOnlySession::end() { return fail(Result::unavailable); }
Result Stock121ReadOnlySession::write_state_slot(const char*, const services::Byte*, U32, services::Byte*, U32& step) {
    step = 0; return Result::unavailable;
}
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
Result Stock121ReadOnlySession::open_write_window(U32& step) { step = 0; return Result::unavailable; }
Result Stock121ReadOnlySession::close_write_window() { return Result::unavailable; }
#if defined(XYF_ONE_SESSION)
Result Stock121ReadOnlySession::reopen_read_only() { return Result::unavailable; }
Result Stock121ReadOnlySession::repair_fresh(U32& step) { step = 0; return Result::unavailable; }
#endif
#endif
#endif
bool Stock121ReadOnlySession::state_slot_path(const char* path) {
    static const char a[] = "/yaffs2/xy-forever/state-a.txt", b[] = "/yaffs2/xy-forever/state-b.txt";
    if (!path) return false;
    const char* const allowed[2] = {a, b};
    for (U32 k = 0; k != 2; ++k) {
        U32 i = 0;
        while (allowed[k][i] && path[i] == allowed[k][i]) ++i;
        if (!allowed[k][i] && !path[i]) return true;
    }
    return false;
}
} }

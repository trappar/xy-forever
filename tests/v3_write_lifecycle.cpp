#include <cassert>
#include <cstdint>
#include <cstdio>
#include <map>
#include <vector>
// Pointer-sized words let the actual callback assignments run on a 64-bit host.
using U32 = uintptr_t;
namespace services { using U32 = ::U32; }
std::map<U32, U32> memory;
U32& word(U32 address) { return memory[address]; }
const U32 slots[5] = {0x90, 0x94, 0x98, 0x9c, 0xa8};
U32 denied = 0;
void deny_media_change() { ++denied; }
bool media_guarded(U32 device) {
    for (U32 slot : slots)
        if (word(device + slot) != reinterpret_cast<U32>(&deny_media_change)) return false;
    return true;
}
bool identities = true;
bool identity() { return identities; }
bool direct_identity() { return identities; }
bool write_identity() { return identities; }
bool fail_rw = false, fail_ro = false, partial_ro = false, fail_unmount = false;
std::vector<int> mounts;
int y_mount(U32 device, int ro) {
    mounts.push_back(ro);
    assert(ro ? media_guarded(device) : !media_guarded(device));
    word(device + 0xe4) = ro ? (!fail_ro || partial_ro) : !fail_rw;
    word(device + 0xe8) = ro;
    return (ro ? fail_ro : fail_rw) ? -1 : 0;
}
int y_unmount(U32 device) {
    if (fail_unmount) return -1;
    word(device + 0xe4) = 0;
    return 0;
}
namespace cold_storage {
enum class Result { ok, lifecycle, write_refused, identity, unmount, mount, remount };
enum class Stage { mounted, failed };
struct Diagnostic { Stage stage = Stage::mounted; U32 handles = 0; Result result = Result::ok; };
struct Stock121ReadOnlySession {
    Diagnostic diagnostic_;
    U32 device_ = 0x1000, saved_[5] = {1, 2, 3, 4, 5};
    bool fresh_ = true, guarded_ = true, mounted_ = true;
    int mount_status_ = 0;
    Stock121ReadOnlySession() {
        word(device_ + 0xe4) = word(device_ + 0xe8) = 1;
        for (U32 slot : slots) word(device_ + slot) = reinterpret_cast<U32>(&deny_media_change);
    }
    bool ready() const {
        return mounted_ && guarded_ && diagnostic_.stage == Stage::mounted &&
            !denied && word(device_ + 0xe4) == 1 && word(device_ + 0xe8) == 1 && media_guarded(device_);
    }
    Result fail(Result r) { diagnostic_.stage = Stage::failed; diagnostic_.result = r; return r; }
    Result open_write_window(U32& step);
    Result close_write_window();
    Result reopen_read_only();
};
}
const U32 max_fds = 4;
int fds[max_fds] = {-1, -1, -1, -1};
struct { U32 open_fds = 0; } result;
U32 mounted = 0, adopted = 1, used = 0, window_open = 0, window_step = 0, window_result = 0;
const U32 entry_write = 2, entry_run = 3;
U32 engine = entry_write, pauses = 0, resumes = 0, handles_closed = 0;
namespace usb_service {
bool guard_pause() { ++pauses; return true; }
void guard_resume(bool was) { if (was) ++resumes; }
}
void close_handles() { ++handles_closed; result.open_fds = 0; }
U32 window_touched = 0, library_dirty = 0;                   // 2.2c: the idle timer and the rescan flag
void note(U32, U32, U32) {}
bool mount(cold_storage::Stock121ReadOnlySession& storage) {
    if (mounted) return true;
    if (!storage.ready()) return false;
    mounted = used = 1;
    return true;
}
void unmount(cold_storage::Stock121ReadOnlySession&) { mounted = 0; }
U32 selected = 0;                                          // install mode's boot partition (2.3b)
bool boot_selected() { return selected != 0; }
U32 held = 0;                                              // files and folders b opened (read-only entry)
U32 held_files() { return held; }
#include "lifecycle.inc"
void reset() {
    memory.clear(); mounts.clear(); denied = 0;
    identities = true;
    fail_rw = fail_ro = partial_ro = fail_unmount = false;
    mounted = used = window_open = window_step = window_result = selected = held = 0;
    engine = entry_write; pauses = resumes = handles_closed = 0;
}
int main() {
    using cold_storage::Stock121ReadOnlySession;
    using cold_storage::Result;
    reset();
    Stock121ReadOnlySession good;
    for (int i = 0; i < 2; ++i) {
        assert(open_window(good) && window_open && !good.ready());
        close_engine(good);
        assert(!window_open && !mounted && window_result == U32(Result::ok));
        assert(good.ready()); // subsequent reads and another write remain possible
    }
    assert((mounts == std::vector<int>{0, 1, 0, 1}));
    assert(pauses == 4 && resumes == 4 && handles_closed == 2);   // the write entry: a's guard paused, then back

    reset();
    Stock121ReadOnlySession run;
    engine = entry_run;                                    // the run entry keeps a's guard on (097)
    assert(open_window(run) && window_open);
    close_engine(run);
    assert(!window_open && !mounted && window_result == U32(Result::ok) && run.ready());
    assert(pauses == 0 && resumes == 0);

    reset();
    Stock121ReadOnlySession rw_failed;
    fail_rw = true;
    assert(!open_window(rw_failed));
    assert(rw_failed.ready() && !mounted && !window_open);
    assert(window_result == U32(Result::mount));
    assert((mounts == std::vector<int>{0, 1}));

    reset();
    Stock121ReadOnlySession on_boot_partition;
    selected = 1;                                          // 2.3b: never read-write while a boot partition is selected
    assert(!open_window(on_boot_partition));
    assert(mounts.empty() && on_boot_partition.ready() && !window_open && pauses == 0);

    reset();
    Stock121ReadOnlySession busy;
    held = 1;                                              // 2.3b: a read-entry file still open: busy, nothing unmounted
    assert(!open_window(busy));
    assert(mounts.empty() && busy.ready() && !window_open && pauses == 0);
    held = 0;
    assert(open_window(busy) && window_open);              // once b's handles are closed (op 9)

    reset();
    Stock121ReadOnlySession refused;
    identities = false;
    assert(!open_window(refused));
    assert(mounts.empty() && refused.ready());

    reset();
    Stock121ReadOnlySession ro_unmount_failed;
    fail_unmount = true;
    assert(!open_window(ro_unmount_failed));
    assert(mounts.empty() && media_guarded(ro_unmount_failed.device_));

    reset();
    Stock121ReadOnlySession close_failed;
    assert(open_window(close_failed));
    fail_unmount = true;
    close_engine(close_failed);
    assert(window_open && mounted && window_result == U32(Result::unmount));
    assert(!close_failed.ready() && !media_guarded(close_failed.device_));
    assert(close_failed.reopen_read_only() == Result::lifecycle);
    assert(!open_window(close_failed));
    assert((mounts == std::vector<int>{0})); // never mount over a writable mount

    for (bool partial : {false, true}) {
        reset();
        Stock121ReadOnlySession remount_failed;
        assert(open_window(remount_failed));
        fail_ro = true; partial_ro = partial;
        close_engine(remount_failed);
        assert(!window_open && !mounted && window_result == U32(Result::remount));
        assert(!remount_failed.ready() && media_guarded(remount_failed.device_));
        assert(!word(remount_failed.device_ + 0xe4));
        assert(!open_window(remount_failed));
    }
    reset();
    Stock121ReadOnlySession both_failed;
    fail_rw = fail_ro = true;
    assert(!open_window(both_failed));
    assert(window_result == U32(Result::remount));
    assert(!both_failed.ready() && media_guarded(both_failed.device_));
    puts("write lifecycle checks passed");
}

#ifndef XYF_SIM_RUNTIME_HPP
#define XYF_SIM_RUNTIME_HPP
// Simulator internals shared by sim/*.cpp (not included by device code).
#include <atomic>
#include <stdint.h>
#include <stddef.h>
#include "sim.hpp"
namespace xyf_sim {
// Processor exit codes (the board restarts a processor after exit_reset).
enum Exit { exit_reset = 40, exit_halt = 41, exit_os = 42, exit_crash = 43 };
// Emulated memory: one 4 GiB window, device address a at base + a. Regions:
// L2, L1 (data and instruction), core and system MMRs, and SDRAM (0x80000000..0x90000000).
// SDRAM is a shared memory object per processor: it survives a processor reset (as on
// the device) and is cleared by a power cut.
bool mapped(uint32_t address, uint32_t bytes);
uint32_t r32(uint32_t address);
void w32(uint32_t address, uint32_t value);
uint8_t r8(uint32_t address);
void w8(uint32_t address, uint8_t value);
void reset_private_memory();                    // L1/L2/MMR back to zero (processor reset)
void attach_sdram(int fd);
// Shared state between the board and both processors (anonymous shared mapping).
struct Shared {
    std::atomic<int32_t> a_silent, a_slow_ms, drop_request, drop_body, cable_pull, cut_after, fail_write;
    std::atomic<int32_t> fail_mount;              // the Nth yaffs mount from now fails (EIO)
    std::atomic<int32_t> a_silenced;              // B's class requests a_silent kept from A (tests: the request is out)
    std::atomic<uint32_t> clock_reads[2];         // device clock reads by A and B (tests: a processor ran on past an advance)
    // 2.2b test hooks: the Nth stock select on A switches the partition and then answers failure;
    // every yaffs unmount fails while fail_unmount is set; the Nth posix open on A (the marker's
    // restore) fails; the next path body reaches the stock path buffer whole (240 bytes, no NUL
    // added) instead of cut at 239 bytes.
    std::atomic<int32_t> fail_select, fail_unmount, fail_open, full_path;
    std::atomic<int64_t> space;                  // bytes yaffs may still write (< 0: unlimited)
    std::atomic<int32_t> storage_ops, play, cuts;
    std::atomic<int32_t> b_listening;            // the USB CDC endpoint is open (TCP port listening)
    // The chip's partition register as A's last stock select left it (0 user area: yaffs; 1, 2 boot
    // partitions), and the yaffs media changes made while a boot partition was selected (on the
    // device they would land on that boot partition).
    std::atomic<int32_t> partition, boot_writes;
    // yaffs's own record across restarts and power cuts: a read-write mount not yet unmounted (no
    // checkpoint written), and the mounts that found one (on the device: a full scan, a slow boot).
    std::atomic<int32_t> rw_open, unclean_mounts;
    std::atomic<int32_t> rw_mounts;              // 2.2c: read-write mounts since the simulator started (window opens)
    // Test hook (control "swap PATH"): the next path or query executor on A finds PATH in the stock
    // path buffer, as if a second request's body had arrived between A's check and the executor.
    std::atomic<int32_t> swap_armed;
    // Test hook (set vanish_marker 1): the next unlink or rmdir on A also removes /yaffs2/bootloader.dev.
    std::atomic<int32_t> vanish_marker;
    // Test hook (control "advance MS"): added to the cycle counter of both processors, so time-outs
    // the device code measures in cycles (a_alone_ms, say) pass without waiting for them.
    std::atomic<int64_t> clock_skip_us;
    // Processor B's OTP as its boot-ROM stand-in answers it (sim/stock.cpp: ROM 04000048/04000054):
    // fields by selector, given on the simulator's stdin at start (--otp-stdin; xy_forever/sim.py takes
    // them from the private area), never from a file or the command line. Test hook (set otp_fail N):
    // 1 the ROM answers nothing, 2 the public key is off the curve, 3 it is another valid key (the
    // P-224 generator), 4 the AES key is wrong. Counted: ROM inits and reads.
    std::atomic<int32_t> otp_fail, otp_inits, otp_reads;
    // Plan 0.9a (sim/boot.cpp, decision record 016): per processor, 1 while its stock bootloader
    // stand-in runs (after a reset, until it boots XY Forever); WDOG0 expiries seen; control
    // "expire a|b" (the guard counts out at the processor's next host call, if armed). The
    // board's reset delays (ms from a reset until the processor's bootloader is on the link) and
    // whether the requests A took and never answered reach A's bootloader (Test 108; switchable).
    std::atomic<int32_t> in_bootloader[2], expiries[2], expire[2];
    std::atomic<int32_t> a_boot_ms, b_boot_ms, a_redeliver;
    // set a_stall_now MS (0.9k1): A's main thread (its loop) stalls MS ms of device time at its next
    // host call (a hung A; the link thread still echoes op-10). odd_selects: B's tagged selects
    // (command_tag in the argument's high half) whose low three bits are not 0 (A's bootloader's
    // select takes the argument & 7).
    std::atomic<int32_t> a_stall_now, odd_selects;
    // Test hook (set b_queue N): B's SPI driver is busy from B's Nth file request from now on (command
    // items and keepalives pass): that request and later ones wait in B and are serialized
    // when b_queue is set to 0 (the stock path serializer reads the path buffer, and takes its length,
    // only then). b_queued: requests held that way whose answer has not reached B yet.
    // Test hook (set cut_body N): the next reply body longer than N bytes reaches B cut after N bytes,
    // and then nothing more from A reaches B (the link dropped mid-body) until B restarts.
    std::atomic<int32_t> b_queue, b_queued, cut_body;
    // Plan 0.9k1 (decision record 022 §1, §6): a reset of B lands in B's bootloader, which probes A
    // (one byte) before the display, USB or the link: a freshly reset A (boot ROM, 0xff) gets its
    // bootloader pushed and both go on; an A already running anything (its bootloader with the slave
    // open, XY Forever) makes B wait with no bound until A resets. The board decides at B's reset:
    // b_gate opens if A reset at most boot_a_window_ms before (device: about 0.1 s worked; the boundary
    // lies between 0.15 s and 1.4 s [U]), or later at any reset of A. b_waits_a: 1 while B's
    // bootloader waits (off USB, the screen keeps its last frame). The reset call itself always
    // completes. Test hook (set b_close_stuck 1): B's next master close leaves SPI and DMA enabled
    // and the link interrupts registered (a close that did not take).
    std::atomic<int32_t> boot_a_window_ms, b_gate, b_waits_a, b_close_stuck;
    // 4.7a1: opens of anything under /yaffs2/xy-forever/stage2/ on A (a plain boot makes none), and
    // stage-2 calls through the trampoline stand-in (sim/stage2.cpp).
    std::atomic<int32_t> stage2_opens, stage2_calls;
    // 5.4d2f (set unmount_ms MS): every read-write yaffs unmount on A (the window close; the device's
    // checkpoint after megabytes of writes took 0.3 s to about 17 s) takes MS ms more of host time (set 0:
    // a checkpoint in progress ends).
    std::atomic<int32_t> unmount_ms;
    // 191 (set mount_ms MS): every read-only yaffs mount on A (a full scan after an unclean shutdown)
    // takes MS ms more of host time; set 0: a mount in progress ends.
    std::atomic<int32_t> mount_ms;
    // 193 (set rw_mount_ms MS): every read-write yaffs mount on A (the write window's open: the first after
    // an unclean shutdown is slow on the device) takes MS ms more of host time; set 0: a mount in progress ends.
    std::atomic<int32_t> rw_mount_ms;
    // 192 (set needs_repair 1): A's yaffs after an unclean shutdown: every read-only mount wants a
    // media change (it calls the erase callback, dev +0x88) and fails, until a read-write mount is
    // followed by its unmount (the checkpoint; a power cut before that leaves it set).
    std::atomic<int32_t> needs_repair;
    // 5.4d3a (set rename_fault N, rename_fault_kind K): the Nth yaffs rename on A from now fails with EIO
    // (K 1, nothing renamed), is followed by a power cut (K 2) or leaves A silent from then on (K 3: a_silent).
    std::atomic<int32_t> rename_fault, rename_fault_kind;
    // 8.3 test hooks (control "knob ID DELTA [COUNT]", "knob_raw HEX [COUNT]", "knob_jump N"): PSoC frames for
    // B's stock receive queue, as the stock UART handler stores them. B's menu loop takes them where the stock
    // poll runs (sim/devices.cpp): each into slot write % 128, the write index advanced, then the read index
    // set to it (the poll pops them; type 0x45 is dropped). set knob_per_pass N: at most N frames per pass (0
    // all); set knob_wrap 1: the indexes wrap at 128 (else they count on); knob_jump: both indexes step by N
    // (frames lost or a restart); set knob_identity_fail 1: the stock identity the reader depends on differs.
    struct KnobFrame { uint8_t length, bytes[47]; };
    static const uint32_t knob_frames = 512;
    KnobFrame knob_frame[knob_frames];
    std::atomic<uint32_t> knob_head, knob_tail;
    std::atomic<int32_t> knob_jump, knob_per_pass, knob_wrap, knob_identity_fail;
    // 4.8 test hook (control "realkey K P"): key events from the PSoC (not the host's KEY command), key | P << 8;
    // B's menu loop takes them where the stock poll runs.
    static const uint32_t real_keys = 64;
    uint32_t real_key[real_keys];
    std::atomic<uint32_t> real_head, real_tail;
    uint8_t otp_field[16][64];
    uint8_t otp_length[16];
    char swap_path[240];
    uint32_t capacity;                           // boot partition bytes (each of the two)
    int port;                                    // the USB CDC endpoint's TCP port (127.0.0.1)
    char root[1024], bootpart[1024];
};
extern Shared* shared;
extern int role;                                 // 0 = processor A, 1 = processor B
extern int link_fd;
extern const char* stock_path;                   // the stock memory image (xy_forever/sim.py)                              // this processor's end of the link (to the board)
void log(const char* format, ...) __attribute__((format(printf, 1, 2)));
[[noreturn]] void crash(const char* format, ...) __attribute__((format(printf, 1, 2)));
[[noreturn]] void exit_processor(int code);
uint64_t now_us();
// Link messages (processor <-> board <-> processor), little endian, header + body.
enum Kind : uint32_t { k_request = 1, k_response = 2, k_event = 3 };
enum Event : uint32_t { e_cut = 1 };
struct Message {
    uint32_t magic, kind, tag, cls, op, a0, a1, a2, result, error, body;
};
static const uint32_t message_magic = 0x4d495358u;   // "XSIM"
bool send_message(int fd, const Message& m, const void* body);   // whole message or false
// Stock SPI request classes (storage_server.hpp, peer_services.cpp); 0 = generic op.
static const uint32_t cls_partition = 0x11907694u, cls_path = 0x119076b4u, cls_read = 0x11907368u,
                      cls_sector = 0x11907348u, cls_write = 0x11907710u, cls_query = 0x11907388u;
// Link services (sim/link.cpp).
void link_start();                               // the receiving thread ("interrupt") of this processor
// B: send a request and wait for its answer (stock client RPC: unbounded, as 889f7b12).
uint32_t b_call(uint32_t cls, uint32_t op, uint32_t a0, uint32_t a1, uint32_t a2, const void* body, uint32_t bytes,
                void* out, uint32_t capacity, uint32_t* error = 0);
// B: the session's request object (stock request_ array): builders and submit.
// a2: the slot's +0xc (the simulator's own field); body: sent with the request (path, write data).
void b_build(void* request, uint32_t cls, uint32_t op, uint32_t a0, uint32_t a1, uint32_t a2, const void* body,
             uint32_t bytes, void* out, uint32_t capacity);
void b_submit(void* request);
// B (plan 0.9h): the master close (889f562e) abandons the requests B sent (or its driver holds)
// with no answer yet: SPI and DMA off, handlers gone, nothing completes them afterwards.
void b_link_closed();
// A: answer the request in a pool slot (stock 889f6508); executors use it too.
void a_respond(uint32_t request);
void a_service();                                // A: deliver a queued request if the slot is free
// A: fixed buffers in A's SDRAM for request bodies.
static const uint32_t a_in_buffer = 0x8aa70000u, a_out_buffer = 0x8aa80000u, a_buffer_bytes = 0x10000u;
// A: the cold retained state the 1.2.1 bootloader leaves (sim/devices.cpp).
static const uint32_t a_driver = 0x88a66000u, b_driver = 0x88a66000u, primary = 0x88a66400u;
uint32_t driver();
// Storage (sim/yaffs.cpp): the host folder standing in for /yaffs2, with the mount state.
void storage_op_done();                          // one mutating storage operation finished (power-cut hook)
// Plan 0.9a (sim/boot.cpp): WDOG0 and the stock bootloader after a lone reset.
uint64_t device_cycles();                        // this processor's CCLK cycle count (advance included)
void watchdog_start();                           // processor start: WDOG0 at its reset value, this thread is main
void watchdog_poll();                            // at host calls on the main thread: count, reload, expire
void stock_bootloader();                         // after a reset: the stock bootloader's side of the boot
void a_boot_request(uint32_t request);           // A's bootloader: answer one pool request (sim/stock.cpp)
}
#endif

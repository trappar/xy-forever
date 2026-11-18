// The stock 1.2.1 bootloader's side of a boot after one processor resets alone. Tags in comments:
// [L] read from the 1.2.1 listing, [F] earlier device finding, [I] inferred, [U] unknown.
//
// host_poll(), called at host calls on the processor's main thread (cycles(), stock calls, B's
// stock request wait), runs control "set a_stall_now MS" on A.
#include "runtime.hpp"
#include <pthread.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>

namespace xyf_sim {
namespace {
pthread_t main_thread;
bool have_main = false;
}

void host_start() { main_thread = pthread_self(); have_main = true; }

void host_poll() {
    if (!have_main || !pthread_equal(pthread_self(), main_thread)) return;
    if (role == 0 && !shared->in_bootloader[0])
        if (const int32_t ms = shared->a_stall_now.exchange(0)) {    // control "set a_stall_now MS"
            log("control: A's loop stalls %d ms (device time)", ms);
            const uint64_t until = device_cycles() + uint64_t(ms) * 400000u;
            while (device_cycles() < until) usleep(1000);
        }
}

// ---- the stock bootloader after a lone reset ------------------------------------------------------
// The simulator does not run os.ldr: whatever a bootloader boots (the image it read, or what
// 0x8a000000 still holds) is XY Forever's cold entry again (processor() in sim/runtime.cpp). What is
// modelled is the link: who answers B's boot requests, with what, and what each side then does.
namespace {
uint32_t generic(uint32_t op, uint32_t a0) { return b_call(0, op, a0, 0, 0, 0, 0, 0, 0); }
uint32_t select(uint32_t p) { return b_call(cls_partition, 0x18, p, 0, 0, 0, 0, 0, 0); }
void read_retried(uint32_t sector, uint32_t count, void* out) {      // a failed read is retried forever [L]
    bool noted = false;
    while (b_call(cls_sector, count == 1 ? 0x11 : 0x12, sector, count, 0, 0, 0, out, count * 512u)) {
        if (!noted) log("stock bootloader (B): rsi read error at sector %u - retry", sector);
        noted = true;
    }
}
// 88a07240(0x8a000000, 0x800000) [L]: select 1 (a nonzero answer is returned as the length); sector 0
// (retried); not active: select 2, sector 0; active: sectors 1.. in batches of 128 (retried), CRC;
// then select 0 until it answers 0; returns the image size, or 0 for no (valid) firmware.
bool leftover = false;
uint32_t fetch() {
    const uint32_t refused = select(1);
    leftover = refused != 0;
    if (refused) {
        log("stock bootloader (B): select 1 answered %08x: taken as the length read", refused);
        return refused;
    }
    uint8_t sector[512];
    read_retried(0, 1, sector);
    bool found = sector[12] != 0;
    if (!found && !select(2)) { read_retried(0, 1, sector); found = sector[12] != 0; }
    uint32_t size = 0;
    memcpy(&size, sector + 4, 4);
    if (found && size <= 0x800000u)
        for (uint32_t at = 0, left = (size >> 9) + 1; left;) {
            const uint32_t n = left < 128 ? left : 128;
            read_retried(1 + at, n, host(0x8a000000u + at * 512u));
            at += n; left -= n;
        }
    while (select(0)) {}
    // The simulator's boot partition file is only the evidence of installs (it starts empty), and its
    // bytes are never run: an empty or invalid one counts as XY Forever read (the stock bootloader would
    // log "failed to find any active firmware" and not boot [L]; CRC not checked).
    if (!found || size > 0x800000u) log("stock bootloader (B): no active firmware in the file: XY Forever taken as read");
    return found && size <= 0x800000u && size ? size : 1;
}

// 88a1d34c [L]: early in B's bootloader (before the display reset, USB and the link)
// B sends A 0x03. A freshly reset A (its boot ROM answers 0xff) gets the bootloader's LDR blocks from
// B's flash, and both go on; any other answer (A running its bootloader or XY Forever) makes B send
// eleven 0x0f and wait for PA6 with no bound: it goes on only once A resets. The board decides which
// (b_gate, runtime.cpp: A reset at most boot_a_window_ms before B's reset, or at any time after it).
void b_boot_a() {
    if (!shared->b_gate.load()) {
        shared->b_waits_a = 1;
        log("stock bootloader (B): A is already running (probe not 0xff): B waits for A with no bound "
            "(off USB, the screen keeps its last frame) until A resets");
        while (!shared->b_gate.load()) usleep(1000);
        shared->b_waits_a = 0;
        log("stock bootloader (B): A reset: B goes on");
    }
    while (!shared->in_bootloader[0].load()) usleep(1000);    // B pushes A's bootloader; A's slave opens
}

void b_bootloader() {
    b_boot_a();
    log("stock bootloader (B): handshake, then the boot partition through A (every request waits without a bound)");
    uint32_t r = generic(1, 0);                        // 889f55a2: asserts init_request.m_result == 0xfedebeda [L]
    if (r != 0xfedebedau) { log("stock bootloader (B): handshake answered %08x: assert, halted", r); exit_processor(exit_halt); }
    r = generic(10, 0x12345678u);                      // 889f55f4: asserts the complement [L]
    if (r != 0xedcba987u) { log("stock bootloader (B): op 10 answered %08x: assert, halted", r); exit_processor(exit_halt); }
    const uint32_t length = fetch();
    // 88a34a00 [L]: any nonzero length counts as read: release A (op 7, 889f7b56, no bound), teardown,
    // RAM boot of 0x8a000000. After a refused select that is what memory still holds: [I] (016 §1) the
    // XY Forever os.ldr the last boot loaded there, so XY Forever's cold entry again.
    generic(7, 0);
    log("stock bootloader (B): %s (length %u), released A: booting from RAM", leftover
        ? "select refused, 0x8a000000 boots as it is" : "image read", length);
}

// 88a35b90 [L]: A serves B's requests until B's release (op 7, the slave ISR sets the release byte,
// 889f7ad8), with no time-out, then reads os.ldr itself and boots. Its slave answers generic ops
// (1, 10 echoes for any argument [L 889f7af4], 5, 7) from sim/link.cpp; class requests come here.
void a_bootloader() {
    log("stock bootloader (A): slave open, serving B until its release (op 7), no time-out");
    w32(0x8804f6e0u, primary); w32(primary, 0); w32(primary + 4, 0);
    w32(0x20057004u, r32(0x20057004u) | 0x2000u);      // slave open (link.cpp answers only then)
    for (;;) {
        a_service();
        if (const uint32_t request = r32(primary + 4)) { w32(primary + 4, 0); a_boot_request(request); continue; }
        if (r8(primary)) break;
        usleep(200);
    }
    w32(0x20057004u, r32(0x20057004u) & ~0x2000u);     // teardown: slave closed
    log("stock bootloader (A): released: reads its own os.ldr and boots it");
}
}

// B at power-on: what the stock bootloader's fetch left at the RAM-boot source 0x8a000000 (the image
// of the active boot partition, read from the file directly; hello image= hashes it). Nothing when none.
void power_on_image() {
    const int fd = open(shared->bootpart, O_RDONLY);
    if (fd < 0) return;
    uint8_t sector[512];
    for (uint32_t p = 0; p != 2; ++p) {
        const off_t at = off_t(p * uint64_t(shared->capacity));
        uint32_t size = 0;
        if (pread(fd, sector, 512, at) != 512 || !sector[12]) continue;
        memcpy(&size, sector + 4, 4);
        if (size && size <= 0x800000u && size + 512u <= shared->capacity)
            if (pread(fd, host(0x8a000000u), size, at + 512) == ssize_t(size)) log("stock bootloader (B): image read at power-on (%u bytes)", size);
        break;
    }
    close(fd);
}

void stock_bootloader() {
    shared->in_bootloader[role] = 1;
    if (role == 0) a_bootloader(); else b_bootloader();
    shared->in_bootloader[role] = 0;
}
}

// XY Forever device simulator: emulated memory, the board (power, processors, the link
// relay with fault injection, the control port) and each processor's run.
#include "runtime.hpp"
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <mutex>
#include <new>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <string>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <deque>
#include <map>
#include <vector>

namespace xyf_sim {
Shared* shared = 0;
int role = -1;
int link_fd = -1;

// ---- memory -----------------------------------------------------------------------------
namespace {
struct Region { uint32_t begin, end; };
const Region regions[] = {
    {0x08000000u, 0x08100000u},     // L2
    {0x11800000u, 0x11c00000u},     // L1 data and instruction
    {0x1fc00000u, 0x1fd00000u},     // core MMRs
    {0x20000000u, 0x20100000u},     // system MMRs
    {0x80000000u, 0x90000000u},     // SDRAM (last: shared per processor)
};
const unsigned sdram = sizeof(regions) / sizeof(regions[0]) - 1;
uintptr_t base = 0;
const uint32_t fake_base = 0xc0000000u, slot_bits = 20, slots = 1024;
uintptr_t slot_host[slots];
unsigned slot_count = 0;
std::mutex slot_lock;

void map_region(const Region& r, int fd) {
    void* want = reinterpret_cast<void*>(base + r.begin);
    void* got = fd < 0 ? mmap(want, r.end - r.begin, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE | MAP_FIXED, -1, 0)
                       : mmap(want, r.end - r.begin, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_FIXED, fd, 0);
    if (got != want) { fprintf(stderr, "xyf_sim: cannot map %08x\n", r.begin); _exit(exit_crash); }
}
void ensure() {
    if (base) return;
    void* window = mmap(0, size_t(1) << 32, PROT_NONE, MAP_ANON | MAP_PRIVATE, -1, 0);
    if (window == MAP_FAILED) { fprintf(stderr, "xyf_sim: no address window\n"); _exit(exit_crash); }
    base = reinterpret_cast<uintptr_t>(window);
    for (const Region& r : regions) map_region(r, -1);
}
}
bool mapped(uint32_t address, uint32_t bytes) {
    for (const Region& r : regions)
        if (address >= r.begin && address <= r.end && bytes <= r.end - address) return true;
    return false;
}
void* host(uint32_t address) {
    ensure();
    if (address >= fake_base) {
        const uint32_t slot = (address - fake_base) >> slot_bits;
        if (slot < slot_count) return reinterpret_cast<void*>(slot_host[slot] + (address & ((1u << slot_bits) - 1)));
    } else if (mapped(address, 1)) return reinterpret_cast<void*>(base + address);
    crash("device code used unmapped address %08x", address);
}
uint32_t device(uintptr_t p) {
    ensure();
    if (!p) return 0;
    if (p >= base && p - base < (uintptr_t(1) << 32)) return uint32_t(p - base);
    std::lock_guard<std::mutex> hold(slot_lock);
    const uintptr_t page = p & ~((uintptr_t(1) << slot_bits) - 1);
    for (unsigned i = 0; i != slot_count; ++i)
        if (slot_host[i] == page) return fake_base + (i << slot_bits) + uint32_t(p - page);
    if (slot_count == slots) crash("fake address window full");
    slot_host[slot_count] = page;
    return fake_base + (slot_count++ << slot_bits) + uint32_t(p - page);
}
uint32_t r32(uint32_t a) { return *static_cast<volatile uint32_t*>(host(a)); }
void w32(uint32_t a, uint32_t v) { *static_cast<volatile uint32_t*>(host(a)) = v; }
uint8_t r8(uint32_t a) { return *static_cast<volatile uint8_t*>(host(a)); }
void w8(uint32_t a, uint8_t v) { *static_cast<volatile uint8_t*>(host(a)) = v; }
void reset_private_memory() { ensure(); for (unsigned i = 0; i != sdram; ++i) map_region(regions[i], -1); }
void attach_sdram(int fd) { ensure(); map_region(regions[sdram], fd); }

uint32_t stack_pointer() { return 0x8ab80200u; }   // module stack (WIDE layout: WIDE_STACK + 0x200), below XYF_STACK_END

void log(const char* format, ...) {
    char text[600];
    va_list args; va_start(args, format); vsnprintf(text, sizeof(text), format, args); va_end(args);
    fprintf(stderr, "[sim %s %8.3f] %s\n", role == 0 ? "A" : role == 1 ? "B" : "board", double(now_us() % 100000000u) / 1e6,
            text);
}
void crash(const char* format, ...) {
    char text[600];
    va_list args; va_start(args, format); vsnprintf(text, sizeof(text), format, args); va_end(args);
    fprintf(stderr, "[sim %s] CRASH: %s\n", role == 0 ? "A" : role == 1 ? "B" : "board", text);
    fflush(stderr);
    _exit(exit_crash);
}
void exit_processor(int code) { fflush(stderr); _exit(code); }

bool send_message(int fd, const Message& m, const void* body) {
    static std::mutex lock;
    std::lock_guard<std::mutex> hold(lock);
    if (role == 0 || role == 1) { ++shared->outflight[role]; active(); }   // sim/clock.cpp
    const uint8_t* parts[2] = {reinterpret_cast<const uint8_t*>(&m), static_cast<const uint8_t*>(body)};
    const size_t sizes[2] = {sizeof(m), body ? m.body : 0};
    for (int k = 0; k != 2; ++k)
        for (size_t at = 0; at < sizes[k];) {
            const ssize_t n = write(fd, parts[k] + at, sizes[k] - at);
            if (n < 0 && errno == EINTR) continue;
            if (n < 0 && errno == EAGAIN) { pollfd p = {fd, POLLOUT, 0}; ::poll(&p, 1, 100); continue; }
            if (n <= 0) return false;
            at += size_t(n);
        }
    return true;
}
}

// ---- processors ---------------------------------------------------------------------------
extern "C" void xy_forever_file_boot();
extern "C" volatile uint32_t xy_forever_storage_image_loaded;
extern "C" void xy_forever_no_image();
namespace xyf_sim {
void cold_state(int role, bool power_on, bool play);   // sim/devices.cpp
void load_stock();                                     // sim/devices.cpp
void processor(int which, int fd, int sdram_fd, bool power_on) {
    role = which; link_fd = fd;
    if (which == 0) shared->partition = 0;             // A's stock entry runs from the user area again
    signal(SIGPIPE, SIG_IGN);
    reset_private_memory();
    attach_sdram(sdram_fd);
    load_stock();
    cold_state(which, power_on, power_on && shared->play.load());
    host_start();
    link_start();
    // Power-on stands for the stock bootloader's normal boot (it loaded XY Forever); after a reset
    // the bootloader stand-in runs its side of the boot first (sim/boot.cpp; it may never return).
    if (power_on && which == 1) power_on_image();
    if (!power_on) {
        stock_bootloader();
        load_stock();
        cold_state(which, false, false);              // the retained state its teardown leaves
        host_start();
    }
    log("cold entry (%s)", power_on ? "power-on" : "reset");
    xy_forever_file_boot();                            // cold.cpp: the boot decision
    if (xy_forever_storage_image_loaded == 1) {
        log("committed: would start the OS image now (the simulator stops this processor)");
        exit_processor(exit_os);
    }
    xy_forever_no_image();                             // cold.cpp: no image, halt until a power cycle
    log("halted (no image): needs a power cycle");
    exit_processor(exit_halt);
}
}

// ---- board --------------------------------------------------------------------------------
namespace {
using namespace xyf_sim;
struct Proc {
    pid_t pid = 0;
    int fd = -1;                    // board end of its link
    std::string state = "off";      // off, running, halted, os, crashed, resetting
    int boots = 0;
    uint64_t restart_at = 0;        // us; 0 = none
    std::vector<uint8_t> in;        // partial message
    bool cut = false;               // B: a reply was cut mid-body (cut_body); nothing more reaches it
};
Proc procs[2];
int sdram_fds[2] = {-1, -1};
struct Pending { uint64_t at; Message m; std::vector<uint8_t> body; };
std::deque<Pending> to_a;           // delayed B -> A messages (A slow)
int control_fd = -1;
std::vector<int> clients;
std::vector<std::string> client_in;
int power_ons = 0;
unsigned relayed[2] = {0, 0};        // link messages delivered to A and to B (status: to_a, to_b)
uint64_t cut_pending = 0;
const char* names[2] = {"A", "B"};
// Raw requests to A sent by the board itself, as if from B but past B's own checks:
// A must refuse every brick path on its own. Their answers stay here; B never sees them.
const uint32_t inject_bit = 0x80000000u;
struct Injected { bool done; uint32_t result, error, body; };
std::map<uint32_t, Injected> injected;
uint32_t inject_count = 0;
// Class requests B sent that A took and has not answered (by tag). After a reset of A
// they reach A's bootloader once it is on the link (a_redeliver; Test 108: "the request still in
// flight" was answered by A's bootloader [F]; how the stock link carries it over is not known [U]).
// A request sent while a processor is resetting is lost (forward(): nobody on the link) [I: 154's
// USB loss 25 s + B's 1 s patience after A's alone reset needs B's keepalive to go unanswered].
struct Held { Message m; std::vector<uint8_t> body; };
std::map<uint32_t, Held> outstanding;
uint64_t board_start_us = 0;
int64_t b_sent_ms = -1, reset_ms[2] = {-1, -1};      // board clock (ms) of B's last link message, last resets
uint64_t board_us() {                                // board clock (restarts, delayed messages): sim/clock.cpp
    return virtual_clock() ? shared->clock_ns.load() / 1000u : now_us() + uint64_t(shared->clock_skip_us.load());
}
int64_t board_ms() {                                 // since start, at the device clocks' pace
    if (virtual_clock()) return int64_t(shared->clock_ns.load() / 1000000u);
    return int64_t((now_us() - board_start_us) / 1000u) + int64_t(shared->clock_skip_us.load() / 1000);
}

int make_sdram() {                                 // a sparse unlinked file: one per processor
    const char* dir = getenv("TMPDIR");
    std::string name = std::string(dir && *dir ? dir : "/tmp") + "/xyfsim-sdram-XXXXXX";
    std::vector<char> path(name.begin(), name.end());
    path.push_back(0);
    const int fd = mkstemp(path.data());
    if (fd < 0) crash("SDRAM file: %s", strerror(errno));
    unlink(path.data());
    if (ftruncate(fd, 0x10000000)) crash("ftruncate: %s", strerror(errno));
    return fd;
}
void clear_sdram(int which) {
    if (ftruncate(sdram_fds[which], 0) || ftruncate(sdram_fds[which], 0x10000000)) crash("ftruncate");
}
bool forward(int to, const Message& m, const std::vector<uint8_t>& body);
void start(int which, bool power_on) {
    Proc& p = procs[which];
    int pair[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, pair)) crash("socketpair");
    const pid_t pid = fork();
    if (pid < 0) crash("fork");
    if (!pid) {
        close(pair[0]);
        if (control_fd >= 0) close(control_fd);
        for (int c : clients) close(c);
        for (int k = 0; k != 2; ++k) if (procs[k].fd >= 0) close(procs[k].fd);
        processor(which, pair[1], sdram_fds[which], power_on);
    }
    close(pair[1]);
    fcntl(pair[0], F_SETFL, O_NONBLOCK);
    p.pid = pid; p.fd = pair[0]; p.state = "running"; ++p.boots; p.restart_at = 0; p.in.clear(); p.cut = false;
    kick(which);                                    // not idle until it says so (sim/clock.cpp)
    if (which == 0) to_a.clear();
    if (power_on) return;
    if (which == 1) { outstanding.clear(); return; }  // the master that sent them is gone
    std::map<uint32_t, Held> carried;
    carried.swap(outstanding);
    if (!shared->a_redeliver.load()) return;
    for (auto& h : carried) {
        log("board: request op %02x (tag %u) that A took before its reset reaches A's bootloader", h.second.m.op,
            h.first);
        forward(0, h.second.m, h.second.body);
    }
}
// A reset of one processor alone (its handler's stock reset, or control "reset a|b"): its link end
// goes quiet for its bootloader's start time; the other processor keeps running.
void resetting(int which) {
    Proc& p = procs[which];
    p.state = "resetting";
    shared->in_bootloader[which] = 0;
    // sim/boot.cpp b_boot_a: a reset of A lets a B that is resetting or waits in its
    // bootloader through; a reset of B gets through at once only if A reset at most boot_a_window_ms
    // before it (A still in its boot ROM).
    if (which == 0) shared->b_gate = 1;
    else {
        shared->b_waits_a = 0;
        shared->b_gate = reset_ms[0] >= 0 && board_ms() - reset_ms[0] <= shared->boot_a_window_ms.load() ? 1 : 0;
        log("board: B resets %lld ms after A's last reset: its bootloader %s", (long long)(reset_ms[0] >= 0 ?
            board_ms() - reset_ms[0] : -1), shared->b_gate.load() ? "finds A fresh" : "will find A running");
    }
    // On the board clock (sim/clock.cpp), so a test can pass the start time unwaited.
    p.restart_at = board_us() + uint64_t(which ? shared->b_boot_ms.load() : shared->a_boot_ms.load()) * 1000u;
    reset_ms[which] = board_ms();
    if (which == 1) shared->b_listening = 0;
    if (which == 0) to_a.clear();
}
void stop(int which) {
    Proc& p = procs[which];
    if (p.pid > 0) { kill(p.pid, SIGKILL); waitpid(p.pid, 0, 0); }
    if (p.fd >= 0) close(p.fd);
    board_reset_flight(which);
    p.pid = 0; p.fd = -1; p.restart_at = 0; p.in.clear();
}
void power_cycle(bool play, const char* why) {
    for (int k = 0; k != 2; ++k) stop(k);
    to_a.clear();
    outstanding.clear();
    for (int k = 0; k != 2; ++k) {
        clear_sdram(k); procs[k].state = "off"; shared->in_bootloader[k] = 0; reset_ms[k] = -1;
    }
    shared->b_waits_a = 0; shared->b_gate = 0;
    shared->play = play ? 1 : 0;
    shared->b_listening = 0;
    ++power_ons;
    log("power on (%s, %s)", why, play ? "PLAY held" : "no keys");
    start(1, true);                      // B enters first on the device; A about 0.5 s later
    start(0, true);
}
bool forward(int to, const Message& m, const std::vector<uint8_t>& body) {
    Proc& p = procs[to];
    if (p.fd < 0 || p.state != "running") return false;    // a dead, halted or resetting processor hears nothing
    ++shared->inflight[to];                                // taken by its link thread (sim/link.cpp)
    if (!send_message(p.fd, m, body.empty() ? 0 : body.data())) { --shared->inflight[to]; return false; }
    board_link_cost(uint32_t(body.size()));
    ++relayed[to];
    if (to == 0 && m.kind == k_request && m.cls && !(m.tag & inject_bit)) outstanding[m.tag] = {m, body};
    return true;
}
void route(int from, const Message& m, std::vector<uint8_t>& body) {
    if (m.kind == k_event) {
        if (m.op == e_cut) { log("power cut after storage operation %d", shared->storage_ops.load()); cut_pending = 1; }
        return;
    }
    if (from == 1) {                                       // B -> A
        b_sent_ms = board_ms();
        if (m.cls == cls_partition && m.op == 0x18 && (m.a0 >> 16) == 0x434du) {
            if (m.a0 & 7u) {                               // never from XY Forever's B
                ++shared->odd_selects;
                log("board: B's tagged select %08x has low bits %u", m.a0, m.a0 & 7u);
            }
        }
        if (shared->a_silent) { if (m.cls) ++shared->a_silenced; return; }
        if (m.cls && shared->drop_request > 0) { --shared->drop_request; log("fault: dropped request op %02x", m.op); return; }
        // A slow: A's loop takes each class request N ms late; the stock ISR still answers
        // generic ops (handshake, op-10 echoes, release) at once.
        const int32_t slow = shared->a_slow_ms;
        if (m.cls && (slow > 0 || !to_a.empty())) {
            to_a.push_back({board_us() + uint64_t(slow > 0 ? slow : 0) * 1000u, m, body});
            return;
        }
        forward(0, m, body);
    } else {                                               // A -> B
        if (m.kind == k_response && (m.tag & inject_bit)) {
            auto it = injected.find(m.tag);
            if (it != injected.end()) it->second = {true, m.result, m.error, m.body};
            return;
        }
        if (m.kind == k_response) outstanding.erase(m.tag);
        if (shared->a_silent) { if (m.kind == k_response && m.cls) ++shared->a_silenced; return; }
        if (m.body && shared->drop_body > 0) { --shared->drop_body; log("fault: dropped reply body op %02x", m.op); return; }
        if (procs[1].cut) return;                          // the link dropped mid-body: B hears nothing more
        const int32_t cut = shared->cut_body;
        if (m.kind == k_response && cut > 0 && uint32_t(cut) < m.body && procs[1].fd >= 0 && procs[1].state == "running") {
            shared->cut_body = 0;                          // the header and cut bytes of the body, then nothing
            std::vector<uint8_t> frame(sizeof(m) + uint32_t(cut));
            memcpy(frame.data(), &m, sizeof(m));
            memcpy(frame.data() + sizeof(m), body.data(), uint32_t(cut));
            for (size_t at = 0; at < frame.size();) {
                const ssize_t n = write(procs[1].fd, frame.data() + at, frame.size() - at);
                if (n > 0) at += size_t(n);
                else if (n < 0 && errno != EAGAIN && errno != EINTR) break;
            }
            procs[1].cut = true;
            log("fault: reply op %02x cut after %d of its %u body bytes; the link to B is down", m.op, cut, m.body);
            return;
        }
        forward(1, m, body);
    }
}
void drain(int which) {
    Proc& p = procs[which];
    uint8_t chunk[65536];
    for (;;) {
        const ssize_t n = read(p.fd, chunk, sizeof(chunk));
        if (n <= 0) break;
        p.in.insert(p.in.end(), chunk, chunk + n);
    }
    while (p.in.size() >= sizeof(Message)) {
        Message m; memcpy(&m, p.in.data(), sizeof(m));
        if (m.magic != message_magic) crash("bad link message from %s", names[which]);
        if (p.in.size() < sizeof(m) + m.body) break;
        std::vector<uint8_t> body(p.in.begin() + sizeof(m), p.in.begin() + sizeof(m) + m.body);
        p.in.erase(p.in.begin(), p.in.begin() + sizeof(m) + m.body);
        --shared->outflight[which];
        route(which, m, body);
    }
}
void reap() {
    for (;;) {
        int status = 0;
        const pid_t pid = waitpid(-1, &status, WNOHANG);
        if (pid <= 0) return;
        for (int k = 0; k != 2; ++k) {
            Proc& p = procs[k];
            if (p.pid != pid) continue;
            if (p.fd >= 0) { drain(k); close(p.fd); }
            p.pid = 0; p.fd = -1;
            board_reset_flight(k);
            const int code = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
            if (k == 1) shared->b_listening = 0;
            if (code == exit_reset) resetting(k);
            else if (code == exit_halt) p.state = "halted";
            else if (code == exit_os) p.state = "os";
            else { p.state = "crashed"; log("processor %s ended (status %d)", names[k], status); }
            if (k == 0) to_a.clear();
        }
    }
}
int otp_fields() { int n = 0; for (uint8_t length : shared->otp_length) n += length != 0; return n; }
// --otp-stdin: records {u8 selector < 16, u8 length <= 64, bytes} until the end of stdin.
bool load_otp() {
    uint8_t head[2];
    while (fread(head, 1, 2, stdin) == 2) {
        if (head[0] >= 16 || !head[1] || head[1] > 64 ||
            fread(shared->otp_field[head[0]], 1, head[1], stdin) != head[1]) return false;
        shared->otp_length[head[0]] = head[1];
    }
    return true;
}
void wipe_otp() {
    volatile uint8_t* p = &shared->otp_field[0][0];
    for (size_t i = 0; i != sizeof(shared->otp_field); ++i) p[i] = 0;
}
std::string status_json() {
    char text[2000], clock[200];
    snprintf(clock, sizeof(clock), "\"virtual\": %d, \"steps\": %u, \"work\": [%u, %u], \"idle\": [%d, %d], \"flight\": [%d, %d, %d, %d], \"pids\": [%d, %d], \"pid\": %d",
             shared->virtual_clock.load(), shared->steps.load(), shared->work[0].load(), shared->work[1].load(),
             shared->idle_work[0] == shared->work[0] && shared->idle_steps[0] == shared->steps,
             shared->idle_work[1] == shared->work[1] && shared->idle_steps[1] == shared->steps,
             shared->inflight[0].load(), shared->inflight[1].load(), shared->outflight[0].load(), shared->outflight[1].load(),
             procs[0].pid, procs[1].pid, getpid());
    snprintf(text, sizeof(text),
             "{\"a\": \"%s\", \"b\": \"%s\", \"a_boots\": %d, \"b_boots\": %d, \"power_ons\": %d, \"listening\": %d, "
             "\"storage_ops\": %d, \"cuts\": %d, \"a_silent\": %d, \"a_slow_ms\": %d, \"drop_request\": %d, "
             "\"drop_body\": %d, \"cable_pull\": %d, \"cut_after\": %d, \"fail_write\": %d, \"space\": %lld, "
             "\"partition\": %d, \"boot_writes\": %d, \"rw_open\": %d, \"unclean_mounts\": %d, \"clock_skip_ms\": %lld, "
             "\"to_a\": %u, \"to_b\": %u, \"otp_fields\": %d, \"otp_fail\": %d, \"otp_inits\": %d, \"otp_reads\": %d, "
             "\"a_bootloader\": %d, \"b_bootloader\": %d, \"clock_ms\": %lld, "
             "\"b_sent_ms\": %lld, \"a_reset_ms\": %lld, \"b_reset_ms\": %lld, \"outstanding\": %zu, "
             "\"odd_selects\": %d, \"b_queue\": %d, \"b_queued\": %d, \"cut_body\": %d, "
             "\"boot_a_window_ms\": %d, \"b_waits_a\": %d, \"b_close_stuck\": %d, \"rw_mounts\": %d, "
             "\"stage2_opens\": %d, \"stage2_calls\": %d, \"a_silenced\": %d, \"a_clock_reads\": %u, \"b_clock_reads\": %u, \"needs_repair\": %d, %s}",
             procs[0].state.c_str(), procs[1].state.c_str(), procs[0].boots, procs[1].boots, power_ons,
             shared->b_listening.load(), shared->storage_ops.load(), shared->cuts.load(), shared->a_silent.load(),
             shared->a_slow_ms.load(), shared->drop_request.load(), shared->drop_body.load(),
             shared->cable_pull.load(), shared->cut_after.load(), shared->fail_write.load(),
             (long long)shared->space.load(), shared->partition.load(), shared->boot_writes.load(),
             shared->rw_open.load(), shared->unclean_mounts.load(), (long long)(shared->clock_skip_us.load() / 1000), relayed[0], relayed[1],
             otp_fields(), shared->otp_fail.load(), shared->otp_inits.load(), shared->otp_reads.load(),
             shared->in_bootloader[0].load(), shared->in_bootloader[1].load(),
             (long long)board_ms(), (long long)b_sent_ms, (long long)reset_ms[0],
             (long long)reset_ms[1], outstanding.size(), shared->odd_selects.load(),
             shared->b_queue.load(), shared->b_queued.load(), shared->cut_body.load(),
             shared->boot_a_window_ms.load(), shared->b_waits_a.load(), shared->b_close_stuck.load(), shared->rw_mounts.load(),
             shared->stage2_opens.load(), shared->stage2_calls.load(), shared->a_silenced.load(),
             shared->clock_reads[0].load(), shared->clock_reads[1].load(), shared->needs_repair.load(), clock);
    return text;
}
std::string command(const std::string& line, bool& quit, int client) {
    char verb[32] = {0}, name[32] = {0};
    long long value = 0;
    const int n = sscanf(line.c_str(), "%31s %31s %lld", verb, name, &value);
    if (n >= 1 && !strcmp(verb, "status")) return status_json();
    if (n >= 1 && !strcmp(verb, "quit")) { quit = true; return "{\"ok\": true}"; }
    if (n >= 1 && !strcmp(verb, "power")) {
        power_cycle(n < 2 || strcmp(name, "plain"), "control");
        return "{\"ok\": true}";
    }
    if (n == 2 && (!strcmp(verb, "advance") || !strcmp(verb, "jump"))) {   // advance|jump MS: sim/clock.cpp
        const long long ms = strtoll(name, 0, 10);
        if (ms <= 0) return "{\"ok\": false, \"error\": \"advance: MS > 0\"}";
        if (verb[0] == 'j') { board_jump(ms); return "{\"ok\": true}"; }
        board_advance(client, ms);
        return "";                                        // answered when the clock is there
    }
    if (n >= 1 && !strcmp(verb, "cut")) {                 // power cut now, then power on (PLAY)
        ++shared->cuts;
        power_cycle(true, "power cut");
        return "{\"ok\": true}";
    }
    if (n >= 1 && !strcmp(verb, "inject")) {              // inject CLASS OP ARG0 ARG2 [BODY-HEX] (hex; class 0: generic)
        unsigned cls = 0, op = 0, a0 = 0, a2 = 0;
        char hex[1100] = "";
        const int k = sscanf(line.c_str(), "inject %x %x %x %x %1099s", &cls, &op, &a0, &a2, hex);
        if (k < 4 || procs[0].fd < 0 || procs[0].state != "running")
            return "{\"ok\": false, \"error\": \"inject: CLASS OP ARG0 ARG2 [BODY-HEX], A running\"}";
        std::vector<uint8_t> body;
        for (size_t i = 0; k == 5 && hex[i] && hex[i + 1]; i += 2) {
            unsigned byte = 0;
            if (sscanf(hex + i, "%2x", &byte) != 1) return "{\"ok\": false, \"error\": \"inject: bad hex\"}";
            body.push_back(uint8_t(byte));
        }
        Message m = {message_magic, k_request, inject_bit | ++inject_count, cls, op, a0, 0, a2, 0, 0,
                     uint32_t(body.size())};
        injected[m.tag] = {false, 0, 0, 0};
        log("inject: class %08x op %02x arg %08x (%zu body bytes)", cls, op, a0, body.size());
        forward(0, m, body);
        char text[64];
        snprintf(text, sizeof(text), "{\"ok\": true, \"tag\": %u}", m.tag & ~inject_bit);
        return text;
    }
    if (n == 2 && !strcmp(verb, "reset") && (!strcmp(name, "a") || !strcmp(name, "b"))) {
        const int k = name[0] == 'a' ? 0 : 1;
        if (procs[k].state != "running") return "{\"ok\": false, \"error\": \"processor not running\"}";
        log("control: reset %s alone", names[k]);         // reset a|b: no handler, no USB stop
        stop(k);
        resetting(k);
        return "{\"ok\": true}";
    }
    if (n >= 2 && !strcmp(verb, "swap")) {               // swap PATH: test hook (runtime.hpp)
        snprintf(shared->swap_path, sizeof(shared->swap_path), "%s", line.c_str() + 5);
        shared->swap_armed = 1;
        return "{\"ok\": true}";
    }
    if (n >= 2 && !strncmp(verb, "knob", 4)) return input_command(line, verb);    // sim/devices.cpp
    if (n >= 2 && !strcmp(verb, "realkey")) return input_command(line, verb);
    if (n == 2 && !strcmp(verb, "injected")) {            // injected TAG: A's answer, if any
        auto it = injected.find(inject_bit | uint32_t(strtoul(name, 0, 10)));
        if (it == injected.end()) return "{\"ok\": false, \"error\": \"no such injected request\"}";
        char text[160];
        snprintf(text, sizeof(text), "{\"ok\": true, \"done\": %d, \"result\": %d, \"error\": %u, \"body\": %u}",
                 it->second.done ? 1 : 0, int32_t(it->second.result), it->second.error, it->second.body);
        return text;
    }
    if (n == 3 && !strcmp(verb, "set")) {
        struct { const char* name; std::atomic<int32_t>* field; } fields[] = {
            {"a_silent", &shared->a_silent}, {"a_slow_ms", &shared->a_slow_ms}, {"drop_request", &shared->drop_request},
            {"drop_body", &shared->drop_body}, {"cable_pull", &shared->cable_pull}, {"cut_after", &shared->cut_after},
            {"fail_write", &shared->fail_write}, {"vanish_marker", &shared->vanish_marker},
            {"fail_mount", &shared->fail_mount}, {"otp_fail", &shared->otp_fail}, {"fail_select", &shared->fail_select},
            {"fail_unmount", &shared->fail_unmount}, {"fail_open", &shared->fail_open}, {"full_path", &shared->full_path},
            {"a_boot_ms", &shared->a_boot_ms}, {"b_boot_ms", &shared->b_boot_ms}, {"a_redeliver", &shared->a_redeliver},
            {"a_stall_now", &shared->a_stall_now}, {"b_queue", &shared->b_queue}, {"cut_body", &shared->cut_body},
            {"boot_a_window_ms", &shared->boot_a_window_ms}, {"b_close_stuck", &shared->b_close_stuck},
            {"unmount_ms", &shared->unmount_ms}, {"mount_ms", &shared->mount_ms}, {"rw_mount_ms", &shared->rw_mount_ms}, {"needs_repair", &shared->needs_repair}, {"rename_fault", &shared->rename_fault},
            {"rename_fault_kind", &shared->rename_fault_kind}, {"knob_per_pass", &shared->knob_per_pass},
            {"knob_wrap", &shared->knob_wrap}, {"knob_identity_fail", &shared->knob_identity_fail}};
        for (auto& f : fields) if (!strcmp(f.name, name)) { *f.field = int32_t(value); return "{\"ok\": true}"; }
        if (!strcmp(name, "space")) { shared->space = value; return "{\"ok\": true}"; }
    }
    return "{\"ok\": false, \"error\": \"unknown command\"}";
}
int listen_on(int port) {
    const int fd = socket(AF_INET, SOCK_STREAM, 0);
    const int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    sockaddr_in a = {};
    a.sin_family = AF_INET; a.sin_port = htons(uint16_t(port)); a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(fd, reinterpret_cast<sockaddr*>(&a), sizeof(a)) || listen(fd, 8)) crash("control port %d: %s", port, strerror(errno));
    fcntl(fd, F_SETFL, O_NONBLOCK);
    return fd;
}
int usage() {
    fprintf(stderr, "usage: xyf_sim --root DIR --bootpart FILE --stock MEMORY [--port 7341] [--control 7342]\n"
                    "               [--capacity BYTES] [--plain] [--otp-stdin]\n");
    return 2;
}
}

int main(int argc, char** argv) {
    using namespace xyf_sim;
    void* block = mmap(0, sizeof(Shared), PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANON, -1, 0);
    shared = new (block) Shared();
    shared->space = -1; shared->capacity = 0x400000u; shared->port = 7341;
    // [U] bootloader start times: A's comes first after a REBOOT's 100 ms stagger (REBOOTs
    // work on the device) and outlasts B's 250 ms keepalive interval (154: B lost the link).
    shared->a_boot_ms = 300; shared->b_boot_ms = 400; shared->a_redeliver = 1;
    // a REBOOT's A-then-B stagger (100 ms on the device, plus host scheduling here) must fall
    // inside the window, every lone restart (B notices a lost A after 1 s or more) outside it. The device
    // boundary lies between 0.15 s and 1.4 s [U]; 400 ms sits in that range.
    shared->boot_a_window_ms = 400;
    board_start_us = now_us();
    int control_port = 0;
    bool play = true, otp = false;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        const char* v = i + 1 < argc ? argv[i + 1] : 0;
        if (a == "--plain") { play = false; continue; }
        if (a == "--otp-stdin") { otp = true; continue; }
        if (a == "--virtual") { shared->virtual_clock = 1; continue; }   // sim/clock.cpp (test runs)
        if (!v) return usage();
        if (a == "--root") snprintf(shared->root, sizeof(shared->root), "%s", v);
        else if (a == "--bootpart") snprintf(shared->bootpart, sizeof(shared->bootpart), "%s", v);
        else if (a == "--stock") stock_path = v;
        else if (a == "--port") shared->port = atoi(v);
        else if (a == "--control") control_port = atoi(v);
        else if (a == "--capacity") shared->capacity = uint32_t(strtoul(v, 0, 0));
        else return usage();
        ++i;
    }
    if (!shared->root[0] || !shared->bootpart[0] || !stock_path) return usage();
    if (!control_port) control_port = shared->port + 1;
    if (otp && !load_otp()) { wipe_otp(); fprintf(stderr, "xyf_sim: bad OTP records on stdin\n"); return 2; }
    signal(SIGPIPE, SIG_IGN);
    srand(unsigned(getpid()));
    for (int k = 0; k != 2; ++k) sdram_fds[k] = make_sdram();
    for (auto& w : shared->wake) {
        if (pipe(w)) crash("pipe: %s", strerror(errno));
        for (int end : w) fcntl(end, F_SETFL, O_NONBLOCK);
    }
    control_fd = listen_on(control_port);
    log("USB CDC endpoint 127.0.0.1:%d, control 127.0.0.1:%d, storage %s", shared->port, control_port, shared->root);
    power_cycle(play, "start");
    bool quit = false;
    const pid_t parent = getppid();
    while (!quit) {
        if (getppid() != parent) { log("parent gone: stopping"); break; }   // never outlive the tool that started it
        std::vector<pollfd> fds;
        fds.push_back({control_fd, POLLIN, 0});
        for (int c : clients) fds.push_back({c, POLLIN, 0});
        int link_index[2] = {-1, -1};
        for (int k = 0; k != 2; ++k)
            if (procs[k].fd >= 0) { link_index[k] = int(fds.size()); fds.push_back({procs[k].fd, POLLIN, 0}); }
        fds.push_back({shared->wake[2][0], POLLIN, 0});   // a processor went idle (sim/clock.cpp)
        // The board's next event (a delayed message to A, a restart): virtual, the steps stop there.
        uint64_t next = to_a.empty() ? 0 : to_a.front().at;
        for (const Proc& p : procs) if (p.restart_at && (!next || p.restart_at < next)) next = p.restart_at;
        const bool running[2] = {procs[0].pid > 0 && procs[0].state == "running", procs[1].pid > 0 && procs[1].state == "running"};
        int wait_ms = board_clock(running, next * 1000u);
        if (!virtual_clock() && next) {
            const uint64_t now = board_us();
            if (next - now < uint64_t(wait_ms) * 1000u || next <= now) wait_ms = next > now ? int((next - now) / 1000u) + 1 : 0;
        }
        ::poll(fds.data(), fds.size(), wait_ms);
        reap();
        for (int k = 0; k != 2; ++k)
            if (link_index[k] >= 0 && procs[k].fd >= 0 && (fds[link_index[k]].revents & (POLLIN | POLLHUP))) drain(k);
        while (!to_a.empty() && to_a.front().at <= board_us()) {
            forward(0, to_a.front().m, to_a.front().body);
            to_a.pop_front();
        }
        if (cut_pending) {
            cut_pending = 0; ++shared->cuts;
            power_cycle(true, "power cut");
            continue;
        }
        for (int k = 0; k != 2; ++k)
            if (procs[k].restart_at && procs[k].restart_at <= board_us()) start(k, false);
        if (fds[0].revents & POLLIN) {
            const int c = accept(control_fd, 0, 0);
            if (c >= 0) { clients.push_back(c); client_in.emplace_back(); }
        }
        for (size_t i = 0; i < clients.size();) {
            char buffer[512];
            const ssize_t n = (fds.size() > i + 1 && fds[i + 1].fd == clients[i] && (fds[i + 1].revents & (POLLIN | POLLHUP)))
                              ? read(clients[i], buffer, sizeof(buffer)) : -2;
            if (n == 0 || (n < 0 && n != -2 && errno != EAGAIN)) {
                board_forget(clients[i]);
                close(clients[i]); clients.erase(clients.begin() + long(i)); client_in.erase(client_in.begin() + long(i));
                continue;
            }
            if (n > 0) client_in[i].append(buffer, size_t(n));
            size_t eol;
            while ((eol = client_in[i].find('\n')) != std::string::npos) {
                const std::string line = client_in[i].substr(0, eol), reply = command(line, quit, clients[i]);
                client_in[i].erase(0, eol + 1);
                if (line.compare(0, 6, "status") && line.compare(0, 8, "injected")) { kick(0); kick(1); }   // a fault, a key
                if (!reply.empty() && write(clients[i], (reply + "\n").data(), reply.size() + 1) < 0) break;
            }
            ++i;
        }
    }
    for (int k = 0; k != 2; ++k) stop(k);
    wipe_otp();
    log("stopped");
    return 0;
}

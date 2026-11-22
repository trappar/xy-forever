// The device clock: both processors' cycle counters and the board's clock (reset delays, bootloader
// start times, delayed link messages, fault delays). Device time is Shared::clock_ns, and only the simulator
// moves it: the host's clock never does, so a run is limited only by the host's compute, never paced to it.
//  - The board steps it only while both processors are idle: no link message in flight either way, and each
//    main thread either waits in idle_pause() (B's stock request, a bootloader's wait, the menu loop's pause,
//    a fault's delay) or has read the clock idle_reads times running with nothing done in between (the
//    device's polling loops).
//  - Even then, only while something is due: a control "advance MS" not yet answered (answered once the
//    clock is there and both processors are idle again), a board event (a reset delay, a delayed link
//    message), B waiting on device time away from rest (a delay, a time-out, a slice: anywhere but its menu
//    loop's pause or a hang) or a host request B read and has not answered (not in a hang), unless a host
//    that does not read holds B's reply up, for at most quiet_limit with nothing done; or work done since the
//    last step (a link message, USB data, a control command, a menu pass that polled the clock), which with
//    B at rest counts only for rest_limit past the last news from outside (USB data or a connection, a
//    control command other than status, injected, settle and advance): a device that polls on its own
//    comes to rest. A processor's bounded wait does not run the clock by itself. Otherwise device time
//    stands still and every process blocks, with no CPU, until the host or a control command brings
//    something: an idle device's own timers (keepalives, a deferred save, an idle window's close, a key's
//    repeat) wait for an advance, which is how a host's pause reaches the device (xy_forever/transport.py
//    sim_pause, Simulator.advance; Simulator.settle runs it until nothing is done).
//  - A step goes no further than the next known event (a processor's bounded wait's end too); it is 1 ms after
//    any activity and doubles while nothing happens, up to max_step (the device's own timers are not known
//    events, but for a held key's next repeat: they fire within max_step).
//  - It also moves by a relayed link message's transfer time (link_ns + link_byte_ns per byte) and, while a
//    processor is busy (just after activity), by read_ns per clock read, so work takes device time.
// A processor that is busy (computing, or waiting for the other) stops the clock: its slowness on a busy
// host never reaches the device's time-outs, and a test's outcome does not depend on host load.
#include "runtime.hpp"
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <stdio.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>
#include <vector>

namespace xyf_sim {
namespace {
const uint32_t idle_reads = 32;             // clock reads with nothing done between them: a polling loop, idle
const uint64_t read_ns = 1000, link_ns = 20000, link_byte_ns = 200;   // ~5 MB/s link (docs/hardware/devices.md)
const uint64_t min_step = 1000000, quiet_limit = 60000000000ull;   // 60 s of device time with nothing done: hung
const uint64_t max_step = 10000000;         // a step's most: the device's own timers fire at most 10 ms late
const uint64_t rest_limit = 1000000000ull;  // work at rest runs the clock 1 s past the last news from outside
uint64_t start_ns = 0;                       // this processor's clock at its first read (its counter's zero)
bool started = false;
uint32_t seen_work = 0, seen_steps = 0, spin = 0;
bool charging = false;
int usb_fd = -1, usb_bytes = 0;
uint32_t usb_conn = 0;
bool usb_ready = false, next_ready = false;    // next_ready: a new host's connection seen on the listener
uint32_t usb_news = 0;                      // usb_check()s that found news (cdc_wait: one seen inside block())

void wake(int which) { const char b = 1; if (write(shared->wake[which][1], &b, 1) < 0) {} }
void drain_wake(int which) { char b[64]; while (read(shared->wake[which][0], b, sizeof(b)) > 0) {} }
bool main_thread() {
    static pthread_t main = pthread_self();          // the first caller: the processor's main thread
    return pthread_equal(main, pthread_self());
}
bool in_flight() {
    for (int k = 0; k != 2; ++k) if (shared->inflight[k].load() || shared->outflight[k].load()) return true;
    return false;
}
// The board steps only with no message in flight; while one is, whoever takes it last wakes the board
// (its own loop for outflight, link_taken() for inflight), so an idle processor need not.
bool menu_pause = false;                    // B: inside cdc_wait (at rest there: its menu loop; elsewhere: a hang)
void publish(uint32_t work, uint32_t steps, uint64_t until, bool rest) {
    // A idle is at rest (its loop); B only where it says so: 1 its menu loop's pause, 2 a hang (it never answers).
    shared->idle_rest[role] = role == 0 ? 1 : !rest ? 0 : menu_pause ? 1 : 2;
    shared->idle_until[role] = until; shared->idle_steps[role] = steps; shared->idle_work[role] = work;
    if (!in_flight()) wake(2);
}
// B: USB bytes or a connection that arrived since the main thread last looked count as activity, once
// (B may leave them unread for a while: that is no reason to stop the clock). next: a new host connects
// while another is attached (B takes it at its next look and drops the old one).
bool usb_check(bool readable, bool next = false) {
    const int fd = cdc_fd();
    int bytes = 0;
    if (fd < 0 || ioctl(fd, FIONREAD, &bytes) || bytes < 0) bytes = 0;
    bool news = false;
    const uint32_t conn = cdc_connection();
    if (fd != usb_fd || conn != usb_conn) { next_ready = false; usb_ready = false; usb_bytes = 0; usb_fd = fd; usb_conn = conn; }
    if (bytes != usb_bytes) { news = bytes > usb_bytes; usb_bytes = bytes; usb_ready = false; }
    if (readable && !usb_ready) { usb_ready = true; news = true; }
    if (next && !next_ready) { next_ready = true; news = true; }
    if (news) { ++shared->work[1]; ++shared->outside; ++usb_news; }
    return news;
}
// Until a step, work, new USB data or (B) room for a reply the host had not read yet: no time limit.
void block() {
    if (role == 0 && a_held.load()) wake(3);            // the slave may be open now: the link thread looks again
    pollfd p[4] = {{shared->wake[role][0], POLLIN, 0}, {-1, POLLIN, 0}, {-1, POLLOUT, 0}, {-1, POLLIN, 0}};
    if (role == 1) {
        if (usb_check(false)) return;
        if (usb_fd >= 0 && !usb_ready) p[1].fd = usb_fd;
        if (shared->usb_held.load()) p[2].fd = cdc_fd();
        if (!next_ready) p[3].fd = cdc_next_fd();
    }
    while (::poll(p, 4, -1) < 0 && errno == EINTR) {}
    if (p[0].revents) drain_wake(role);
    if (p[1].revents) usb_check(true);
    if (p[2].revents) ++shared->work[1];
    if (p[3].revents) usb_check(false, true);
}
uint64_t to_ns(uint64_t cycles) { return start_ns + (cycles - 400000000u) * 5 / 2; }
}

uint64_t now_us() {
    timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return uint64_t(t.tv_sec) * 1000000u + uint64_t(t.tv_nsec) / 1000u;
}
uint64_t device_cycles() {
    const uint64_t ns = shared->clock_ns.load();
    if (!started) { start_ns = ns; started = true; }
    return (ns - start_ns) * 2 / 5 + 400000000u;                // 400 MHz; one second in, as after boot
}
void cycles(uint32_t& low, uint32_t& high) {
    host_poll();                                                // sim/boot.cpp
    if (shared && (role == 0 || role == 1)) ++shared->clock_reads[role];
    if (main_thread()) {
        const uint32_t work = shared->work[role].load(), steps = shared->steps.load();
        if (work != seen_work || steps != seen_steps) {
            charging = charging || work != seen_work;           // activity, not a step: busy reads cost time
            seen_work = work; seen_steps = steps; spin = 0;
        }
        if (spin < idle_reads) {
            if (++spin == idle_reads) { charging = false; publish(work, steps, 0, false); }
            else if (charging) shared->clock_ns += read_ns;
        } else block();
    }
    const uint64_t c = device_cycles();
    low = uint32_t(c); high = uint32_t(c >> 32);
}

void kick(int which) {                                         // the main thread and the link thread look again
    ++shared->work[which];
    wake(which); wake(3 + which);
}
void active() { ++shared->work[role]; }
void link_taken() {
    // The main thread already idle at the work this message brought: the board is waiting for this.
    if (--shared->inflight[role] == 0 && shared->idle_work[role].load() == shared->work[role].load() && !in_flight())
        wake(2);
}
void idle_pause(uint64_t bound, bool rest) {
    publish(shared->work[role].load(), shared->steps.load(), bound ? to_ns(bound) : 0, rest);
    block();
}
void cdc_wait(int ms, uint64_t event) {                         // B's menu loop pause, woken by USB data
    const int fd = cdc_fd();
    pollfd p[2] = {{fd, POLLIN, 0}, {cdc_next_fd(), POLLIN, 0}};
    // Not a bound for the board's steps: the loop's pause is no device event; event (a held key's next repeat,
    // device_cycles()) is one. B is at rest
    // here: the clock stands still unless something is due. A pass that polled the clock (a slice of a job, a
    // wait) counts as work, so the next pass comes 1 ms later.
    static uint32_t reads = 0;
    if (shared->clock_reads[1].load() - reads >= idle_reads) active();
    const uint32_t seen = usb_news;
    for (const uint64_t until = device_cycles() + uint64_t(ms) * 400000u; device_cycles() < until;) {
        if (fd >= 0 && ::poll(p, 2, 0) > 0 && usb_check(p[0].revents, p[1].revents)) break;   // new, not left unread
        menu_pause = true; idle_pause(event > device_cycles() ? event : 0, true); menu_pause = false;
        if (usb_news != seen) break;                                        // block() saw it arrive
    }
    reads = shared->clock_reads[1].load();
}

// ---- board ----------------------------------------------------------------------------------
namespace {
struct Advance { int client; uint64_t until; };
std::vector<Advance> advances;              // control "advance" lines not answered yet
std::vector<int> settles;                   // control "settle" lines not answered yet
uint64_t step_ns = min_step, quiet_ns = 0;   // quiet_ns: device time stepped since the last work
uint64_t outside_ns = 0;                    // clock_ns at the last news from outside (outside_any: there was one)
bool outside_any = false;
uint32_t stepped_work = 0, seen_outside = 0;
bool hung = false;
bool quiescent(const bool running[2]) {
    for (int k = 0; k != 2; ++k) if (shared->inflight[k] || shared->outflight[k]) return false;
    const uint32_t steps = shared->steps.load();
    for (int k = 0; k != 2; ++k) {
        if (!running[k]) continue;
        if (shared->idle_work[k].load() != shared->work[k].load()) return false;
        if (shared->idle_steps[k].load() != steps) { wake(k); return false; }   // not yet seen the last step
    }
    return true;
}
}
void board_link_cost(uint32_t bytes) { shared->clock_ns += link_ns + link_byte_ns * bytes; }
void board_reset_flight(int which) {
    shared->inflight[which] = 0; shared->outflight[which] = 0;
    if (which == 1) { shared->usb_held = 0; shared->usb_asked = 0; }   // B's held reply went with it, and the request
}
void board_jump(int64_t ms) {
    shared->clock_ns += uint64_t(ms) * 1000000u;
    ++shared->steps; wake(0); wake(1);
}
void board_advance(int client, int64_t ms) {
    advances.push_back({client, shared->clock_ns.load() + uint64_t(ms) * 1000000u});
}
void board_settle(int client) { settles.push_back(client); }
void board_forget(int client) {
    for (size_t i = 0; i < advances.size();) if (advances[i].client == client) advances.erase(advances.begin() + long(i)); else ++i;
    for (size_t i = 0; i < settles.size();) if (settles[i] == client) settles.erase(settles.begin() + long(i)); else ++i;
}
int board_clock(const bool running[2], uint64_t deadline_ns, bool woken) {
    if (woken) drain_wake(2);
    if (!quiescent(running)) return -1;                         // a processor wakes the board when idle
    const uint64_t now = shared->clock_ns.load();
    uint64_t due = 0;                                           // the nearest time something is due
    for (size_t i = 0; i < advances.size();) {
        if (advances[i].until <= now) {
            if (write(advances[i].client, "{\"ok\": true}\n", 13) < 0) {}
            advances.erase(advances.begin() + long(i));
        } else { if (!due || advances[i].until < due) due = advances[i].until; ++i; }
    }
    if (deadline_ns > now && (!due || deadline_ns < due)) due = deadline_ns;
    uint64_t bound = due;                                       // a step goes no further: also a processor's wait's end
    for (int k = 0; k != 2; ++k) {
        const uint64_t until = running[k] ? shared->idle_until[k].load() : 0;
        if (until > now && (!bound || until < bound)) bound = until;
    }
    const uint32_t outside = shared->outside.load();
    if (outside != seen_outside) { seen_outside = outside; outside_ns = now; outside_any = true; }
    const uint32_t work = shared->work[0].load() + shared->work[1].load();
    const bool worked = work != stepped_work;
    if (worked) { quiet_ns = 0; hung = false; }
    // Waited on: B waiting on device time away from rest (a delay, a time-out, a slice), or a host request B has
    // not answered yet (not in a hang: nothing answers it), unless a host that does not read holds its reply up. Up to quiet_limit with nothing done: then nothing will be (a hung
    // processor), and the clock stands still until something comes.
    const int32_t rest = shared->idle_rest[1].load();
    const bool waited = running[1] && !shared->usb_held.load() && (!rest || (rest == 1 && shared->usb_asked.load()));
    if (waited && !worked && quiet_ns >= quiet_limit && !hung) {
        hung = true;
        log("board: %llu s of device time with nothing done while B waits: the clock stands still",
            (unsigned long long)(quiet_limit / 1000000000u));
    }
    // Work with B at rest (its menu loop's passes, a job's slices) runs the clock only up to rest_limit past the
    // last news from outside: a device that polls on its own (a pass that asks A something every time) would
    // otherwise never come to rest. A host that waits for more moves the clock on (advance: its pause).
    const bool working = worked && (waited || (outside_any && now - outside_ns < rest_limit));
    if (!due && !working && (!waited || hung)) {                // nothing is due: the clock stands still
        for (int client : settles) if (write(client, "{\"ok\": true}\n", 13) < 0) {}
        settles.clear();
        return -1;
    }
    uint64_t step = worked ? min_step : step_ns;
    if (bound && bound - now < step) step = bound - now;
    step_ns = worked ? 2 * min_step : (step_ns * 2 < max_step ? step_ns * 2 : max_step);
    quiet_ns += step;
    stepped_work = work;
    shared->clock_ns += step;
    ++shared->steps;
    wake(0); wake(1);
    return 0;
}
}

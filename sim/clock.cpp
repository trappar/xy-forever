// The device clock: both processors' cycle counters and the board's clock (reset delays, bootloader
// start times, delayed link messages, fault delays).
//
// Real time (interactive runs: python3 -m xy_forever.sim, the web app, conformance --sim): the host's
// monotonic clock plus control "advance"/"jump".
//
// Virtual (--virtual: test runs): device time is Shared::clock_ns, and only the simulator moves it. It moves
//  - by the board's steps, taken only while both processors are idle: no link message in flight either
//    way, and each main thread either waits in idle_pause() (B's stock request, a bootloader's wait, the
//    menu loop's pause, a fault's delay) or has read the clock idle_reads times running with nothing done
//    in between (the device's polling loops). A step goes no further than the next event the simulator
//    knows of (a bounded wait's end, a reset delay, a delayed link message) and at most max_step; it is
//    1 ms after any activity and doubles while nothing happens. Steps keep pace with host time (an idle
//    device's time passes as on the OP-XY, so a host's own pauses mean the same): they spend host time
//    that has passed (busy or idle, at most max_lag of it), unless a test fast-forwards (control
//    "advance MS": answered once the clock is there and both processors are idle again);
//  - by a relayed link message's transfer time (link_ns + link_byte_ns per byte) and, while a processor is
//    busy (just after activity), read_ns per clock read, so work takes device time however loaded the host.
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
const uint64_t min_step = 1000000, max_step = 8000000, max_lag = 1000000000;
uint64_t start_ns = 0;                       // this processor's clock at its first read (its counter's zero)
bool started = false;
uint32_t seen_work = 0, seen_steps = 0, spin = 0;
bool charging = false;
int usb_fd = -1, usb_bytes = 0;
bool usb_ready = false;
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
void publish(uint32_t work, uint32_t steps, uint64_t until) {
    shared->idle_until[role] = until; shared->idle_steps[role] = steps; shared->idle_work[role] = work;
    if (!in_flight()) wake(2);
}
// B: USB bytes or a connection that arrived since the main thread last looked count as activity, once
// (B may leave them unread for a while: that is no reason to stop the clock).
bool usb_check(bool readable) {
    const int fd = cdc_fd();
    int bytes = 0;
    if (fd < 0 || ioctl(fd, FIONREAD, &bytes) || bytes < 0) bytes = 0;
    bool news = false;
    if (fd != usb_fd || bytes != usb_bytes) { news = bytes > usb_bytes; usb_fd = fd; usb_bytes = bytes; usb_ready = false; }
    if (readable && !usb_ready) { usb_ready = true; news = true; }
    if (news) { ++shared->work[1]; ++usb_news; }
    return news;
}
// Until a step, work, new USB data or 2 ms (a flag another process set is seen then).
void block() {
    pollfd p[2] = {{shared->wake[role][0], POLLIN, 0}, {-1, POLLIN, 0}};
    nfds_t n = 1;
    if (role == 1) {
        if (usb_check(false)) return;
        if (usb_fd >= 0 && !usb_ready) { p[1].fd = usb_fd; n = 2; }
    }
    ::poll(p, n, 2);
    if (p[0].revents) drain_wake(role);
    if (n == 2 && p[1].revents) usb_check(true);
}
uint64_t to_ns(uint64_t cycles) { return start_ns + (cycles - 400000000u) * 5 / 2; }
}

bool virtual_clock() { return shared && shared->virtual_clock.load(std::memory_order_relaxed); }

uint64_t now_us() {
    timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return uint64_t(t.tv_sec) * 1000000u + uint64_t(t.tv_nsec) / 1000u;
}
uint64_t device_cycles() {
    uint64_t ns;
    if (virtual_clock()) ns = shared->clock_ns.load();
    else {
        timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
        ns = uint64_t(t.tv_sec) * 1000000000u + uint64_t(t.tv_nsec) +
             uint64_t(shared ? shared->clock_skip_us.load() : 0) * 1000u;          // control "advance MS"
    }
    if (!started) { start_ns = ns; started = true; }
    return (ns - start_ns) * 2 / 5 + 400000000u;                // 400 MHz; one second in, as after boot
}
void cycles(uint32_t& low, uint32_t& high) {
    host_poll();                                                // sim/boot.cpp
    if (shared && (role == 0 || role == 1)) ++shared->clock_reads[role];
    if (!virtual_clock()) {
        // A processor polling the clock in a loop (A's link loop, B's waits) would hold a host core at
        // 100% and, with many simulators at once, starve the others. A short sleep every 64 reads.
        static uint32_t reads = 0;
        if (!(++reads & 63u)) usleep(20);
    } else if (main_thread()) {
        const uint32_t work = shared->work[role].load(), steps = shared->steps.load();
        if (work != seen_work || steps != seen_steps) {
            charging = charging || work != seen_work;           // activity, not a step: busy reads cost time
            seen_work = work; seen_steps = steps; spin = 0;
        }
        if (spin < idle_reads) {
            if (++spin == idle_reads) { charging = false; publish(work, steps, 0); }
            else if (charging) shared->clock_ns += read_ns;
        } else block();
    }
    const uint64_t c = device_cycles();
    low = uint32_t(c); high = uint32_t(c >> 32);
}

void kick(int which) {
    if (!virtual_clock()) return;
    ++shared->work[which];
    wake(which);
}
void active() { if (virtual_clock()) ++shared->work[role]; }
void link_taken() {
    // The main thread already idle at the work this message brought: the board is waiting for this.
    if (--shared->inflight[role] == 0 && virtual_clock() && shared->idle_work[role].load() == shared->work[role].load() &&
        !in_flight())
        wake(2);
}
void idle_pause(uint64_t bound, uint32_t real_us) {
    if (!virtual_clock()) { usleep(real_us); return; }
    publish(shared->work[role].load(), shared->steps.load(), bound ? to_ns(bound) : 0);
    block();
}
void cdc_wait(int ms) {                                         // B's menu loop pause, woken by USB data
    const int fd = cdc_fd();
    pollfd p = {fd, POLLIN, 0};
    if (!virtual_clock()) { if (fd < 0) usleep(ms * 1000); else ::poll(&p, 1, ms); return; }
    // Not a bound for the board's steps: the loop's pause is no device event (key_ui has its own).
    const uint32_t seen = usb_news;
    for (const uint64_t until = device_cycles() + uint64_t(ms) * 400000u; device_cycles() < until;) {
        if (fd >= 0 && ::poll(&p, 1, 0) > 0 && usb_check(true)) return;     // new data, not data B left unread
        idle_pause(0, 0);
        if (usb_news != seen) return;                                       // block() saw it arrive
    }
}

// ---- board ----------------------------------------------------------------------------------
namespace {
struct Advance { int client; uint64_t until; };
std::vector<Advance> advances;              // control "advance" lines not answered yet
uint64_t host_seen = 0, lag_ns = 0, step_ns = min_step;    // lag_ns: host time the paced steps may still spend
uint32_t stepped_work = 0;
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
void board_link_cost(uint32_t bytes) { if (virtual_clock()) shared->clock_ns += link_ns + link_byte_ns * bytes; }
void board_reset_flight(int which) { shared->inflight[which] = 0; shared->outflight[which] = 0; }
void board_jump(int64_t ms) {
    if (!virtual_clock()) { shared->clock_skip_us += ms * 1000; return; }
    shared->clock_ns += uint64_t(ms) * 1000000u;
    ++shared->steps; wake(0); wake(1);
}
void board_advance(int client, int64_t ms) {
    if (!virtual_clock()) {                                     // real time: one jump
        shared->clock_skip_us += ms * 1000;
        if (write(client, "{\"ok\": true}\n", 13) < 0) {}
        return;
    }
    advances.push_back({client, shared->clock_ns.load() + uint64_t(ms) * 1000000u});
}
void board_forget(int client) {
    for (size_t i = 0; i < advances.size();) if (advances[i].client == client) advances.erase(advances.begin() + long(i)); else ++i;
}
int board_clock(const bool running[2], uint64_t deadline_ns, bool woken) {
    if (woken) drain_wake(2);
    if (!virtual_clock()) return 20;
    const uint64_t host = now_us();
    if (host_seen) lag_ns = lag_ns + (host - host_seen) * 1000u > max_lag ? max_lag : lag_ns + (host - host_seen) * 1000u;
    host_seen = host;
    if (!quiescent(running)) return 20;                         // a processor wakes the board when idle
    const uint64_t now = shared->clock_ns.load();
    uint64_t forward = 0;                                       // the furthest advance still to reach
    for (size_t i = 0; i < advances.size();) {
        if (advances[i].until <= now) {
            if (write(advances[i].client, "{\"ok\": true}\n", 13) < 0) {}
            advances.erase(advances.begin() + long(i));
        } else { if (advances[i].until > forward) forward = advances[i].until; ++i; }
    }
    const uint32_t work = shared->work[0].load() + shared->work[1].load();
    uint64_t step = work != stepped_work ? min_step : step_ns;
    if (forward && forward - now < step) step = forward - now;
    if (deadline_ns > now && deadline_ns - now < step) step = deadline_ns - now;
    for (int k = 0; k != 2; ++k) {
        const uint64_t until = running[k] ? shared->idle_until[k].load() : 0;
        if (until > now && until - now < step) step = until - now;
    }
    if (!forward) {                                             // keep pace with host time
        if (lag_ns < step) return int((step - lag_ns) / 1000000u) + 1;
        lag_ns -= step;
    }
    step_ns = work != stepped_work ? 2 * min_step : (step_ns * 2 > max_step ? max_step : step_ns * 2);
    stepped_work = work;
    shared->clock_ns += step;
    ++shared->steps;
    wake(0); wake(1);
    return 0;
}
}

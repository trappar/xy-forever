// Host test: the MTP function driver (mtp_usb.*) against a thin model of the stock USB stack
//: one driver slot, start/stop, descriptors built from the driver tables, the stack's
// OUT urb queue (completion when full or on a short packet, a packet parks while no urb is active,
// recycle only appends, kick / kick_rx resume), IN urbs completing later through urb_sent, and the
// stop/deconfigure teardown that frees queued OUT urbs without a callback. 186-005: the
// serial service (CDC) in the slot before and after, stop(arg) closing GP timer 7 only for arg != 0 and a
// start with the timer closed never returning (candidate 1), progress marks, and freezes injected at
// every stock step (each in a forked child) leaving the marks file naming that step.
#include "mtp_usb.hpp"
#include "../native/mtp_responder.hpp"
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <algorithm>
#include <deque>
#include <set>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <string>
#include <vector>
using namespace xyf::mtp_usb;
static unsigned checks;
static void require(bool ok, int line) {
    ++checks;
    if (!ok) {
        fprintf(stderr, "check %u (line %d) failed\n", checks, line);
        abort();
    }
}
#define REQUIRE(x) require((x), __LINE__)

namespace stack {
Driver* slot;
bool started;
// The start flags word ([0x88a137a4]), GP timer 7, and every stop / start argument the driver passed.
uint32_t flags_word;
bool timer_open = true;
std::vector<uint32_t> stops, starts;
// Freeze injection: the n-th call of one stock step never returns (a Freeze is thrown out of it).
struct Freeze {};
enum Op : uint32_t { op_none, op_stop, op_register, op_start, op_alloc, op_submit, op_unregister, op_ops };
uint32_t freeze_op, freeze_nth, calls[op_ops];
void step(Op op) {
    if (++calls[op] == freeze_nth && op == freeze_op) throw Freeze();
}
Driver other = {"other", 0, 0, 0, 0, 0, 0, 0};
void boot() {                          // a boot before USB: empty slot, nothing started
    slot = 0;
    started = false;
    flags_word = 0;
    timer_open = true;
    stops.clear();
    starts.clear();
    for (uint32_t& c : calls) c = 0;
}
int masked;
// One OUT endpoint: its active urb (ep.active), its queue, a packet parked while no urb is active.
struct OutEp {
    Endpoint ep;
    std::deque<Urb*> queue;
    std::vector<uint8_t> parked;
    bool has_parked;
};
OutEp mtp_out = {{kBulkOut, 0, 0, 0, 0, kMaxPacket, 0}, {}, {}, false};
OutEp ser_out = {{kSerialOut, 0, 0, 0, 0, kMaxPacket, 0}, {}, {}, false};
Endpoint ep_in = {kBulkIn, 0, 0, 0, 0, kMaxPacket, 0}, ep_ser_in = {kSerialIn, 0, 0, 0, 0, kMaxPacket, 0};
std::deque<Urb*> in_pending;
std::vector<uint8_t> host_in, host_serial;   // bytes the host has read from bulk IN (MTP, serial)
uint32_t serial_zlps, serial_in_urbs;
// With check_containers, every MTP IN urb starts at a container's start or continues one; only a container's
// last urb may be short, and it carries a ZLP exactly when it is a multiple of 512.
bool check_containers;
uint32_t mtp_left, mtp_zlps, mtp_urbs;
std::set<Urb*> live;
uint32_t parks, recycles;
const uintptr_t kDev = 0x1234;
Urb* active(OutEp& o) { return reinterpret_cast<Urb*>(o.ep.active); }
Urb* active() { return active(mtp_out); }
OutEp* out_of(uintptr_t ep) {
    return ep == uintptr_t(&mtp_out.ep) ? &mtp_out : ep == uintptr_t(&ser_out.ep) ? &ser_out : 0;
}
// The stock heap (88a2ec6c..) has no lock and never masks; the USB interrupt mallocs (event 6). Every
// heap call the driver makes from the main loop "fires the interrupt" inside the call when unmasked:
// the interrupt's own malloc then finds the heap busy and the test fails.
bool heap_busy, fire_in_heap = true;
uint32_t heap_calls;
void isr_malloc() { REQUIRE(!heap_busy); }
void heap_call() {
    REQUIRE(!heap_busy);
    heap_busy = true;
    ++heap_calls;
    if (!masked && fire_in_heap) isr_malloc();
    heap_busy = false;
}
void release(Urb* u) {
    REQUIRE(live.erase(u) == 1);
    delete[] u->buf;
    delete u;
}
void drop_out(OutEp& o) {              // 88a095a8: queued and active OUT urbs freed, no callback
    if (active(o)) release(active(o));
    o.ep.active = 0;
    for (Urb* u : o.queue) release(u);
    o.queue.clear();
    o.has_parked = false;
}
uint32_t stack_urbs; // the stack's own one-packet OUT urb per OUT endpoint, armed at every configure
void flush() { // 88a08ab4 -> 88a095a8 per endpoint: OUT queued+active
    drop_out(mtp_out);
    drop_out(ser_out); // freed without a callback; IN queued+active completed (status 2)
    while (!in_pending.empty()) {
        Urb* u = in_pending.front();
        in_pending.pop_front();
        if (slot->ops->urb_sent(u)) release(u);
    }
}
void isr_event(uint32_t code) {
    REQUIRE(!masked);
    if (code == 0xa || code == 7) flush(); // deconfigure / suspend / disconnect; SET_INTERFACE
    if (code == 6) { // 88a08b16 -> 88a09612: flush, then arm one 512-byte stack urb
        flush();
        for (OutEp* o : {&mtp_out, &ser_out}) {
            Urb* u = new Urb();
            u->buf = new uint8_t[kMaxPacket]();
            u->size = kMaxPacket;
            u->dev = kDev;
            u->ep = uintptr_t(&o->ep);
            live.insert(u);
            ++stack_urbs;
            if (!o->ep.active) o->ep.active = uintptr_t(u);
            else o->queue.push_back(u);
        }
    }
    // code 4 (bus reset, 88a08cbe): no flush; queued urbs stay with the stack until the next configure.
    slot->ops->event(kDev, code);
}
void complete_out(OutEp& o) {
    Urb* u = active(o);
    o.ep.active = 0;
    if (!o.queue.empty()) {
        o.ep.active = uintptr_t(o.queue.front());
        o.queue.pop_front();
    }
    if (slot->ops->recv_urb(u)) { // nonzero: the stack re-queues it (as the stock CDC's receive)
        u->done = 0;
        if (!o.ep.active) o.ep.active = uintptr_t(u);
        else o.queue.push_back(u);
    }
}
bool deliver(OutEp& o, const uint8_t* p, uint32_t n) { // one packet into the active urb (88a0ba08); false: parked
    Urb* u = active(o);
    if (!u) {
        o.parked.assign(p, p + n);
        o.has_parked = true;
        ++parks;
        return false;
    }
    uint32_t take = u->size - u->done;
    if (take > n) take = n;
    memcpy(u->buf + u->done, p, take);
    u->done += take;
    if (u->done == u->size || n < kMaxPacket) complete_out(o);
    return true;
}
bool deliver(const uint8_t* p, uint32_t n) { return deliver(mtp_out, p, n); }
// Port
int reg(Driver* d) {
    step(op_register);
    REQUIRE(!masked);
    if (slot) return -1;
    slot = d;
    return 0;
}
void unreg(Driver* d) {
    step(op_unregister);
    if (slot == d) slot = 0;
}
void stop(uint32_t arg) { // 88a34fec
    stops.push_back(arg);
    step(op_stop);
    if (started) { // teardown only while the init flag is set
        started = false;
        flush(); // event 0xf -> 88a08ab4
        for (uint32_t c : {4u, 0xdu, 0xeu, 0xfu}) slot->ops->event(kDev, c);
    }
    if (arg) timer_open = false; // 88a087b4 -> 88a0890e: GP timer 7 closed
}
void start(uint32_t flags) { // 88a35044: its own stop(0) finds nothing (never called while running)
    starts.push_back(flags);
    REQUIRE(!started && flags == kCompositeFlags);
    flags_word = flags;
    if (!timer_open) throw Freeze(); // a re-open does not tick: the 5-tick wait never ends
    step(op_start); // the controller init's 5-tick wait (88a0bfec)
    REQUIRE(slot); // bit 9: no class registers; ours must be in the slot
    started = true;
    isr_event(1);
    isr_event(2);
}
Urb* alloc(uintptr_t dev, uint32_t ep, uint32_t len) {
    step(op_alloc);
    REQUIRE(dev == kDev && started);
    heap_call();
    Urb* u = new Urb();
    u->buf = new uint8_t[len]();
    u->size = len;
    u->dev = dev;
    u->ep = ep == kBulkOut   ? uintptr_t(&mtp_out.ep) :
            ep == kSerialOut ? uintptr_t(&ser_out.ep) :
            ep == kSerialIn  ? uintptr_t(&ep_ser_in) :
                               uintptr_t(&ep_in);
    REQUIRE(ep == kBulkOut || ep == kSerialOut || ep == kSerialIn || ep == kBulkIn);
    live.insert(u);
    return u;
}
int submit_in(Urb* u) {
    step(op_submit);
    REQUIRE(masked && (u->ep == uintptr_t(&ep_in) || u->ep == uintptr_t(&ep_ser_in)) && u->wanted <= u->size);
    in_pending.push_back(u);
    return 0;
}
void recycle(Urb* u) {
    REQUIRE(masked && live.count(u));
    OutEp* o = out_of(u->ep);
    REQUIRE(o && u != active(*o));
    for (Urb* q : o->queue) REQUIRE(q != u);
    u->done = 0;
    u->status = 3;
    o->queue.push_back(u);
    ++recycles;
}
void free_urb(Urb* u) {
    if (OutEp* o = out_of(u->ep)) {
        REQUIRE(u != active(*o));
        for (Urb* q : o->queue) REQUIRE(q != u);
    }
    heap_call();
    release(u);
}
void kick(Endpoint* e) {
    OutEp* o = out_of(uintptr_t(e));
    REQUIRE(XYF_MTP_USB_KICK && masked && o);
    if (!active(*o) && !o->queue.empty()) {
        o->ep.active = uintptr_t(o->queue.front());
        o->queue.pop_front();
    }
}
OutEp* out_number(uint32_t n) { return n == (kBulkOut & 0xf) ? &mtp_out : n == (kSerialOut & 0xf) ? &ser_out : 0; }
void kick_rx(uint32_t n) {
    OutEp* o = out_number(n);
    REQUIRE(masked && o && o->has_parked);
    o->has_parked = false;
    std::vector<uint8_t> p = o->parked;
    deliver(*o, p.data(), uint32_t(p.size()));
}
bool rx_parked(uint32_t n) {
    OutEp* o = out_number(n);
    return o && o->has_parked;
}
uint32_t mask() { return uint32_t(masked++); }
void unmask(uint32_t old) {
    --masked;
    REQUIRE(uint32_t(masked) == old);
}
const Port port = {reg,      unreg, start,   stop,      alloc, submit_in, recycle,
                   free_urb, kick,  kick_rx, rx_parked, mask,  unmask};
void service_in() { // the controller finishes one IN urb (interrupt)
    if (in_pending.empty()) return;
    Urb* u = in_pending.front();
    in_pending.pop_front();
    const bool serial = u->ep == uintptr_t(&ep_ser_in);
    std::vector<uint8_t>& to = serial ? host_serial : host_in;
    to.insert(to.end(), u->buf, u->buf + u->wanted);
    if (serial) {
        ++serial_in_urbs;
        if (u->zlp) ++serial_zlps;
        REQUIRE(bool(u->zlp) == (u->wanted % kMaxPacket == 0));
    } else if (check_containers) {
        ++mtp_urbs;
        if (!mtp_left) {
            REQUIRE(u->wanted >= 12);
            mtp_left = u->buf[0] | u->buf[1] << 8 | u->buf[2] << 16 | uint32_t(u->buf[3]) << 24;
        }
        REQUIRE(u->wanted <= mtp_left);
        mtp_left -= u->wanted;
        REQUIRE(mtp_left == 0 || u->wanted % kMaxPacket == 0);
        if (!mtp_left) {
            REQUIRE(bool(u->zlp) == (u->wanted % kMaxPacket == 0));
            mtp_zlps += u->zlp ? 1 : 0;
        } else REQUIRE(!u->zlp);
    }
    u->done = u->wanted;
    if (slot->ops->urb_sent(u)) release(u);
}
}

// Enumeration: the descriptors the stack builds from the driver tables.
static void enumerate() {
    const Driver* d = stack::slot;
    REQUIRE(d && d->n_configs == 1 && d->devinfo->cls == 0xef && d->devinfo->subclass == 2 &&
            d->devinfo->protocol == 1);
    const ConfigDesc& c = d->configs[0];
    REQUIRE(c.n_ifaces == 3 && c.n_iads == 1 && c.n_iads <= 4); // 88a0aa0e: at most 4
    REQUIRE(c.iads[0].first == 0 && c.iads[0].count == 2 && c.iads[0].cls == 2 && c.iads[0].subclass == 2 &&
            c.iads[0].protocol == 1 && c.iads[0].name);
    const IfaceDesc& comm = c.ifaces[0];
    REQUIRE(comm.cls == 2 && comm.subclass == 2 && comm.protocol == 1 && comm.n_alts == 1);
    const AltDesc& ca = comm.alts[0];
    REQUIRE(ca.n_eps == 1 && ca.eps[0].address == kNotifyIn && ca.eps[0].attributes == 3 && ca.eps[0].dir == 0x80);
    REQUIRE(ca.n_extra == 4);
    const CdcExtra* x = static_cast<const CdcExtra*>(ca.extra);
    const uint8_t subtypes[4] = {0, 6, 1, 2};
    for (uint32_t i = 0; i != 4; ++i) REQUIRE(x[i].type == 0x24 && x[i].subtype == subtypes[i]);
    REQUIRE(x[1].field[0] == 0 && x[1].field[1] == 1 && x[2].field[1] == 1 && x[3].field[0] == 2); // union, call, ACM
    const IfaceDesc& data = c.ifaces[1];
    REQUIRE(data.cls == 0x0a && data.n_alts == 1 && data.alts[0].n_extra == 0 && data.alts[0].n_eps == 2);
    REQUIRE(data.alts[0].eps[0].address == kSerialOut && data.alts[0].eps[0].dir == 0 &&
            data.alts[0].eps[0].attributes == 2);
    REQUIRE(data.alts[0].eps[1].address == kSerialIn && data.alts[0].eps[1].dir == 0x80 &&
            data.alts[0].eps[1].max_packet == 512);
    const IfaceDesc& i = c.ifaces[2];
    REQUIRE(i.cls == 6 && i.subclass == 1 && i.protocol == 1 && i.n_alts == 1);
    const AltDesc& a = i.alts[0];
    REQUIRE(a.n_eps == 3 && a.eps[0].address == kBulkIn && a.eps[1].address == kBulkOut && a.eps[2].address == kIntIn);
    REQUIRE(a.eps[0].attributes == 2 && a.eps[1].attributes == 2 && a.eps[2].attributes == 3);
    REQUIRE(a.eps[0].max_packet == 512 && a.eps[1].max_packet == 512);
    REQUIRE(a.eps[0].dir == 0x80 && a.eps[1].dir == 0 && a.eps[2].dir == 0x80); //
    // One FIFO per endpoint number (88a0c202): no number twice across the interfaces.
    std::set<uint32_t> numbers;
    uint32_t n_eps = 0;
    for (uint32_t k = 0; k != 3; ++k)
        for (uint32_t e = 0; e != c.ifaces[k].alts[0].n_eps; ++e, ++n_eps)
            numbers.insert(c.ifaces[k].alts[0].eps[e].address & 0x0fu);
    REQUIRE(numbers.size() == n_eps && !numbers.count(5) && !numbers.count(6) && !numbers.count(0));
    stack::isr_event(4);
    stack::isr_event(5);
    stack::isr_event(6); // reset, address, SET_CONFIGURATION
}

static uint32_t longest_us;
static uint32_t stalls __attribute__((unused));
static void (*after_pump)() = 0; // the responder's steps (usb_service)
static uint64_t cpu_us() { // this thread's CPU time: a pass's own
    timespec t;
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t); // work, not a loaded host's waits
    return uint64_t(t.tv_sec) * 1000000u + uint64_t(t.tv_nsec) / 1000u;
}
static void pass() {
    const uint64_t t0 = cpu_us();
    pump();
    if (after_pump) after_pump();
    const uint32_t us = uint32_t(cpu_us() - t0);
    if (us > longest_us) longest_us = us;
}
static void host_out(const std::vector<uint8_t>& data) { // host bulk OUT transfer, NAKed while parked
    size_t off = 0;
    unsigned spins = 0;
    for (;;) {
        if (stack::mtp_out.has_parked) {
            pass();
            stack::service_in();
            REQUIRE(++spins < 100000);
            continue;
        }
        if (off == data.size() && (data.size() % kMaxPacket || data.empty())) break;
        const uint32_t n = uint32_t(data.size() - off < kMaxPacket ? data.size() - off : kMaxPacket);
        stack::deliver(data.data() + off, n);
        off += n;
        if (n < kMaxPacket) break;
        if (off == data.size()) {
            stack::deliver(0, 0);
            break;
        } // ZLP after a 512-multiple
    }
    while (stack::mtp_out.has_parked) {
        pass();
        stack::service_in();
        REQUIRE(++spins < 100000);
    }
}
static void put32(std::vector<uint8_t>& v, uint32_t x) {
    for (int i = 0; i != 4; ++i) v.push_back(uint8_t(x >> 8 * i));
}
static uint32_t get32(const uint8_t* p) { return p[0] | p[1] << 8 | p[2] << 16 | uint32_t(p[3]) << 24; }
static uint32_t get16(const uint8_t* p) { return p[0] | p[1] << 8; }
static std::vector<uint8_t> container(uint16_t type, uint16_t code, uint32_t tx, std::vector<uint32_t> params,
                                      size_t body = 0) {
    std::vector<uint8_t> v;
    put32(v, uint32_t(12 + 4 * params.size() + body));
    v.push_back(uint8_t(type));
    v.push_back(uint8_t(type >> 8));
    v.push_back(uint8_t(code));
    v.push_back(uint8_t(code >> 8));
    put32(v, tx);
    for (uint32_t p : params) put32(v, p);
    return v;
}
// Run the main loop until the host has a response container; returns {data phase, response}.
static void run_until_response(std::vector<uint8_t>& data, std::vector<uint8_t>& resp) {
    data.clear();
    resp.clear();
    for (unsigned i = 0; i != 10000; ++i) {
        pass();
        stack::service_in();
        auto& in = stack::host_in;
        while (in.size() >= 12 && in.size() >= get32(in.data())) {
            const uint32_t len = get32(in.data()), type = get16(in.data() + 4);
            std::vector<uint8_t> c(in.begin(), in.begin() + len);
            in.erase(in.begin(), in.begin() + len);
            if (type == 2) data = c;
            else {
                REQUIRE(type == 3);
                resp = c;
                return;
            }
        }
    }
    REQUIRE(false);
}

#if XYF_MTP_USB_KICK
// The probe's sink hook sees exactly the sink's data, in order.
static std::vector<uint8_t> hooked;
static unsigned hook_starts;
static void hook(const uint8_t* data, uint32_t n, bool first) {
    if (first) {
        REQUIRE(!data && !n);
        ++hook_starts;
        hooked.clear();
        return;
    }
    hooked.insert(hooked.end(), data, data + n);
}
#endif

// Every OUT urb live after a configure: ours (MTP, serial) and the stack's own one per OUT endpoint.
static const size_t kAllOut = kOutUrbs + kSerialOutUrbs + 2;

// CDC ACM requests on interface 0 (wIndex): line coding stored and returned, control lines, break.
static void cdc_requests(Urb& ep0, uint8_t* buf) {
    const uint8_t get[8] = {0xa1, 0x21, 0, 0, kIfaceComm, 0, 7, 0};
    memcpy(ep0.setup, get, 8);
    ep0.wanted = 0;
    REQUIRE(stack::slot->ops->recv_setup(&ep0) == 0 && ep0.wanted == 7 && get32(buf) == 115200 && buf[6] == 8);
    const uint8_t set[8] = {0x21, 0x20, 0, 0, kIfaceComm, 0, 7, 0};
    const uint8_t coding[7] = {0x00, 0x10, 0x0e, 0x00, 0, 0, 8}; // 921600 8N1, the data stage already in
    memcpy(ep0.setup, set, 8);
    memcpy(buf, coding, 7);
    const uint32_t codings = stats().line_codings;
    ep0.done = 7;                                  // #314: the stack counted the data stage's bytes
    ep0.zlp = 0;
    REQUIRE(stack::slot->ops->recv_setup(&ep0) == 0 && ep0.wanted == 0 && stats().line_codings == codings + 1);
    REQUIRE(ep0.done == 0 && ep0.zlp == 1);         // ended as the stock class ends it: nothing left to send
    const uint8_t get_short[8] = {0xa1, 0x21, 0, 0, kIfaceComm, 0, 3, 0};
    memcpy(ep0.setup, get_short, 8);
    REQUIRE(stack::slot->ops->recv_setup(&ep0) == 0 && ep0.wanted == 3 && ep0.done == 0);   // at most wLength
    memset(buf, 0, 7);
    memcpy(ep0.setup, get, 8);
    REQUIRE(stack::slot->ops->recv_setup(&ep0) == 0 && ep0.wanted == 7 && !memcmp(buf, coding, 7));
    const uint8_t lines[8] = {0x21, 0x22, 3, 0, kIfaceComm, 0, 0, 0};
    memcpy(ep0.setup, lines, 8);
    REQUIRE(stack::slot->ops->recv_setup(&ep0) == 0 && ep0.wanted == 0 && stats().control_lines == 3);
    const uint8_t brk[8] = {0x21, 0x23, 0xff, 0xff, kIfaceComm, 0, 0, 0};
    memcpy(ep0.setup, brk, 8);
    REQUIRE(stack::slot->ops->recv_setup(&ep0) == 0 && ep0.wanted == 0);
    const uint8_t other[8] = {0x21, 0x30, 0, 0, kIfaceComm, 0, 0, 0};
    memcpy(ep0.setup, other, 8);
    REQUIRE(stack::slot->ops->recv_setup(&ep0) != 0); // unknown: stall
    const uint8_t mtp_on_cdc[8] = {0x21, 0x66, 0, 0, kIfaceComm, 0, 0, 0}; // an MTP reset sent to the CDC: stall
    memcpy(ep0.setup, mtp_on_cdc, 8);
    REQUIRE(stack::slot->ops->recv_setup(&ep0) != 0);
}

// The serial link: host bytes in over 0x08 into the ring (in order; held urbs while it is full, NAKs then),
// replies out over 0x8b (one urb in flight, ZLP on 512 multiples), MTP running at the same time.
static void __attribute__((unused)) host_serial_out(const std::vector<uint8_t>& data, std::vector<uint8_t>& got,
                                                    uint32_t read_per_pass) {
    size_t off = 0;
    unsigned spins = 0;
    uint8_t chunk[4096];
    while (off < data.size() || stack::ser_out.has_parked || serial_available()) {
        REQUIRE(++spins < 200000);
        if (!stack::ser_out.has_parked && off < data.size()) {
            const uint32_t n = uint32_t(data.size() - off < kMaxPacket ? data.size() - off : kMaxPacket);
            if (stack::deliver(stack::ser_out, data.data() + off, n)) off += n;
            else off += n; // parked: the packet is the stack's now
            if (spins % 8) continue; // the host is faster than the main loop
        }
        pass();
        const uint32_t n = serial_read(chunk, read_per_pass < sizeof(chunk) ? read_per_pass : sizeof(chunk));
        got.insert(got.end(), chunk, chunk + n);
    }
}
static void serial() {
    std::vector<uint8_t> data, got;
    for (uint32_t i = 0; i != 100000; ++i) data.push_back(uint8_t(i * 31 + (i >> 8)));
    const uint32_t held0 = stats().serial_held;
#if XYF_MTP_USB_KICK
    host_serial_out(data, got, 700); // the reader slower than the host
    REQUIRE(got == data && stats().serial_held > held0 && stats().serial_rx >= data.size());
#else
    // No kick: a held urb is recycled, but a parked packet is never resumed (as MTP's OUT).
    for (uint32_t off = 0; off < 64 * 1024 && !stack::ser_out.has_parked; off += kMaxPacket)
        stack::deliver(stack::ser_out, data.data() + off, kMaxPacket);
    REQUIRE(stack::ser_out.has_parked && stats().serial_held > held0);
    uint8_t sink[4096];
    while (serial_read(sink, sizeof(sink))) {}
    stack::isr_event(0xa);
    pass();
    stack::isr_event(6);
    pass();            // the host re-enumerates
    REQUIRE(state() == State::configured && !stack::ser_out.has_parked);
    (void)got;
#endif
    REQUIRE(stack::live.size() == kAllOut);
    // A short frame: one urb, read back whole.
    const uint8_t hello[5] = {1, 2, 3, 4, 5};
    REQUIRE(stack::deliver(stack::ser_out, hello, 5));
    pass();
    uint8_t five[8] = {};
    REQUIRE(serial_available() == 5 && serial_read(five, 8) == 5 && !memcmp(five, hello, 5) && !serial_available());
    // Writes: one in flight (busy until the host has read it), ZLP on a 512 multiple, MTP untouched.
    std::vector<uint8_t> reply(1000, 0x5a), block(1024, 0xa5);
    stack::host_serial.clear();
    const uint32_t zlps = stack::serial_zlps, mtp_in = stats().in_sent;
    REQUIRE(serial_write(reply.data(), 1000) == 1000);
    REQUIRE(serial_write(block.data(), 1024) == -1);
    stack::service_in();
    REQUIRE(serial_write(block.data(), 1024) == 1024);
    stack::service_in();
    REQUIRE(stack::host_serial.size() == 2024 && stack::serial_zlps == zlps + 1 && stats().in_sent == mtp_in);
    REQUIRE(serial_write(block.data(), kSerialInMax + 1) == -1);
    // MTP and serial interleaved: a GetDeviceInfo while a serial reply is in flight.
    std::vector<uint8_t> mdata, resp;
    REQUIRE(serial_write(reply.data(), 1000) == 1000);
    host_out(container(1, op_get_device_info, 40, {}));
    run_until_response(mdata, resp);
    REQUIRE(get16(resp.data() + 6) == rc_ok && stack::host_serial.size() == 3024);
    // Deconfigure with serial urbs held (ring full): ours freed by us, the stack's by its flush.
#if XYF_MTP_USB_KICK
    const uint32_t held1 = stats().serial_held;
    for (uint32_t off = 0; !stack::ser_out.has_parked; off += kMaxPacket) {
        REQUIRE(off < 64 * 1024);
        stack::deliver(stack::ser_out, data.data() + off, kMaxPacket);
    }
    REQUIRE(serial_available() == kSerialRing && stats().serial_held == held1 + kSerialOutUrbs + 1);
    stack::isr_event(0xa);
    pass();
    REQUIRE(state() == State::started && stack::live.empty());
    uint8_t sink[4096];
    while (serial_read(sink, sizeof(sink))) {}
    stack::isr_event(6);
    pass();
    REQUIRE(state() == State::configured && stack::live.size() == kAllOut);
#endif
    // A bus reset with a serial reply in flight (no flush): the old urb completes after the next configure and
    // counts for nobody (not MTP's IN); the next write goes out.
    REQUIRE(serial_write(reply.data(), 1000) == 1000);
    stack::isr_event(4);
    pass();
    REQUIRE(state() == State::started && stack::in_pending.size() == 1);
    REQUIRE(serial_write(reply.data(), 10) == -1); // not configured
    stack::isr_event(5);
    stack::isr_event(6);
    pass(); // the flush completes the old urb
    REQUIRE(state() == State::configured && stack::in_pending.empty());
    host_out(container(1, op_get_device_info, 41, {}));
    run_until_response(mdata, resp);
    REQUIRE(get16(resp.data() + 6) == rc_ok);
    REQUIRE(serial_write(reply.data(), 10) == 10);
    stack::service_in();
}

static bool flushed_inside;
static void flush_inside_pump(const uint8_t*, uint32_t n, bool first) {
    if (!first && n && !flushed_inside) {
        flushed_inside = true;
        stack::isr_event(0xa);
    }
}

static void session() {
    std::vector<uint8_t> data, resp;
    stack::boot();
    REQUIRE(begin(stack::port) && state() == State::started && stack::slot == driver());
    REQUIRE(!begin(stack::port));
    REQUIRE(stack::starts == std::vector<uint32_t>({kCompositeFlags}) && stack::stops.empty()); // one start, no stop
    enumerate();
    pass();
    REQUIRE(state() == State::configured && stack::live.size() == kAllOut); // ours + the stack's own
    REQUIRE(stack::active() && stack::mtp_out.queue.size() == kOutUrbs); // the stack's urb is active first
    REQUIRE(stack::active(stack::ser_out) && stack::ser_out.queue.size() == kSerialOutUrbs);
    // GetDeviceInfo before a session (allowed), then OpenSession.
    host_out(container(1, 0x1001, 1, {}));
    run_until_response(data, resp);
    REQUIRE(get16(resp.data() + 6) == rc_ok && get32(resp.data() + 8) == 1);
    REQUIRE(data.size() > 40 && get16(data.data() + 6) == 0x1001 && get16(data.data() + 12) == 100);
    REQUIRE(get32(data.data() + 14) == 6);
    host_out(container(1, 0x1002, 2, {1}));
    run_until_response(data, resp);
    REQUIRE(get16(resp.data() + 6) == rc_ok && data.empty());
    REQUIRE(stats().device_infos == 1 && stats().sessions_opened == 1 && stats().sessions_closed == 0);
    host_out(container(1, 0x1002, 3, {1}));
    run_until_response(data, resp);
    REQUIRE(get16(resp.data() + 6) == rc_session_already_open);
    host_out(container(1, 0x1234, 4, {}));
    run_until_response(data, resp);
    REQUIRE(get16(resp.data() + 6) == rc_operation_not_supported);
    // 1 MiB counted sink: paced by the urbs; every pass bounded.
    const uint32_t mib = 1u << 20;
    host_out(container(1, op_test_sink, 5, {mib}));
    std::vector<uint8_t> payload = container(2, op_test_sink, 5, {}, mib);
    uint32_t sum = 0;
    for (uint32_t i = 0; i != mib; ++i) {
        payload.push_back(uint8_t(i * 13 + (i >> 11)));
        sum += payload.back();
    }
    const uint32_t parks0 = stack::parks;
    longest_us = 0;
#if XYF_MTP_USB_KICK
    hooked.clear();
    hook_starts = 0;
    set_sink_hook(hook);
    host_out(payload);
    run_until_response(data, resp);
    set_sink_hook(0);
    REQUIRE(get16(resp.data() + 6) == rc_ok && get32(resp.data() + 12) == mib && get32(resp.data() + 16) == sum);
    REQUIRE(stats().sink_bytes == mib && stack::parks > parks0 && stats().kick_rx > 0);
    REQUIRE(hook_starts == 1 && hooked.size() == mib && std::equal(hooked.begin(), hooked.end(), payload.begin() + 12));
#else
    // No kick (default): the host bursts; once every urb is full before pump() recycles one, the next
    // packet parks and nothing resumes it: OUT stalls until re-enumeration.
    uint32_t accepted = 0;
    while (accepted < payload.size() && stack::deliver(payload.data() + accepted, kMaxPacket)) accepted += kMaxPacket;
    for (unsigned i = 0; i != 10000; ++i) {
        pass();
        stack::service_in();
    }
    REQUIRE(stack::mtp_out.has_parked && stack::parks == parks0 + 1 && stats().kicks == 0 && stats().kick_rx == 0);
    REQUIRE(stats().sink_bytes == accepted - 12 && sum != 0);
    if (++stalls == 1) printf("no kick: OUT stalled after %u bytes (the urbs free at the burst)\n", accepted);
    stack::isr_event(0xa);
    pass();
    stack::isr_event(6);
    pass();            // the host gives up and re-enumerates
    REQUIRE(state() == State::configured && !stack::mtp_out.has_parked);
#endif
    REQUIRE(stats().longest_pass_urbs <= kOutUrbs && stats().longest_pass_bytes <= kOutUrbs * kOutSize);
    REQUIRE(longest_us < 50000); // host: generous; the bound is in bytes
    REQUIRE(stack::live.size() == kAllOut && stats().urbs_dropped == 0);
    // Source 100000 bytes and a 3000-byte loopback.
    host_out(container(1, op_test_source, 6, {100000}));
    run_until_response(data, resp);
    REQUIRE(get16(resp.data() + 6) == rc_ok && data.size() == 100012);
    bool same = true;
    for (uint32_t i = 0; i != 100000; ++i) same = same && data[12 + i] == uint8_t(i * 7u + (i >> 9));
    REQUIRE(same);
    host_out(container(1, op_test_loopback, 7, {}));
    std::vector<uint8_t> echo = container(2, op_test_loopback, 7, {}, 3000);
    for (uint32_t i = 0; i != 3000; ++i) echo.push_back(uint8_t(i ^ 0x5a));
    host_out(echo);
    run_until_response(data, resp);
    REQUIRE(get16(resp.data() + 6) == rc_ok && data.size() == 3012 &&
            !memcmp(data.data() + 12, echo.data() + 12, 3000));
    // Class requests on EP0: Get Device Status.
    Urb ep0 = {};
    uint8_t buf[0x280];
    ep0.buf = buf;
    const uint8_t gds[8] = {0xa1, 0x67, 0, 0, kIfaceMtp, 0, 4, 0};
    memcpy(ep0.setup, gds, 8);
    REQUIRE(stack::slot->ops->recv_setup(&ep0) == 0 && ep0.wanted == 4 && get16(buf + 2) == rc_ok);
    ep0.setup[4] = kIfaceComm;
    REQUIRE(stack::slot->ops->recv_setup(&ep0) != 0); // by interface: not the CDC's
    ep0.setup[4] = kIfaceData;
    REQUIRE(stack::slot->ops->recv_setup(&ep0) != 0);
    cdc_requests(ep0, buf);
    const uint8_t std_req[8] = {0x80, 6, 0, 1, 0, 0, 18, 0};
    memcpy(ep0.setup, std_req, 8);
    REQUIRE(stack::slot->ops->recv_setup(&ep0) != 0);
    // Deconfigure mid-transfer (OUT): held urbs freed by us, queued ones by the stack; reconfigure re-arms.
    host_out(container(1, op_test_sink, 8, {mib}));
    stack::deliver(container(2, op_test_sink, 8, {}, mib).data(), 12); // short: completes one urb, held in the ring
    stack::isr_event(0xa);
    pass();
    REQUIRE(state() == State::started && stack::live.empty() && stats().deconfigures == (XYF_MTP_USB_KICK ? 1u : 2u));
    stack::isr_event(6);
    pass();
    REQUIRE(state() == State::configured && stack::live.size() == kAllOut);
    // Deconfigure mid-transfer (IN): the in-flight IN urb is completed by the flush and freed by the stack.
    host_out(container(1, op_test_source, 10, {100000}));
    pass();
    pass();
    REQUIRE(stack::in_pending.size() == 1);
    stack::isr_event(0xa);
    pass();
    REQUIRE(state() == State::started && stack::live.empty() && stack::in_pending.empty());
    stack::isr_event(6);
    pass();
    REQUIRE(state() == State::configured && stack::live.size() == kAllOut);
    // Bus reset mid-transfer: no flush at reset; queued urbs stay with the stack (not ours to free), the
    // held one is freed by us, and the next SET_CONFIGURATION's flush frees the queued ones.
    host_out(container(1, op_test_sink, 11, {mib}));
    stack::deliver(container(2, op_test_sink, 11, {}, mib).data(), 12);
    const uint32_t resets0 = stats().resets_bus;
    stack::isr_event(4);
    pass();
    REQUIRE(state() == State::started && stats().resets_bus == resets0 + 1);
    REQUIRE(stack::live.size() == stack::mtp_out.queue.size() + (stack::active() ? 1 : 0) +
                                      stack::ser_out.queue.size() +
                                      (stack::active(stack::ser_out) ? 1 : 0)); // all left are the stack's
    stack::isr_event(5);
    stack::isr_event(6);
    pass();
    REQUIRE(state() == State::configured && stack::live.size() == kAllOut);
    // A flush between taking a urb from the ring and requeueing it (event 0xa while pump() is inside the
    // sink hook): the old-generation urb is freed, not appended to the stack's queue.
    host_out(container(1, op_test_sink, 12, {mib}));
    set_sink_hook(flush_inside_pump);
    flushed_inside = false;
    std::vector<uint8_t> piece = container(2, op_test_sink, 12, {}, mib);
    piece.resize(12 + 100, 0x33);
    stack::deliver(piece.data(), uint32_t(piece.size())); // short: completes one urb
    pass();
    set_sink_hook(0);
    REQUIRE(flushed_inside && stack::live.empty()); // the old-generation urb freed
    pass();
    REQUIRE(state() == State::started && stack::live.empty());
    stack::isr_event(6);
    pass();
    REQUIRE(state() == State::configured && stack::live.size() == kAllOut);
    // SET_INTERFACE flushes like SET_CONFIGURATION: re-armed.
    stack::isr_event(7);
    pass();
    REQUIRE(state() == State::configured && stack::live.size() == kAllOut - 2);
    // Suspend (0xa flushes), then resume (0xc, no SET_CONFIGURATION): bulk OUT re-armed; a lone 0xc changes nothing.
    stack::isr_event(0xa);
    pass();
    REQUIRE(state() == State::started && stack::live.empty());
    stack::isr_event(0xc);
    pass();
    REQUIRE(state() == State::configured && stack::live.size() == kAllOut - 2);
    const uint32_t deconf = stats().deconfigures;
    stack::isr_event(0xc);
    pass();
    REQUIRE(state() == State::configured && stack::live.size() == kAllOut - 2 && stats().deconfigures == deconf);
    stack::isr_event(6);
    pass(); // a stack urb active again (no kick: 002)
    REQUIRE(state() == State::configured && stack::live.size() == kAllOut);
    serial();
    // Leave (before a restart) with a received urb held: nothing leaks, the slot is free.
    stack::deliver(container(1, 0x1003, 9, {}).data(), 12);
    leave();
    REQUIRE(state() == State::off && stack::live.empty() && stack::in_pending.empty() && !stack::slot);
    REQUIRE(stack::stops == std::vector<uint32_t>({0}) && stack::timer_open); // stop(0): GP timer 7 kept
    REQUIRE(serial_write(reinterpret_cast<const uint8_t*>("x"), 1) == -1);
    pump(); // off: nothing
}

// ---- Progress marks --------------------------------------------------------------------------------
// The hook as usb_service's: each mark rewrites the whole file (here: the list); fail_at's write fails.
static std::vector<uint32_t> marks_file, marks_ram;
static uint32_t fail_at, hook_calls;
static bool record_mark(uint32_t s) {
    ++hook_calls;
    if (s == fail_at) return false;
    marks_ram.push_back(s);
    marks_file = marks_ram;
    return true;
}
// One trial boot as usb_service runs it: the flag found and deleted (marks 1, 2), begin, the host's MTP
// session (GetDeviceInfo, OpenSession, an 8 KiB sink, CloseSession), the restart (leave).
static void trial_run() {
    std::vector<uint8_t> data, resp;
    stack::boot();
    marks_file.clear();
    marks_ram.clear();
    hook_calls = 0;
    set_mark_hook(record_mark);
    mark(mark_flag_found);
    mark(mark_flag_deleted);
    REQUIRE(begin(stack::port));
    enumerate();
    pass();
    host_out(container(1, op_get_device_info, 1, {}));
    run_until_response(data, resp);
    REQUIRE(get16(resp.data() + 6) == rc_ok);
    host_out(container(1, op_open_session, 2, {1}));
    run_until_response(data, resp);
    REQUIRE(session_open());
    std::vector<uint8_t> payload = container(2, op_test_sink, 3, {}, 8192);
    for (uint32_t i = 0; i != 8192; ++i) payload.push_back(uint8_t(i * 5));
    host_out(container(1, op_test_sink, 3, {8192}));
    host_out(payload);
    run_until_response(data, resp);
    REQUIRE(get16(resp.data() + 6) == rc_ok && get32(resp.data() + 12) == 8192);
    host_out(container(1, op_close_session, 4, {}));
    run_until_response(data, resp);
    REQUIRE(!session_open() && stats().mtp_commands == 4);
    leave();
    REQUIRE(state() == State::off && !stack::slot && stack::live.empty());
    REQUIRE(stack::stops == std::vector<uint32_t>({0}) && stack::starts == std::vector<uint32_t>({kCompositeFlags}));
}
static void marks() {
    const std::vector<uint32_t> pass_order = {mark_flag_found,  mark_flag_deleted, mark_registered, mark_before_start,
                                              mark_after_start, mark_first_setup,  mark_configured, mark_first_command};
    fail_at = 0;
    trial_run(); // clean: the driver's marks in order; 6/7, 10, 11, 13 are usb_service's
    REQUIRE(marks_file == pass_order);
    REQUIRE(stats().marks_written == pass_order.size() && stats().marks_failed == 0);
    for (uint32_t s = 1; s != mark_count; ++s) REQUIRE(strcmp(mark_name(s), "?") != 0);
    REQUIRE(!strcmp(mark_name(0), "?") && !strcmp(mark_name(mark_count), "?"));
    // A failed mark (A did not answer, or refused): the boot goes on unchanged; no further mark is tried.
    for (uint32_t k = 0; k != pass_order.size(); ++k) {
        fail_at = pass_order[k];
        trial_run();
        REQUIRE(marks_file.size() == k && hook_calls == k + 1);
        REQUIRE(stats().marks_written == k && stats().marks_failed == 1);
    }
    fail_at = 0;
    // Register refused (the slot taken): nothing started, mark 14.
    stack::boot();
    stack::slot = &stack::other;
    marks_file.clear();
    marks_ram.clear();
    set_mark_hook(record_mark);
    REQUIRE(!begin(stack::port) && state() == State::off && stack::starts.empty());
    REQUIRE(marks_file == std::vector<uint32_t>({mark_register_refused}));
    stack::slot = 0;
    // A freeze inside each stock step (n-th call of it): the marks file's last mark names that step.
    struct Case {
        stack::Op op;
        uint32_t nth;
        uint32_t last;
        const char* what;
    };
    const Case cases[] = {
        {stack::op_register, 1, mark_flag_deleted, "register"},
        {stack::op_start, 1, mark_before_start, "start(0x200): the controller init's 5-tick wait"},
        {stack::op_alloc, 1, mark_first_setup, "configure (the first alloc)"},
        {stack::op_submit, 1, mark_first_command, "the first reply (GetDeviceInfo)"},
        {stack::op_stop, 1, mark_first_command, "stop(0) before the restart"},
    };
    for (const Case& c : cases) {
        fflush(stdout);
        const pid_t pid = fork();
        REQUIRE(pid >= 0);
        if (!pid) {
            stack::freeze_op = c.op;
            stack::freeze_nth = c.nth;
            bool frozen = false;
            try {
                trial_run();
            } catch (stack::Freeze&) { frozen = true; }
            _exit(frozen && !marks_file.empty() && marks_file.back() == c.last ? 0 : 1);
        }
        int status = 0;
        REQUIRE(waitpid(pid, &status, 0) == pid);
        if (!WIFEXITED(status) || WEXITSTATUS(status)) fprintf(stderr, "freeze in %s: marks do not name it\n", c.what);
        REQUIRE(WIFEXITED(status) && WEXITSTATUS(status) == 0);
        printf("freeze in %s: last mark %u %s\n", c.what, c.last, mark_name(c.last));
    }
    set_mark_hook(0);
}

static void unmasked_free() { // argv "unmasked-free": the model must abort (Python checks)
    stack::started = true;
    const uint32_t m = stack::mask();
    Urb* u = stack::alloc(stack::kDev, kBulkIn, 4);
    stack::unmask(m);
    stack::free_urb(u); // interrupt fires inside the unmasked heap call
}

#if XYF_MTP_USB_KICK
// ---- The responder as the MTP interface's handler, over an in-memory /yaffs2 --------------------------------
namespace t2b {
namespace M = xyf_core::mtp;
using stack::check_containers;
using stack::mtp_left;
using stack::mtp_urbs;
using stack::mtp_zlps;
using xyf_core::U32;
M::Responder responder;
std::vector<uint8_t> big, up;
bool up_exists;
uint32_t prepared, logged;
int list(void*, const char* folder, U32 skip, M::Entry* out, U32 room, U32& count, U32& more) {
    std::vector<M::Entry> all;
    auto add = [&](const char* name, U32 kind, U32 size) {
        M::Entry e = {kind, size, {}};
        strcpy(e.name, name);
        all.push_back(e);
    };
    if (!strcmp(folder, "/yaffs2")) add("xy-forever", 1, 0);
    else if (!strcmp(folder, "/yaffs2/xy-forever")) {
        add("big.bin", 0, U32(big.size()));
        add("tmp", 1, 0);
    } else if (!strcmp(folder, "/yaffs2/xy-forever/tmp")) {
        if (up_exists) add("up.bin", 0, U32(up.size()));
    } else return M::f_not_found;
    count = 0;
    for (U32 i = skip; i < all.size() && count != room; ++i) out[count++] = all[i];
    more = skip + count < all.size();
    return 0;
}
int stat(void*, const char* path, M::Entry& e) {
    e.name[0] = 0;
    if (!strcmp(path, "/yaffs2/xy-forever") || !strcmp(path, "/yaffs2/xy-forever/tmp")) {
        e.kind = 1;
        e.size = 0;
        return 0;
    }
    if (!strcmp(path, "/yaffs2/xy-forever/big.bin")) {
        e.kind = 0;
        e.size = U32(big.size());
        return 0;
    }
    if (!strcmp(path, "/yaffs2/xy-forever/tmp/up.bin") && up_exists) {
        e.kind = 0;
        e.size = U32(up.size());
        return 0;
    }
    return M::f_not_found;
}
int space(void*, const char*, U32& b, U32& n, U32& f) {
    b = 4096;
    n = 1000;
    f = 500;
    return 0;
}
uint32_t read_at;
int open(void*, const char* path, bool write) {
    if (write) {
        REQUIRE(!strcmp(path, "/yaffs2/xy-forever/tmp/up.bin"));
        up.clear();
        up_exists = true;
        return 4;
    }
    if (!strcmp(path, "/yaffs2/xy-forever/big.bin")) {
        read_at = 0;
        return 3;
    }
    return M::f_not_found;
}
int read(void*, int fd, xyf_core::Byte* out, U32 n) {
    REQUIRE(fd == 3 && n <= M::default_piece);
    if (n > big.size() - read_at) n = U32(big.size() - read_at);
    memcpy(out, big.data() + read_at, n);
    read_at += n;
    return int(n);
}
int write(void*, int fd, const xyf_core::Byte* data, U32 n) {
    REQUIRE(fd == 4 && n <= M::default_piece);
    up.insert(up.end(), data, data + n);
    return int(n);
}
int seek(void*, int, U32 offset) {
    read_at = offset;
    return 0;
}
int close(void*, int) { return 0; }
int refuse(void*, const char*) { return M::f_refused; }
int unlink(void*, const char* path) {
    if (!strcmp(path, "/yaffs2/xy-forever/tmp/up.bin") && up_exists) {
        up_exists = false;
        return 0;
    }
    return M::f_not_found;
}
int rename(void*, const char*, const char*) { return M::f_refused; }
int upload(void*, const char* path, U32, U32) {
    REQUIRE(!strcmp(path, "/yaffs2/xy-forever/tmp/up.bin"));
    up.clear();
    return 4;
}
int commit(void*, const char*, bool keep) {
    if (keep) up_exists = true;
    return 0;
} // #4: the name only now
U32 prepare(void*) {
    ++prepared;
    return 0;
}
void log(void*, U32, const U32*, U32, U32, U32) { ++logged; }
void steps() {
    for (int i = 0; i != 8 && responder.step(); ++i) {}
}
bool h_deliver(const uint8_t* d, uint32_t n) {
    REQUIRE(n && n <= kHandlerPiece);
    return responder.receive(d, n);
}
uint32_t h_pending() { return responder.pending(); }
uint32_t h_length() { return responder.staged_length(); }
uint32_t h_take(uint8_t* out, uint32_t n) { return responder.take(out, n); }
bool h_idle() { return !responder.busy(); }
void h_cancel(bool reset) {
    if (reset) responder.reset();
    else responder.cancel();
}
uint16_t h_status() { return uint16_t(responder.device_status()); }
const Handler handler = {h_deliver, h_pending, h_length, h_take, h_idle, h_cancel, h_status};
uint32_t handle_named(const std::vector<uint8_t>& handles, uint32_t tx, const char* want) { // GetObjectInfo names
    std::vector<uint8_t> data, resp;
    for (uint32_t i = 0; i != get32(handles.data() + 12); ++i) {
        const uint32_t h = get32(handles.data() + 16 + 4 * i);
        host_out(container(1, 0x1008, tx, {h}));
        run_until_response(data, resp);
        REQUIRE(get16(resp.data() + 6) == 0x2001);
        std::string name;
        for (uint32_t k = 0; k + 1 < data[12 + 52]; ++k) name += char(data[12 + 53 + 2 * k]);
        if (name == want) return h;
    }
    return 0;
}
void run() {
    std::vector<uint8_t> data, resp;
    big.resize(512 * 137 - 12); // the data container: a ZLP
    for (size_t i = 0; i != big.size(); ++i) big[i] = uint8_t(i * 13 + (i >> 8));
    const M::FileOps ops = {0,      list,   stat,   space,  open,    read, write,  seek,   close,
                            refuse, unlink, refuse, rename, prepare, log,  upload, commit, 0};
    responder.init(ops);
    stack::boot();
    set_handler(&handler);
    after_pump = steps;
    check_containers = true;
    mtp_left = mtp_zlps = mtp_urbs = 0;
    REQUIRE(begin(stack::port));
    enumerate();
    pass();
    host_out(container(1, 0x1001, 1, {}));
    run_until_response(data, resp);
    REQUIRE(get16(resp.data() + 6) == 0x2001 && data.size() > 12);
    host_out(container(1, 0x1002, 2, {1}));
    run_until_response(data, resp);
    REQUIRE(get16(resp.data() + 6) == 0x2001 && responder.session_open());
    host_out(container(1, 0x1007, 3, {0x00010001u, 0, 0xffffffffu}));
    run_until_response(data, resp);
    const uint32_t xyf = handle_named(data, 4, "xy-forever");
    REQUIRE(xyf);
    host_out(container(1, 0x1007, 5, {0x00010001u, 0, xyf}));
    run_until_response(data, resp);
    const std::vector<uint8_t> listing = data;
    const uint32_t file = handle_named(listing, 6, "big.bin"), tmp = handle_named(listing, 7, "tmp");
    REQUIRE(file && tmp);
    const uint32_t zlps = mtp_zlps;
    host_out(container(1, 0x1009, 8, {file}));
    run_until_response(data, resp); // GetObject: 4 KiB at most staged
    REQUIRE(get16(resp.data() + 6) == 0x2001 && data.size() == big.size() + 12 &&
            !memcmp(data.data() + 12, big.data(), big.size()));
    REQUIRE(mtp_zlps == zlps + 1);
    // SendObjectInfo (up.bin, 20000 bytes) and SendObject: the OUT urbs (16 KiB) go in 4 KiB pieces, held meanwhile.
    std::vector<uint8_t> info(52, 0), body(20000);
    info[0] = 1;
    info[2] = 1;
    info[4] = 0x00;
    info[5] = 0x30;
    info[8] = uint8_t(20000 & 0xff);
    info[9] = uint8_t(20000 >> 8);
    const char name[] = "up.bin";
    info.push_back(uint8_t(sizeof(name)));
    for (char c : name) {
        info.push_back(uint8_t(c));
        info.push_back(0);
    }
    for (int k = 0; k != 3; ++k) info.push_back(0);
    std::vector<uint8_t> phase = container(2, 0x100c, 9, {}, info.size());
    phase.insert(phase.end(), info.begin(), info.end());
    host_out(container(1, 0x100c, 9, {0x00010001u, tmp}));
    host_out(phase);
    run_until_response(data, resp);
    REQUIRE(get16(resp.data() + 6) == 0x2001);
    for (size_t i = 0; i != body.size(); ++i) body[i] = uint8_t(i * 7);
    phase = container(2, 0x100d, 10, {}, body.size());
    phase.insert(phase.end(), body.begin(), body.end());
    host_out(container(1, 0x100d, 10, {}));
    host_out(phase);
    run_until_response(data, resp);
    REQUIRE(get16(resp.data() + 6) == 0x2001 && up_exists && up == body);
    REQUIRE(stats().handler_in >= body.size() && stats().urbs_dropped == 0);
    // An unknown operation answers OperationNotSupported; the device keeps going.
    host_out(container(1, 0x95c1, 11, {1, 0, 0, 16}));
    run_until_response(data, resp);
    REQUIRE(get16(resp.data() + 6) == 0x2005);
    // The test sink still runs in the driver while the responder is idle.
    std::vector<uint8_t> sink = container(2, op_test_sink, 12, {}, 4096);
    for (uint32_t i = 0; i != 4096; ++i) sink.push_back(uint8_t(i));
    host_out(container(1, op_test_sink, 12, {4096}));
    host_out(sink);
    run_until_response(data, resp);
    REQUIRE(get16(resp.data() + 6) == 0x2001 && get32(resp.data() + 12) == 4096);
    host_out(container(1, 0x1003, 13, {}));
    run_until_response(data, resp);
    REQUIRE(get16(resp.data() + 6) == 0x2001 && !responder.session_open());
    REQUIRE(prepared >= 5 && logged >= 13);
    leave();
    set_handler(0);
    after_pump = 0;
    check_containers = false;
    REQUIRE(state() == State::off && stack::live.empty());
    printf("t2b: responder over the driver: %u IN urbs, %u ZLPs, %u bytes to the handler\n", mtp_urbs, mtp_zlps,
           stats().handler_in);
}
}
#endif
int main(int argc, char** argv) {
    if (argc > 1 && !strcmp(argv[1], "unmasked-free")) {
        unmasked_free();
        return 0;
    }
    REQUIRE(rx_service_allowed(kBulkOut & 0xf) && !rx_service_allowed(0)); // the binding's guard
    for (uint32_t n = 1; n != 16; ++n) REQUIRE(rx_service_allowed(n) == (n == 0xa || n == 8));
    session();
    session(); // begin and leave twice
    marks();
#if XYF_MTP_USB_KICK
    t2b::run();
#endif
    printf("%u checks passed\n", checks);
    return 0;
}

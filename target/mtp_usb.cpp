// Our composite function driver (mtp_usb.hpp): CDC serial + MTP. Interrupt side: the four
// stock callbacks only push urbs and counters into single-producer rings (serial OUT: bytes into a RAM
// ring). Main-loop side: pump(), bounded per pass; serial_read / serial_write.
#include "mtp_usb.hpp"

namespace xyf { namespace mtp_usb {
namespace {

void copy(void* to, const void* from, uint32_t n) {   // freestanding: no string.h
    uint8_t* t = static_cast<uint8_t*>(to); const uint8_t* f = static_cast<const uint8_t*>(from);
    for (uint32_t i = 0; i != n; ++i) t[i] = f[i];
}
uint32_t length(const char* s) { uint32_t n = 0; while (s[n]) ++n; return n; }

const char kName[] = "XY Forever", kMaker[] = "XY Forever", kProduct[] = "OP-XY (XY Forever)",
           kSerial[] = "XYF0001", kIface[] = "MTP", kSerialName[] = "XY Forever Serial", kDataName[] = "XY Forever Data";
int on_event(uintptr_t dev, uint32_t code);
int on_urb_sent(Urb* urb);
int on_recv_urb(Urb* urb);
int on_recv_setup(Urb* ep0);
const Ops kOps = {on_event, on_urb_sent, on_recv_urb, on_recv_setup};
// VID/PID: the stock pair 2367:0021 that the stock MTP and CDC drivers both declare (teenage
// engineering's pair, reused; revisit before a public release; xyf_build.py
// MTP_USB_IDS). Builds 186 to 189-001 used pid.codes' test pair 1209:0001. The device class 0xEF (below)
// tells the composite from the stock OS's MTP and the stock serial fallback on the same pair.
// Class EF/02/01 (miscellaneous, IAD): Windows and Linux bind the CDC pair through the IAD.
const DevInfo kDevInfo = {0xef, 2, 1, 0, 0x2367u, 0x0021u, kMaker, kProduct, kSerial};
// dir: 0x80 for IN, as the stock MTP records (driver 88a12534); the stack ORs it into bEndpointAddress
// (88a0a758..60), review 029 finding 4.
const EpDesc kEps[3] = {{uint8_t(kBulkIn), 2, uint16_t(kMaxPacket), 0, 0x80, 0},
                        {uint8_t(kBulkOut), 2, uint16_t(kMaxPacket), 0, 0, 0},
                        {uint8_t(kIntIn), 3, 64, 6, 0x80, 0}};
const AltDesc kAlt = {kIface, 0, 0, 0, 3, kEps};
// CDC (record 031 b2): notify as the stock CDC's (0x88a134bc: 0x81, interrupt, 16 bytes, interval 0x10);
// data on 0x08 / 0x8b, 512 (endpoint numbers 1, 3, 8, 9, 10, 11 in all: one FIFO per number, 5 and 6 avoided).
const EpDesc kNotifyEp[1] = {{uint8_t(kNotifyIn), 3, 16, 0x10, 0x80, 0}};
const EpDesc kDataEps[2] = {{uint8_t(kSerialOut), 2, uint16_t(kMaxPacket), 0, 0, 0},
                            {uint8_t(kSerialIn), 2, uint16_t(kMaxPacket), 0, 0x80, 0}};
// The stock CDC's order (0x88a13370): header, union (master 0, slave 1), call management (caps 0, data 1),
// ACM (caps 0x02: line coding and serial state requests). Writable: the stack's fill only reads them.
CdcExtra g_cdc_extras[4] = {{0x24, 0x00, {0, 0}, {0}}, {0x24, 0x06, {0, 0}, {kIfaceComm, kIfaceData}},
                            {0x24, 0x01, {0, 0}, {0, kIfaceData}}, {0x24, 0x02, {0, 0}, {0x02}}};
const AltDesc kCommAlt = {kSerialName, 0, 4, g_cdc_extras, 1, kNotifyEp};
const AltDesc kDataAlt = {kDataName, 0, 0, 0, 2, kDataEps};
const IfaceDesc kIfaces[3] = {{kSerialName, 2, 2, 1, 0, 1, &kCommAlt},   // CDC comm, ACM, AT (as stock)
                              {kDataName, 0x0a, 0, 0, 0, 1, &kDataAlt},    // CDC data
                              {kIface, 6, 1, 1, 0, 1, &kAlt}};             // still image, PTP/MTP
const Iad kIads[1] = {{kSerialName, 2, 2, 1, kIfaceComm, 2, {0, 0, 0}}};
const ConfigDesc kConfig = {kName, 0xc0, 50, 0, 3, kIfaces, 1, kIads};
Driver g_driver = {kName, &kOps, &kDevInfo, &kConfig, 0, 0, 1, 0};

const Port* g_port = 0;
volatile State g_state = State::off;
Stats g_stats = {};
SinkHook g_sink_hook = 0;

// Every global has an initializer: the cold link has no BSS (built with -fno-zero-initialized-in-bss).
// Interrupt -> main rings (one producer: the USB interrupt; one consumer: pump()).
const uint32_t kRing = 8;                       // > kOutUrbs
Urb* volatile ring[kRing] = {};
volatile uint32_t ring_head = 0, ring_tail = 0;         // head: interrupt, tail: main
volatile uint32_t sent_count = 0;                   // urb_sent completions (interrupt)
volatile uint32_t config_gen = 0;                   // event 6 bumps config_gen; 0xa / reset bump too
volatile bool want_config = false, cancel_flag = false, reset_flag = false;
volatile uintptr_t g_dev = 0;
// 186-005: suspended: a 0xa came after a configuration (no bus reset since), so a resume (0xc) re-arms bulk
// OUT (no SET_CONFIGURATION follows a resume; review 029 finding 3). setup_seen: SET_ADDRESS (5), a
// configuration or a class request reached the driver (standard requests stay in the stack, record 002).
volatile bool configured_bus = false, suspended = false, setup_seen = false;
// The serial. OUT: the interrupt copies each received urb into the ring (head: interrupt, or main under mask)
// and lets the stack re-queue it (return 1, as the stock CDC); with no room, or while any urb is held, it
// holds the urb (return 0) in held[], in order: pump() copies it once there is room and recycles it (the
// host meanwhile gets NAKs: flow control). IN: one urb in flight, recognised by its pointer in urb_sent.
uint8_t serial_ring[kSerialRing] = {};
volatile uint32_t sring_head = 0, sring_tail = 0;
const uint32_t kHeld = 8;                           // > kSerialOutUrbs + the stack's own
Urb* volatile held[kHeld] = {};
volatile uint32_t held_head = 0, held_tail = 0;
Urb* volatile serial_in_urb = 0;
Urb* volatile serial_stale = 0;                     // in flight across a bus reset (no flush): completes later
const uint8_t kLineCoding[7] = {0x00, 0xc2, 0x01, 0x00, 0, 0, 8};   // 115200 8N1 until the host sets one
uint8_t line_coding[7] = {0x00, 0xc2, 0x01, 0x00, 0, 0, 8};
Urb* serial_out[kSerialOutUrbs] = {};

// 186-005: progress marks (mark()); marked_*: each run-time mark once per enter().
MarkHook g_mark_hook = 0;
bool marks_off = false;
bool marked_setup = false, marked_config = false, marked_command = false;
const char* const kMarkNames[mark_count] = {
    "?", "flag-found", "flag-deleted", "registered", "before-start-0x200", "after-start-0x200",
    "attach-ok", "attach-failed", "first-setup", "configured", "first-serial-byte", "first-hello",
    "first-mtp-command", "fallback-restart", "register-refused"};

// Main-loop state.
uint32_t main_gen = 0;                              // generation our queued urbs belong to
Urb* out_urbs[kOutUrbs] = {};                        // allocated OUT urbs (queued or held by us)
uint32_t n_out = 0;
bool g_session_open = false;
uint32_t session_id = 0;

enum class Phase : uint32_t { command, data_out, data_in, response };
Phase phase = Phase::command;
uint32_t cur_op = 0, cur_tx = 0, out_left = 0, out_seen = 0;
uint8_t loop_buf[kLoopMax + 12] = {};
uint32_t loop_len = 0;
// IN side: one urb in flight; what to send comes from a generator by offset.
enum class Src : uint32_t { none, buffer, pattern };
Src src = Src::none;
uint8_t tx_buf[512] = {};                            // GetDeviceInfo data container, responses
const uint8_t* src_data = 0;
uint32_t src_len = 0, src_off = 0, in_inflight_seen = 0;
bool in_inflight = false;
uint32_t resp_code = 0, resp_param[2] = {}, resp_nparam = 0;
// The handler (usb_service's responder). rx_off: bytes of the ring's head urb already delivered; h_left:
// bytes left of the IN container being sent (0: the next one starts).
const Handler* g_handler = 0;
uint32_t rx_off = 0, h_left = 0;

void put16(uint8_t* p, uint32_t v) { p[0] = uint8_t(v); p[1] = uint8_t(v >> 8); }
void put32(uint8_t* p, uint32_t v) { put16(p, v); put16(p + 2, v >> 16); }
uint32_t get16(const uint8_t* p) { return p[0] | uint32_t(p[1]) << 8; }
uint32_t get32(const uint8_t* p) { return get16(p) | get16(p + 2) << 16; }
uint8_t pattern(uint64_t i) { return uint8_t(i * 7u + (i >> 9)); }

// ---- interrupt side ---------------------------------------------------------------------
int on_event(uintptr_t dev, uint32_t code) {
    // Record 027: deconfigure (0xa), stop (0xf) and every SET_CONFIGURATION (6/7) flush all endpoints
    // (88a08ab4 -> 88a095a8): queued/active OUT urbs freed by the stack, IN completed through urb_sent.
    // Bus reset (4) does not flush: our queued urbs stay with the stack until the next configure frees
    // them. Either way the queued ones are never ours to free; only urbs taken from the ring are.
    // SET_INTERFACE (7) flushes as 6 does (027 item 2): the same reconfigure (review 029 finding 3).
    // A resume (0xc) after a suspend's 0xa brings no SET_CONFIGURATION: re-arm bulk OUT as one.
    if (code == 6 || code == 7) { g_dev = dev; ++config_gen; want_config = true; configured_bus = true;
                                  suspended = false; setup_seen = true; }
    else if (code == 0xa) { ++config_gen; want_config = false; suspended = configured_bus; }
    else if (code == 0xc && suspended && g_dev) { ++config_gen; want_config = true; suspended = false; }
    else if (code == 4) { ++config_gen; want_config = false; configured_bus = suspended = false; ++g_stats.resets_bus; }
    else if (code == 5) setup_seen = true;
    return 0;
}
int on_urb_sent(Urb* urb) {                                    // the stack frees the IN urb
    if (urb == serial_in_urb) serial_in_urb = 0;
    else if (urb == serial_stale) serial_stale = 0;
    else ++sent_count;
    return 1;
}
uint32_t serial_room() { return kSerialRing - (sring_head - sring_tail); }
void serial_copy_in(const Urb* u) {                            // interrupt, or main with the interrupt masked
    uint32_t h = sring_head;
    for (uint32_t i = 0; i != u->done; ++i, ++h) serial_ring[h % kSerialRing] = u->buf[i];
    sring_head = h;
    g_stats.serial_rx += u->done;
}
int serial_received(Urb* u) {
    if (held_head == held_tail && serial_room() >= u->done) { serial_copy_in(u); return 1; }   // the stack re-queues it
    if (held_head - held_tail >= kHeld) { ++g_stats.urbs_dropped; return 1; }   // cannot happen: kHeld > urbs
    held[held_head % kHeld] = u;
    held_head = held_head + 1;
    ++g_stats.serial_held;
    return 0;                                                  // ours until pump() recycles it
}
int on_recv_urb(Urb* urb) {
    const Endpoint* ep = reinterpret_cast<const Endpoint*>(urb->ep);
    if (ep && (ep->address & 0x0fu) == (kSerialOut & 0x0fu)) return serial_received(urb);
    const uint32_t h = ring_head;
    if (h - ring_tail >= kRing) { ++g_stats.urbs_dropped; return 0; }   // cannot happen: kOutUrbs < kRing
    ring[h % kRing] = urb;
    ring_head = h + 1;
    return 0;                                                  // ours until pump() recycles it
}
int on_recv_setup(Urb* ep0) {
    setup_seen = true;
    const uint8_t* s = ep0->setup;
    if ((s[0] & 0x60) != 0x20) return 1;                       // class requests only
    if (s[4] == kIfaceComm) switch (s[1]) {                    // CDC ACM by interface (wIndex)
    case 0x20: copy(line_coding, ep0->buf, 7); ++g_stats.line_codings; ep0->wanted = 0; return 0;   // data stage in
    case 0x21: copy(ep0->buf, line_coding, 7); ep0->wanted = 7; return 0;
    case 0x22: g_stats.control_lines = get16(s + 2); ep0->wanted = 0; return 0;
    case 0x23: ep0->wanted = 0; return 0;                      // SEND_BREAK: acknowledged
    default: return 1;
    }
    if (s[4] != kIfaceMtp) return 1;
    switch (s[1]) {
    case 0x64: cancel_flag = true; ep0->wanted = 0; return 0;  // Cancel (data stage already in)
    case 0x66: reset_flag = true; ep0->wanted = 0; return 0;   // Device Reset
    case 0x67:                                                 // Get Device Status
        put16(ep0->buf, 4);
        put16(ep0->buf + 2, g_handler && phase == Phase::command && src == Src::none ? uint32_t(g_handler->status()) :
                            (in_inflight && sent_count == in_inflight_seen) || phase != Phase::command ? 0x2019u : rc_ok);
        ep0->wanted = 4; return 0;
    }
    return 1;
}

// ---- main-loop side ---------------------------------------------------------------------
void free_ours() {                       // urbs pump() still holds (taken from the ring, not requeued)
    // Masked throughout (record 027): the stock heap has no lock and the USB interrupt mallocs, and the
    // interrupt may push to the ring while we read ring_head. Longest masked section of the driver:
    // at most kRing (8) free_urb calls, each one buffer free and one urb free in the stock heap.
    const uint32_t m = g_port->mask();
    for (uint32_t t = ring_tail; t != ring_head; ++t) g_port->free_urb(ring[t % kRing]);
    ring_tail = ring_head;
    for (uint32_t t = held_tail; t != held_head; ++t) g_port->free_urb(held[t % kHeld]);
    held_tail = held_head;
    if (serial_in_urb) { serial_stale = serial_in_urb; serial_in_urb = 0; }   // still the stack's (bus reset): not MTP's
    g_port->unmask(m);
    n_out = 0;
    in_inflight = false; src = Src::none;
    phase = Phase::command;
    rx_off = h_left = 0;
    if (g_handler) g_handler->cancel(true);            // the transfer is gone with the configuration
}
void requeue(Urb* urb) {                 // record 002 section 2: recycle (append to the stack's queue)
    const uint32_t m = g_port->mask();
    if (config_gen != main_gen) {        // review 029 finding 2: a flush came since this urb was taken; it
        g_port->free_urb(urb);           // belongs to the old configuration: ours to free, never re-queued
        g_port->unmask(m);
        return;
    }
    g_port->recycle(urb);
#if XYF_MTP_USB_KICK
    // Off by default (record 027): the two kicks run interrupt-path stack code from the main loop
    // (record 007 (a)); admitted separately (MTP_USB_KICK_CALLS), each needs the developer's explicit yes.
    Endpoint* ep = reinterpret_cast<Endpoint*>(urb->ep);
    if (ep && !ep->active) { g_port->kick(ep); ++g_stats.kicks;
        const uint32_t n = ep->address & 0x0f;
        if (ep->active && g_port->rx_parked(n)) { g_port->kick_rx(n); ++g_stats.kick_rx; } }
#endif
    g_port->unmask(m);
}
void configure() {
    const uint32_t m = g_port->mask();
    want_config = false; const uint32_t gen = config_gen; const uintptr_t dev = g_dev;
    g_port->unmask(m);
    // Queued urbs: freed by the stack's flush; held ones: freed by free_ours() just before this.
    n_out = 0; main_gen = gen; phase = Phase::command; in_inflight = false; src = Src::none;
    for (uint32_t i = 0; i != kOutUrbs; ++i) {
        const uint32_t mm = g_port->mask();
        Urb* u = g_port->alloc(dev, kBulkOut, kOutSize);
        g_port->unmask(mm);
        if (!u) break;
        out_urbs[n_out++] = u;
        requeue(u);
    }
    for (uint32_t i = 0; i != kSerialOutUrbs; ++i) {            // the serial's bulk OUT
        const uint32_t mm = g_port->mask();
        Urb* u = g_port->alloc(dev, kSerialOut, kSerialOutSize);
        g_port->unmask(mm);
        if (!u) break;
        serial_out[i] = u;
        requeue(u);
    }
    g_state = State::configured;
}
void respond(uint32_t code, uint32_t n = 0, uint32_t p0 = 0, uint32_t p1 = 0) {
    resp_code = code; resp_nparam = n; resp_param[0] = p0; resp_param[1] = p1;
    if (src == Src::none) phase = Phase::response;
}
uint32_t string_at(uint8_t* p, const char* s) {               // MTP string: count incl. NUL, UTF-16LE
    const uint32_t n = length(s);
    p[0] = uint8_t(n + 1);
    for (uint32_t i = 0; i != n; ++i) put16(p + 1 + 2 * i, uint8_t(s[i]));
    put16(p + 1 + 2 * n, 0);
    return 1 + 2 * (n + 1);
}
uint32_t array16_at(uint8_t* p, const uint16_t* v, uint32_t n) {
    put32(p, n); for (uint32_t i = 0; i != n; ++i) put16(p + 4 + 2 * i, v[i]); return 4 + 2 * n;
}
void device_info() {
    static const uint16_t ops[] = {op_get_device_info, op_open_session, op_close_session,
                                   uint16_t(op_test_sink), uint16_t(op_test_source), uint16_t(op_test_loopback)};
    uint8_t* p = tx_buf + 12;
    put16(p, 100); put32(p + 2, 6); put16(p + 6, 100); p += 8;    // standard 1.00, MTP extension 1.00
    p += string_at(p, "");
    put16(p, 0); p += 2;                                          // functional mode
    p += array16_at(p, ops, sizeof(ops) / 2);
    p += array16_at(p, 0, 0); p += array16_at(p, 0, 0);           // events, device properties
    p += array16_at(p, 0, 0); p += array16_at(p, 0, 0);           // capture, playback formats
    p += string_at(p, kMaker); p += string_at(p, kProduct); p += string_at(p, "3.2"); p += string_at(p, kSerial);
    const uint32_t len = uint32_t(p - tx_buf);
    put32(tx_buf, len); put16(tx_buf + 4, 2); put16(tx_buf + 6, op_get_device_info); put32(tx_buf + 8, cur_tx);
    src = Src::buffer; src_data = tx_buf; src_len = len; src_off = 0; phase = Phase::data_in;
}
void command(const uint8_t* c, uint32_t len) {
    if (len < 12 || get16(c + 4) != 1) return;                    // not a command: ignore
    const uint32_t np = (len - 12) / 4, p0 = np > 0 ? get32(c + 12) : 0;
    cur_op = get16(c + 6); cur_tx = get32(c + 8);
    ++g_stats.mtp_commands;
    if (!marked_command) { marked_command = true; mark(mark_first_command); }
    switch (cur_op) {
    case op_open_session:
        if (g_session_open) return respond(rc_session_already_open, 1, session_id);
        g_session_open = true; session_id = p0; ++g_stats.sessions_opened; return respond(rc_ok);
    case op_close_session:
        if (!g_session_open) return respond(rc_session_not_open);
        g_session_open = false; ++g_stats.sessions_closed; return respond(rc_ok);
    case op_get_device_info: ++g_stats.device_infos; return device_info();
    case op_test_sink: case op_test_loopback:
        g_stats.sink_bytes = 0; g_stats.sink_sum = 0; out_left = 0; out_seen = 0; loop_len = 0;
        if (cur_op == op_test_sink && g_sink_hook) g_sink_hook(0, 0, true);
        phase = Phase::data_out; return;
    case op_test_source: {
        src = Src::pattern; src_len = 12 + p0; src_off = 0; phase = Phase::data_in;
        put32(tx_buf, src_len); put16(tx_buf + 4, 2); put16(tx_buf + 6, cur_op); put32(tx_buf + 8, cur_tx);
        return;
    }
    }
    respond(rc_operation_not_supported);
}
void data_out(const uint8_t* d, uint32_t len) {
    uint32_t skip = 0;
    if (!out_seen) {                                              // first urb: the data container header
        if (len < 12 || get16(d + 4) != 2) { phase = Phase::command; return respond(rc_general_error); }
        out_left = get32(d) - 12; skip = 12;
    }
    out_seen += len;
    uint32_t body = len - skip;
    if (body > out_left) body = out_left;
    const uint8_t* b = d + skip;
    if (cur_op == op_test_loopback) {
        const uint32_t room = kLoopMax - loop_len, n = body < room ? body : room;
        copy(loop_buf + 12 + loop_len, b, n); loop_len += n;
    } else {
        uint32_t s = g_stats.sink_sum;
        for (uint32_t i = 0; i != body; ++i) s += b[i];
        g_stats.sink_sum = s;
        if (g_sink_hook && body) g_sink_hook(b, body, false);
    }
    g_stats.sink_bytes += body; out_left -= body;
    if (out_left) return;
    if (cur_op == op_test_loopback) {
        put32(loop_buf, 12 + loop_len); put16(loop_buf + 4, 2); put16(loop_buf + 6, cur_op); put32(loop_buf + 8, cur_tx);
        src = Src::buffer; src_data = loop_buf; src_len = 12 + loop_len; src_off = 0; phase = Phase::data_in;
        resp_code = rc_ok; resp_nparam = 1; resp_param[0] = loop_len;
        return;
    }
    phase = Phase::command;
    respond(rc_ok, 2, uint32_t(g_stats.sink_bytes), g_stats.sink_sum);
}
bool test_command(const uint8_t* c, uint32_t len) {
    if (len < 12 || get16(c + 4) != 1) return false;
    const uint32_t op = get16(c + 6);
    return op == op_test_sink || op == op_test_source || op == op_test_loopback;
}
// true: the urb is done (requeue it); false: the handler said not now (rx_off keeps the progress).
bool received(Urb* u) {
    const uint32_t len = u->done;
    if (phase == Phase::data_out) { data_out(u->buf, len); return true; }
    if (!g_handler) { if (len && phase == Phase::command) command(u->buf, len); return true; }  // a ZLP: ignored
    if (!len) return true;                                        // a ZLP after a 512-multiple data phase
    if (!rx_off && phase == Phase::command && src == Src::none && g_handler->idle() && test_command(u->buf, len)) {
        command(u->buf, len);                                     // the driver's test operations (the sink)
        return true;
    }
    if (!rx_off && g_handler->idle() && len >= 12 && get16(u->buf + 4) == 1) {   // a command: the MTP mark
        ++g_stats.mtp_commands;
        if (!marked_command) { marked_command = true; mark(mark_first_command); }
    }
    while (rx_off != len) {
        uint32_t n = len - rx_off;
        if (n > kHandlerPiece) n = kHandlerPiece;
        if (!g_handler->deliver(u->buf + rx_off, n)) { ++g_stats.handler_held; return false; }
        rx_off += n; g_stats.handler_in += n;
    }
    rx_off = 0;
    return true;
}
// The handler's next IN piece: whole packets, or the rest of the container (ZLP when it is a multiple of 512).
void handler_in() {
    const uint32_t pending = g_handler->pending();
    if (!pending) return;
    if (!h_left) { h_left = g_handler->length(); if (h_left < 12) { h_left = 0; return; } }
    uint32_t n = pending;
    bool last = false;
    if (n >= h_left) { n = h_left; last = true; }
    else { n &= ~(uint32_t(kMaxPacket) - 1); if (!n) return; }
    if (n > kInChunk) { n = kInChunk; last = false; }           // kInChunk: whole packets
    const uint32_t m = g_port->mask();
    Urb* u = g_port->alloc(g_dev, kBulkIn, n);
    g_port->unmask(m);
    if (!u) return;
    const uint32_t got = g_handler->take(u->buf, n);             // n <= pending: all of it
    u->wanted = got; u->zlp = last && got % kMaxPacket == 0 ? 1 : 0;
    const uint32_t mm = g_port->mask();
    in_inflight_seen = sent_count;
    const int r = got == n ? g_port->submit_in(u) : -1;
    if (r) g_port->free_urb(u);
    g_port->unmask(mm);
    if (r) { h_left = 0; return; }                               // lost: the host's next command recovers
    in_inflight = true; ++g_stats.in_sent; ++g_stats.handler_urbs_in; g_stats.handler_out += got;
    h_left -= n;
}
bool send(const uint8_t* data, uint32_t len, bool last, uint32_t pat = 0) {   // data 0: source container from offset pat
    const uint32_t m = g_port->mask();
    Urb* u = g_port->alloc(g_dev, kBulkIn, len ? len : 1);
    g_port->unmask(m);
    if (!u) return false;
    if (data) copy(u->buf, data, len);
    else for (uint32_t i = 0; i != len; ++i) { const uint32_t g = pat + i; u->buf[i] = g < 12 ? tx_buf[g] : pattern(g - 12); }
    u->wanted = len; u->zlp = last && len % kMaxPacket == 0 ? 1 : 0;
    const uint32_t mm = g_port->mask();
    in_inflight_seen = sent_count;
    const int r = g_port->submit_in(u);
    if (r) g_port->free_urb(u);                                   // not queued: ours; heap call masked
    g_port->unmask(mm);
    if (r) return false;
    in_inflight = true; ++g_stats.in_sent;
    return true;
}
void in_step() {
    if (in_inflight) { if (sent_count == in_inflight_seen) return; in_inflight = false; }
    if (src != Src::none) {
        uint32_t n = src_len - src_off; if (n > kInChunk) n = kInChunk;
        const bool last = src_off + n == src_len;
        bool ok;
        if (src == Src::buffer) ok = send(src_data + src_off, n, last);
        else ok = send(0, n, last, src_off);                     // pattern: header from tx_buf, then data
        if (!ok) return;                                          // try again next pass
        if (src == Src::pattern) g_stats.source_bytes += n;
        src_off += n;
        if (last) { src = Src::none; if (phase == Phase::data_in) phase = Phase::response;
                    if (cur_op != op_test_loopback) { resp_code = rc_ok; resp_nparam = 0; } }
        return;
    }
    if (phase == Phase::response) {
        uint8_t r[20]; const uint32_t len = 12 + 4 * resp_nparam;
        put32(r, len); put16(r + 4, 3); put16(r + 6, resp_code); put32(r + 8, cur_tx);
        for (uint32_t i = 0; i != resp_nparam; ++i) put32(r + 12 + 4 * i, resp_param[i]);
        if (send(r, len, true)) phase = Phase::command;
        return;
    }
    if (g_handler && phase == Phase::command) handler_in();
}
}  // namespace

bool begin(const Port& port) {
    if (g_state != State::off) return false;
    g_port = &port;
    const uint32_t written = g_stats.marks_written, failed = g_stats.marks_failed;   // set_mark_hook's
    g_stats = Stats();
    g_stats.marks_written = written; g_stats.marks_failed = failed;
    ring_head = ring_tail = 0; sent_count = 0; want_config = cancel_flag = reset_flag = false;
    sring_head = sring_tail = 0; held_head = held_tail = 0; serial_in_urb = serial_stale = 0;
    copy(line_coding, kLineCoding, 7);
    configured_bus = suspended = setup_seen = false;
    marked_setup = marked_config = marked_command = false;
    n_out = 0; g_session_open = false; phase = Phase::command; src = Src::none; in_inflight = false;
    if (port.register_driver(&g_driver)) { g_port = 0; mark(mark_register_refused); return false; }
    mark(mark_registered);
    mark(mark_before_start);
    port.start(kCompositeFlags);          // the boot's only start (its own teardown finds nothing running)
    g_state = State::started;
    mark(mark_after_start);
    return true;
}

void leave(bool) {
    if (g_state == State::off) return;
    const Port& port = *g_port;
    port.stop(kStopKeepTimer);            // teardown through our driver; flags 0x200: no class unregister
    port.unregister_driver(&g_driver);
    free_ours();                          // the urbs we hold (taken from the ring, not requeued)
    g_state = State::off;
    g_port = 0;
}

uint32_t serial_available() { return sring_head - sring_tail; }
uint32_t serial_read(uint8_t* out, uint32_t n) {
    const uint32_t have = sring_head - sring_tail;
    if (n > have) n = have;
    uint32_t t = sring_tail;
    for (uint32_t i = 0; i != n; ++i, ++t) out[i] = serial_ring[t % kSerialRing];
    sring_tail = t;
    return n;
}
int serial_write(const uint8_t* data, uint32_t n) {
    if (g_state != State::configured || serial_in_urb || n > kSerialInMax) { ++g_stats.serial_busy; return -1; }
    const uint32_t m = g_port->mask();
    Urb* u = g_port->alloc(g_dev, kSerialIn, n ? n : 1);
    g_port->unmask(m);
    if (!u) { ++g_stats.serial_busy; return -1; }
    copy(u->buf, data, n);
    u->wanted = n; u->zlp = n % kMaxPacket == 0 ? 1 : 0;
    const uint32_t mm = g_port->mask();
    serial_in_urb = u;
    const int r = g_port->submit_in(u);
    if (r) { serial_in_urb = 0; g_port->free_urb(u); }      // not queued: ours; heap call masked
    g_port->unmask(mm);
    if (r) return -1;
    g_stats.serial_tx += n;
    return int(n);
}
bool session_open() { return g_session_open; }

uint32_t pump() {
    if (g_state == State::off) return 0;
    ++g_stats.passes;
    if (reset_flag || cancel_flag) {
        const bool reset = reset_flag;
        if (reset_flag) { ++g_stats.resets; g_session_open = false; } else ++g_stats.cancels;
        reset_flag = cancel_flag = false; src = Src::none; phase = Phase::command;
        if (g_handler) {                                          // what the ring holds belongs to the old transfer
            g_handler->cancel(reset);
            if (g_state == State::configured)
                while (ring_tail != ring_head) { Urb* u = ring[ring_tail % kRing]; ring_tail = ring_tail + 1; requeue(u); }
            rx_off = h_left = 0;
        }
    }
    if (setup_seen && !marked_setup) { marked_setup = true; mark(mark_first_setup); }
    if (config_gen != main_gen || want_config) {
        // Deconfigured or reconfigured: the stack forgot our queued urbs; free the held ones.
        if (g_state == State::configured) ++g_stats.deconfigures;
        free_ours();
        g_state = State::started;
        main_gen = config_gen;
        if (want_config) {
            configure();
            if (!marked_config) { marked_config = true; mark(mark_configured); }
        }
        return 0;
    }
    for (;;) {                                                     // held serial urbs, in order, as room frees
        const uint32_t m = g_port->mask();
        if (held_tail == held_head || serial_room() < held[held_tail % kHeld]->done) { g_port->unmask(m); break; }
        Urb* u = held[held_tail % kHeld];
        serial_copy_in(u);
        held_tail = held_tail + 1;
        g_port->unmask(m);
        requeue(u);
    }
    uint32_t urbs = 0, bytes = 0;
    while (urbs != kOutUrbs && ring_tail != ring_head) {
        Urb* u = ring[ring_tail % kRing];
        if (!received(u)) break;                                   // held for the handler
        bytes += u->done;
        ring_tail = ring_tail + 1;
        ++urbs; ++g_stats.urbs_received;
        requeue(u);
    }
    in_step();
    if (urbs > g_stats.longest_pass_urbs) g_stats.longest_pass_urbs = urbs;
    if (bytes > g_stats.longest_pass_bytes) g_stats.longest_pass_bytes = bytes;
    return urbs;
}

void set_sink_hook(SinkHook hook) { g_sink_hook = hook; }
void set_handler(const Handler* handler) { g_handler = handler; rx_off = h_left = 0; }
void set_mark_hook(MarkHook hook) { g_mark_hook = hook; marks_off = false; g_stats.marks_written = g_stats.marks_failed = 0; }
void mark(uint32_t step) {
    if (!g_mark_hook || marks_off) return;
    if (g_mark_hook(step)) ++g_stats.marks_written;
    else { marks_off = true; ++g_stats.marks_failed; }   // a failed mark never stops the probe
}
const char* mark_name(uint32_t step) { return step && step < mark_count ? kMarkNames[step] : kMarkNames[0]; }
State state() { return g_state; }
const Stats& stats() { return g_stats; }
const Driver* driver() { return &g_driver; }

}}

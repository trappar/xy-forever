#ifndef XYF_MTP_USB_HPP
#define XYF_MTP_USB_HPP
// Our own USB function driver on the stock USB stack (smxUSBD).
// The composite: one composite device, started once per boot and never stopped inside it:
// device class EF/02/01 with one IAD over interfaces 0..1; interface 0 CDC comm (notify IN 0x81),
// interface 1 CDC data (bulk OUT 0x08, bulk IN 0x8b), interface 2 MTP (still image 06/01/01: bulk IN
// 0x89, bulk OUT 0x0a, interrupt IN 0x83). Registered before the boot's only start, start(0x200): bit 9
// registers no stock class but makes the stack build the CDC functional descriptors (from our extras)
// and the IAD (from the 0x18-byte config record).
// MTP alone (#21, USB mode "MTP"): the same driver, start and endpoints with one interface, MTP as interface 0 (as
// the stock MTP driver 88a12534: device class 00/00/00, no IAD, no CDC extras, so bit 9's CDC fill has none to
// read); no serial interface: the host tool and the web app cannot reach the device.
// The stock callbacks (USB interrupt) only queue urbs into rings (serial: copy into a RAM ring); all
// other work runs in B's main loop through pump(). MTP: OpenSession / CloseSession / GetDeviceInfo and
// three test operations: sink (count N bytes), source (send N pattern bytes) and loopback (echo up to
// kLoopMax bytes). Serial: serial_available / serial_read / serial_write for the USB service's frames.
// Stock entry points go through Port, so the host test (mtp_usb_test.cpp) mocks the stack;
// mtp_usb::stock_port() binds them to the 1.2.1 addresses.
#include <stdint.h>

namespace xyf { namespace mtp_usb {

// Stock structures. Word fields are pointer-sized so the layout matches the
// 32-bit target; on the host the mock builds them itself. [U] = byte layout inside the word not
// read from the listing yet: confirm before the device.
struct Urb {                                   // 0x30, zeroed stock heap
    uintptr_t link0, link1, ep, dev;           // +8 endpoint object, +0xc device
    uint8_t setup[8];                          // +0x10 (EP0 urb: the setup packet)
    uint8_t* buf;                              // +0x18
    uint32_t size, wanted, done, status, zlp;  // +0x1c .. +0x2c
};
struct Endpoint {                              // 0x18, [0x11803cc8 + 4n]
    uint32_t address; uintptr_t queue, r8, active; uint16_t r10, max_packet; uint32_t r14;
};
struct Ops {                                   // stack -> driver; return 0 = driver keeps the urb
    int (*event)(uintptr_t dev, uint32_t code);
    int (*urb_sent)(Urb* urb);
    int (*recv_urb)(Urb* urb);
    int (*recv_setup)(Urb* ep0);
};
struct DevInfo { uint8_t cls, subclass, protocol, pad; uint16_t vid, pid; const char *manufacturer, *product, *serial; };
struct EpDesc { uint8_t address, attributes; uint16_t max_packet; uint8_t interval, dir; uint16_t pad; };
struct AltDesc { const char* name; uint32_t alt; uint32_t n_extra; const void* extra; uint32_t n_eps; const EpDesc* eps; };
// IfaceDesc / ConfigDesc byte layout: as the stock MTP records (driver 88a12534: config 'MTP Config' c0 fa,
// interface 'MTP Data Intf' 06 01 01) and the CDC ones (driver 88a1349c), read from the 1.2.1 image.
struct IfaceDesc { const char* name; uint8_t cls, subclass, protocol, pad; uint32_t n_alts; const AltDesc* alts; };
// ConfigDesc is 0x18: +0x10 n_iads (at most 4, 88a0aa0e), +0x14 the IAD array (88a0aa34);
// stock records end in two zero words (CDC 0x88a13460, MTP 0x88a12504). 186-005's 0x10 record let the stack read
// the next record's name pointer as n_iads: the descriptor build failed and attach 88a08d52 returned -1.
// The other records match the stock ones word for word (DevInfo 0x14: 0x88a1251c; Driver 0x20: 0x88a12534;
// IfaceDesc 0x10: 0x88a12674, 0x88a13530; AltDesc 0x18: 0x88a1265c, 0x88a13500; EpDesc 8: 0x88a1262c, 0x88a134bc).
struct ConfigDesc { const char* name; uint8_t attributes, max_power; uint16_t pad; uint32_t n_ifaces; const IfaceDesc* ifaces;
                    uint32_t n_iads; const struct Iad* iads; };
// Composite (88a0aa34..98): one IAD record, 0xc: name (iFunction), class, subclass, protocol,
// first interface, interface count. CDC extras (88a0a22a, filled by 88a0a3e0 only with start bit 9, for
// interfaces 0..1): 0x3c-byte records, +0 type 0x24, +1 subtype, +4/+5 the subtype's fields (header 0: none,
// bcdCDC 0x0200 by the stack; call management 1: caps, data interface; ACM 2: caps; union 6: master, slave).
// The stock CDC's four are at 0x88a13370 (header, union, call management, ACM).
struct Iad { const char* name; uint8_t cls, subclass, protocol, first, count, pad[3]; };
struct CdcExtra { uint8_t type, subtype, pad[2]; uint8_t field[0x38]; };
struct Driver { const char* name; const Ops* ops; const DevInfo* devinfo; const ConfigDesc* configs;
                uintptr_t f10, f14; uint32_t n_configs; uintptr_t f1c; };   // writable: the stack fills +0x10/+0x14/+0x1c

#if defined(__bfin__)
static_assert(sizeof(Urb) == 0x30 && sizeof(Endpoint) == 0x18 && sizeof(Ops) == 0x10 && sizeof(DevInfo) == 0x14, "stock USB core structure sizes");
static_assert(sizeof(EpDesc) == 8 && sizeof(AltDesc) == 0x18 && sizeof(IfaceDesc) == 0x10 && sizeof(ConfigDesc) == 0x18, "stock USB descriptor sizes");
static_assert(__builtin_offsetof(ConfigDesc, n_iads) == 0x10 && __builtin_offsetof(ConfigDesc, iads) == 0x14, "stock USB IAD array offsets");
static_assert(sizeof(Iad) == 0xc && __builtin_offsetof(Iad, first) == 7 && sizeof(CdcExtra) == 0x3c, "stock USB IAD and CDC extra layout");
static_assert(sizeof(Driver) == 0x20 && __builtin_offsetof(Urb, buf) == 0x18 && __builtin_offsetof(Urb, zlp) == 0x2c, "stock USB driver and URB layout");
static_assert(__builtin_offsetof(Endpoint, active) == 0xc && __builtin_offsetof(Endpoint, max_packet) == 0x12, "stock USB endpoint offsets");
#endif
#ifndef XYF_MTP_USB_KICK
#define XYF_MTP_USB_KICK 0                     // kick / kick_rx (88a094aa / 88a0ba08): off, separate approval required
#endif
struct Port {
    int (*register_driver)(Driver*);                          // 88a09d5c: 0 / -1
    void (*unregister_driver)(Driver*);                       // 88a09d44
    void (*start)(uint32_t flags);                            // 88a35044: kCompositeFlags (once per boot)
    void (*stop)(uint32_t arg);                               // 88a34fec: always kStopKeepTimer
    Urb* (*alloc)(uintptr_t dev, uint32_t ep, uint32_t len);  // 88a092ee
    int (*submit_in)(Urb*);                                   // 88a08f24 (IN only)
    void (*recycle)(Urb*);                                    // 88a08f08 (OUT urb back to its queue)
    void (*free_urb)(Urb*);                                   // 88a09418
    void (*kick)(Endpoint*);                                  // 88a094aa (only with XYF_MTP_USB_KICK; else 0)
    void (*kick_rx)(uint32_t ep_number);                      // 88a0ba08 (only when rx_parked; same option)
    bool (*rx_parked)(uint32_t ep_number);                    // MUSB RxCSR bit 0 (W[0x200d0106 + 0x10n])
    uint32_t (*mask)();                                       // USB interrupt off; returns the old state
    void (*unmask)(uint32_t);
};

enum : uint32_t {
    // both switches stop with 0, as start's own stop does (88a3505e): teardown only, GP
    // timer 7 (the stack's tick, which the controller init's 5-tick wait needs) stays open. stop(1) closes it.
    kStopKeepTimer = 0, kSerialFlags = 0x400,  // start(0x400): the stock CDC serial service (menu_start_usb)
    // Bit 9 only (no stock USB class): no stock class.
    kCompositeFlags = 0x200,
    kBulkIn = 0x89, kBulkOut = 0x0a, kIntIn = 0x83, kMaxPacket = 512,
    kNotifyIn = 0x81, kSerialOut = 0x08, kSerialIn = 0x8b,   // CDC: interface 0 (notify), 1 (data)
    kIfaceComm = 0, kIfaceData = 1, kIfaceMtp = 2, kIfaceMtpAlone = 0,
    kSerialOutUrbs = 2, kSerialOutSize = 512,  // one packet per urb: each completes at once (no ZLP question)
    kSerialRing = 8192, kSerialInMax = 16384,  // RAM ring (power of two); one IN urb at most this long
    kOutUrbs = 4, kOutSize = 16384,           // K x S; S a multiple of 512
    kInChunk = 16384, kLoopMax = 4096,
    op_get_device_info = 0x1001, op_open_session = 0x1002, op_close_session = 0x1003,
    // Vendor codes no client uses (the old 0x9801..03 are MTP's GetObjectPropsSupported etc.: macOS clients send them)
    op_test_sink = 0x9f01, op_test_source = 0x9f02, op_test_loopback = 0x9f03,
    rc_ok = 0x2001, rc_general_error = 0x2002, rc_session_not_open = 0x2003,
    rc_operation_not_supported = 0x2005, rc_session_already_open = 0x201e, rc_invalid_parameter = 0x201d,
};
enum class State : uint32_t { off, started, configured };
// 88a0ba08 takes the control-endpoint (EP0) path for 0: the binding refuses 0 and every endpoint but
// our two bulk OUTs (the serial's 0x08 too).
inline bool rx_service_allowed(uint32_t ep_number) { return ep_number == (kBulkOut & 0x0fu) || ep_number == (kSerialOut & 0x0fu); }

struct Stats {
    uint32_t passes, longest_pass_urbs, longest_pass_bytes, urbs_received, urbs_dropped;
    uint32_t in_sent, kicks, kick_rx, deconfigures, resets, cancels, resets_bus;
    uint64_t sink_bytes, source_bytes;
    uint32_t sink_sum;                         // byte sum of the last sink's data
    uint32_t device_infos, sessions_opened, sessions_closed;   // what the probe's host did
    uint32_t marks_written, marks_failed;      // 186-005: progress marks (mark hook) written, failed (<= 1)
    uint32_t mtp_commands;                     // MTP commands received (the screen's MTP mark)
    uint32_t serial_rx, serial_tx, serial_held, serial_busy;   // bytes in / out, urbs held (ring full), writes refused busy
    uint32_t line_codings, control_lines;      // SET_LINE_CODING count; last SET_CONTROL_LINE_STATE wValue
    uint32_t handler_in, handler_out, handler_held, handler_urbs_in;   // bytes to / from the handler, passes it said not now, IN urbs
};
// Called from pump() (main loop) for the test sink: first = true when a sink starts (no data), then
// once per received piece of its data, in order. The probe hashes them (SHA-256 reported afterwards).
typedef void (*SinkHook)(const uint8_t* data, uint32_t n, bool first);
void set_sink_hook(SinkHook hook);

// 186-005: progress marks that survive a power cycle. The hook (usb_service: a whole rewrite of a small file
// through A's file engine, each request bounded by the file client) runs in the main loop only, before
// each step that could freeze; false = the mark was not written: no further mark is tried until the hook
// is set again (one failed request costs at most its bound once). Interrupt-side facts (first SETUP)
// are marked by the next pump() pass.
// One boot's steps (usb_service writes 1-2, 3-7 here, 10-11 and 13-14 usb_service, 8-9 and 12 pump()).
enum Mark : uint32_t {
    mark_flag_found = 1, mark_flag_deleted, mark_registered, mark_before_start, mark_after_start,
    mark_attach_ok, mark_attach_failed,        // the stack's init flag [0x88a137a0] after start (88a13714)
    mark_first_setup, mark_configured, mark_first_serial_byte, mark_first_hello, mark_first_command,
    mark_fallback_restart, mark_register_refused,
    mark_count
};
const char* mark_name(uint32_t step);         // short name ("before-start-0"), "?" outside 1..mark_count-1
typedef bool (*MarkHook)(uint32_t step);
void set_mark_hook(MarkHook hook);             // also re-enables marks after a failed one
void mark(uint32_t step);                      // the hook, unless none is set or one failed

// Register ours, start(0x200): the boot's only start (no stop before: nothing runs yet). false: the
// slot was taken (register refused): nothing started; the caller starts the stock serial service instead.
// serial false: MTP alone (no CDC interfaces; serial_write refuses, serial_available stays 0).
bool begin(const Port& port, bool serial = true);
// Before a restart or a boot (menu_release): stop(0), unregister ours, free the urbs we hold. marked: marks
// around it (none now: the restart's own mark comes first).
void leave(bool marked = false);
// Serial (CDC data, main loop only). available: bytes in the ring; read: up to n of them; write: one IN
// urb of n (<= kSerialInMax) bytes, ZLP when n is a multiple of 512; -1 while one is in flight or before
// SET_CONFIGURATION (the caller retries, bounded), else n.
uint32_t serial_available();
uint32_t serial_read(uint8_t* out, uint32_t n);
int serial_write(const uint8_t* data, uint32_t n);
bool session_open();                           // an MTP session is open
// The MTP interface's handler (usb_service: the responder through mtp_session). With one set, every
// bulk-OUT transfer goes to deliver in pieces of at most kHandlerPiece bytes (a zero-length packet is dropped),
// except a test command (sink, source, loopback) that arrives while idle() (the driver serves it, as with no handler);
// deliver false = not now: the urb stays held (the host gets NAKs) and the rest is offered next pass. IN: the
// driver takes the handler's bytes container by container (length: the container at the head, 0 = none yet),
// in urbs of whole 512-byte packets except a container's last, ZLP when the container is a multiple of 512.
// cancel(reset): Cancel (false), Device Reset, bus reset or a new configuration (true). status: Get Device
// Status's code. All from the main loop (pump) except status (the interrupt).
struct Handler {
    bool (*deliver)(const uint8_t* data, uint32_t n);
    uint32_t (*pending)();
    uint32_t (*length)();
    uint32_t (*take)(uint8_t* out, uint32_t n);
    bool (*idle)();
    void (*cancel)(bool reset);
    uint16_t (*status)();
};
enum : uint32_t { kHandlerPiece = 4096 };
void set_handler(const Handler* handler);
uint32_t pump();                               // one bounded main-loop pass; returns urbs handled
State state();
const Stats& stats();
const Driver* driver();
const Port& stock_port();                      // device binding

}}
#endif

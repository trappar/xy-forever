#include "xyf_clock.hpp"
#include "xyf_state_write.hpp"
#include "usb_service.hpp"
#include "usb_spans.hpp"
#if defined(XYF_MENU_ON_FAILURE)
#include "file_boot.hpp"
#endif
#if defined(XYF_SERVICE_SCREEN)
#include "key_ui.hpp"
#endif
#if defined(XYF_SERVICE_ON_MARKER_ONLY)
#include "xyf_boot.hpp"
#endif
#include "xyf_crc32.hpp"
#include "knob_input.hpp"      // the knob reader's counters (diag.status block KNB1)
#if defined(XYF_MTP_SESSION)
#include "mtp_session.hpp"          // the MTP session (simulator only so far)
#endif
#if defined(XYF_MTP_USB)
#include "mtp_usb.hpp"              // the MTP USB probe (test build xyf-mtp-usb only)
#include "mtp_usb_spans.hpp"
#include "mtp_spans.hpp"
#include "mtp_usb_gate.hpp"         // which check refuses a probe start, and why
#endif
// XY Forever USB service (see usb_service.hpp). Stock addresses: the
// authenticated 1.2.1 bootloader, mapped offline (docs/HARDWARE.md, Boot partition and USB).
namespace xy_forever { namespace usb_service {
#if defined(__bfin__)
// Initialized data, not BSS: the cold link has no zero-initialization.
Result result __attribute__((section(".data"))) = {magic};
#endif

uint32_t identity() {
    uint32_t bits = 0;
    for (uint32_t i = 0; i != xyf::usb_spans::count; ++i) {
        const uint32_t* s = xyf::usb_spans::table[i];
        const volatile uint8_t* p = reinterpret_cast<const volatile uint8_t*>(s[0]);
        if (xyf::crc::update_volatile(0, p, s[1]) != s[2]) bits |= 1u << (i & 31u);
    }
    return bits;
}

bool has_descriptor(const uint8_t* data, uint32_t size) {
    for (uint32_t i = 0; i + 8 <= size; i += 4)
        if (xyf::usbp::get32(data + i) == descriptor_magic && xyf::usbp::get32(data + i + 4) == 1) return true;
    return false;
}

bool accept_image(bool uninstall, bool descriptor, uint32_t size, const uint8_t* sha256) {
    if (!uninstall) return descriptor;
    if (size != stock_os_bytes || !sha256) return false;
    uint32_t d = 0;
    for (uint32_t i = 0; i != 32; ++i) d |= uint32_t(sha256[i] ^ stock_os_sha256[i]);
    return !d;
}

bool header_matches(const uint8_t* sector, uint32_t size, uint32_t crc) {
    return xyf::usbp::get32(sector) >= 2 && xyf::usbp::get32(sector + 4) == size &&
           xyf::usbp::get32(sector + 8) == crc && sector[12] != 0 && sector[13] <= 1;
}
} }

#if defined(__bfin__)
#include "menu_link.hpp"
#include "storage_server.hpp"
#include "file_client.hpp"
#include "../native/hash.hpp"
#include "../native/ldr.hpp"
#include "../native/mod_job.hpp"
#include "cold_layout.hpp"
#include "stack_mark.hpp"         // live stack high-water mark in STATUS
#include "xyf_store.hpp"
#include "xyf_stage2.hpp"
#include "xyf_save_log.hpp"           // #27: the save-time log
#if defined(XYF_CONTENT_SWITCH)
#include "xyf_content_job.hpp"
#include "xyf_config.hpp"           // the saved configuration (the config job)
#include "xyf_settings.hpp"         // #21: settings.txt (through the config job), the confirm gate
#endif
#if defined(XYF_TFW_CHECK)
#include "otp_keys.hpp"
#include "../native/tfw_check.hpp"
#endif
extern "C" { extern const xyf_core::Policy* volatile xy_forever_cold_policy; }   // cold.cpp
namespace {
using namespace xy_forever;
using namespace xy_forever::usb_service;
namespace P = xyf::usbp;
typedef unsigned int U32;
template<U32 A, class R, class... Args> __attribute__((noinline, noclone)) R stock(Args... args) {
    return reinterpret_cast<R(*)(Args...)>(A)(args...);
}
volatile U32& word(U32 address) { return *reinterpret_cast<volatile U32*>(address); }
volatile uint8_t& byte(U32 address) { return *reinterpret_cast<volatile uint8_t*>(address); }
U32 half(U32 address) { return *reinterpret_cast<volatile uint16_t*>(address); }
U32 cycles_div1024() {
    U32 low, high;
    __asm__ volatile("%0 = CYCLES; %1 = CYCLES2;" : "=d"(low), "=d"(high));
    return (low >> 10) | (high << 22);
}
volatile U32 start_ms __attribute__((section(".data"))) = 0;   // xyf_clock: wrap-safe
U32 now() { return xyf::clock::ms() - start_ms; }
void lines(U32 address, U32 bytes, bool invalidate) {
    for (U32 a = address & ~31u; a < address + bytes; a += 32) {
        if (invalidate) __asm__ volatile("FLUSHINV [%0];" : : "a"(a) : "memory");
        else __asm__ volatile("FLUSH [%0];" : : "a"(a) : "memory");
    }
    __asm__ volatile("SSYNC;" : : : "memory");
}

// Stock entries (all inside usb_spans.hpp or the peer/server spans).
void usb_start() { stock<0x88a35044u, void>(0x400u); }               // smxUSBD, CDC "Serial" only
void usb_stop() { stock<0x88a34fecu, void>(1u); }                    // release + PHY down (stock boot path)
#if defined(XYF_MTP_USB)
// The serial service's transport is our composite's CDC while it is up (composite_on), else the stock
// CDC's four calls exactly as in the product. Writes on the composite go out as one urb of up to
// kSerialInMax bytes (write_chunk), not 512.
volatile U32 composite_on __attribute__((section(".data"))) = 0;   // usb_begin sets it, menu_release clears it
U32 cdc_available() {
    if (composite_on) return xyf::mtp_usb::serial_available();
    const int n = stock<0x88a13328u, int>(0u); return n > 0 ? U32(n) : 0;
}
U32 cdc_read(uint8_t* out, U32 n) {
    if (composite_on) return xyf::mtp_usb::serial_read(out, n);
    const int got = stock<0x88a1334au, int>(0u, out, n); return got > 0 ? U32(got) : 0;
}
int cdc_write(const uint8_t* data, U32 n) {
    if (composite_on) return xyf::mtp_usb::serial_write(data, n);
    return stock<0x88a132f6u, int>(0u, data, n);
}
U32 write_chunk() { return composite_on ? U32(xyf::mtp_usb::kSerialInMax) : 512u; }
#else
U32 cdc_available() { const int n = stock<0x88a13328u, int>(0u); return n > 0 ? U32(n) : 0; }
U32 cdc_read(uint8_t* out, U32 n) { const int got = stock<0x88a1334au, int>(0u, out, n); return got > 0 ? U32(got) : 0; }
int cdc_write(const uint8_t* data, U32 n) { return stock<0x88a132f6u, int>(0u, data, n); }
#endif
// Test 032: the stock bootloader's USB-serial transport (L1 11a03b6e) leaves its
// receive callback 11a03e04 registered at [0x880734c0]; the CDC receive
// completion (88a130f2) calls it for every packet and it drains the ring into
// the stock transport buffer, so cdc_read never saw a byte. 88a12c7c is the
// stock setter (a single store); cleared while serving, restored afterwards.
U32 cdc_callback() { return word(0x880734c0u); }
void set_cdc_callback(U32 callback) { stock<0x88a12c7cu, void>(0u, callback); }
U32 boot_capacity() { return stock<0x889f65f0u, U32>(); }               // generic op 5 -> a's 88a086c0
U32 write_boot(const uint8_t* data, U32 size) { return stock<0x88a06a7eu, U32>(data, size, 0u); }
U32 select_partition(U32 p) { return stock<0x889f683cu, U32>(p); }     // 0 = selected (88a08710 result)
U32 read_sector0(U32 sector, uint8_t* out) { return stock<0x889f6a6au, U32>(sector, out); }   // 0 = ok
U32 read_sectors(U32 sector, uint8_t* out, U32 count) { return stock<0x889f6abeu, U32>(sector, out, count); }

uint8_t* const rx = reinterpret_cast<uint8_t*>(rx_frame);
uint8_t* const tx = reinterpret_cast<uint8_t*>(tx_frame);
uint8_t* const files = reinterpret_cast<uint8_t*>(file_buffer);
uint8_t* const staged = reinterpret_cast<uint8_t*>(image);
P::Parser parser __attribute__((section(".data"))) = {0, 0, 0, 0};
volatile U32 rx_quiet_from __attribute__((section(".data"))) = 0;   // now() when input last ended

// PUT state.
volatile U32 put_expected __attribute__((section(".data"))) = 0;
uint8_t put_sha[32] __attribute__((section(".data"))) = {0};
// Raw storage: a global with a constructor would need dynamic init (none in
// the cold link). put_begin() calls reset() before any use.
U32 put_hash_storage[(sizeof(xyf_core::hash::Sha256) + 3) / 4] __attribute__((section(".data"))) = {0};
xyf_core::hash::Sha256& put_hash() { return *reinterpret_cast<xyf_core::hash::Sha256*>(put_hash_storage); }
volatile U32 put_running_crc __attribute__((section(".data"))) = 0;
volatile uint32_t mounted __attribute__((section(".data"))) = 0;
volatile U32 read_enabled __attribute__((section(".data"))) = 0;
volatile U32 storage_used __attribute__((section(".data"))) = 0;   // Test 048: a wrote (set default)

// Watchdog guard (usb_service.hpp, Test 041).
const U32 rcu_ctl = 0x20000000u, sec_gctl = 0x20004000u, sec_fctl = 0x20004010u, sec_sctl3 = 0x20004818u;
U32 guard_saved[4] __attribute__((section(".data"))) = {0, 0, 0, 0};
volatile U32 guard_on __attribute__((section(".data"))) = 0;
volatile U32 held __attribute__((section(".data"))) = 0;
volatile U32 dry_run __attribute__((section(".data"))) = 0;      // this boot ends in a dry-run reset
volatile U32 carried_flags __attribute__((section(".data"))) = 0; // hold/guard for the dry-run marker
U32 flags() { return (held ? marker_held : 0) | (guard_on ? marker_guard : 0); }
// 104: the watchdog no longer requests a hardware fault reset (RCU_CTL bit 8, SEC fault):
// on v3 that reset dropped USB but left b frozen (Tests 096-103). WDOG0 expiry (SEC source 3)
// now raises a priority-0 interrupt (it nests above the USB interrupt, 096) whose handler runs
// reset_self(), the stock reset every REBOOT uses. A hang with interrupts disabled is not covered.
volatile U32 guard_fired __attribute__((section(".data"))) = 0;
void say(const char*, const char*, const char*, const char*);
// 112: where b was stuck. Our caller is the stock nesting wrapper 11a015d8 (LINK 0, then
// [--SP] = RETI): with a frame pointer, [FP] is the wrapper's FP, [wrapper FP - 4] the
// interrupted PC (RETI), [wrapper FP + 4] its return into 11a01570 (expect 0x11a015b2), and
// [wrapper FP] the interrupted code's FP (walked for its callers).
char guard_text[3][40] __attribute__((section(".data"))) = {{0}, {0}, {0}};
bool ram(U32 a) { return !(a & 3u) && ((a >= 0x88000000u && a < 0x8c000000u) || (a >= 0x11800000u && a < 0x11c00000u)); }
void hex8(char* at, U32 v) { for (U32 i = 0; i != 8; ++i) { const U32 d = v >> (28 - 4 * i) & 0xfu; at[i] = char(d < 10 ? '0' + d : 'a' + d - 10); } }
// Record 021 (a): b's stock reset did not complete with a link request open (148-160) or
// with USB on (096-108). Every reset of b closes the link first (xyf_boot::link_close: an open request
// waited for, at most close_ms, then the master close, then the hardware checked off); USB stops after
// that. False: the link could not be shown closed, so b does not reset (nor tries again this boot).
volatile U32 reset_refused __attribute__((section(".data"))) = 0;
void guard_disarm();
#if defined(XYF_ONE_SESSION) && defined(XYF_SERVICE_ON_MARKER_ONLY)
bool b_link_closed(bool wait) {
    if (reset_refused || !xyf_boot::link_close(wait)) { reset_refused = 1; return false; }
    return true;
}
#else
bool b_link_closed(bool) { return true; }
#endif
__attribute__((optimize("no-omit-frame-pointer"))) void guard_expired(U32, U32) {
    guard_fired = 1;
    const U32 fp = reinterpret_cast<U32>(__builtin_frame_address(0));
    const U32 wfp = ram(fp) ? word(fp) : 0;
    const U32 reti = ram(wfp) ? word(wfp - 4) : 0, check = ram(wfp) ? word(wfp + 4) : 0;
    const U32 ifp = ram(wfp) ? word(wfp) : 0;
    const U32 r1 = ram(ifp) ? word(ifp + 4) : 0, ifp2 = ram(ifp) ? word(ifp) : 0;
    const U32 r2 = ram(ifp2) ? word(ifp2 + 4) : 0;
    char* a = guard_text[0]; char* b = guard_text[1]; char* c = guard_text[2];
    const char* p = "stuck at "; U32 k = 0; for (; p[k]; ++k) a[k] = p[k]; hex8(a + k, reti); a[k + 8] = 0;
    p = "wrapper "; for (k = 0; p[k]; ++k) b[k] = p[k]; hex8(b + k, check); b[k + 8] = 0;
    p = "callers "; for (k = 0; p[k]; ++k) c[k] = p[k]; hex8(c + k, r1); c[k + 8] = ' '; hex8(c + k + 9, r2); c[k + 17] = 0;
    say("Watchdog fired. Please read me:", a, b, c);
    // 107: a reset with USB running froze b every time (fault reset, test-hang, 105-106's
    // interrupt); every working REBOOT stops USB first (menu_release -> usb_stop). Same here.
    // a has no USB: it resets as its other restarts do (103). Record 021: b closes the link first (no
    // wait: nothing completes a request inside this interrupt); refused: no reset, guard off.
    if (byte(0x8807cac4u)) {
        if (!b_link_closed(false)) { guard_disarm(); return; }
        usb_stop();
    }
    reset_self();                                        // guard_disarm, then the stock reset
}
void guard_arm() {
    if (guard_on) return;
    guard_saved[0] = word(rcu_ctl); guard_saved[1] = word(sec_gctl);
    guard_saved[2] = word(sec_fctl); guard_saved[3] = word(sec_sctl3);
    word(wdog_ctl) = wdog_disable;
    word(wdog_cnt) = guard_count;
    stock<0x88a30798u, U32>(3u, reinterpret_cast<U32>(&guard_expired), 0u, 1u);   // source 3: SEN|IEN
    stock<0x88a319fau, U32>(3u, 0u);                     // priority 0 (highest)
    word(wdog_stat) = 0;                                 // reload from CNT
    word(wdog_ctl) = wdog_enable;
    guard_on = 1;
}
volatile U32 guard_muted __attribute__((section(".data"))) = 0;   // 105: expiry test (b keeps running)
void guard_kick() {
    if (guard_on && !guard_muted) { word(wdog_stat) = 0; ++result.guard_kicks; }
}
// 107: WDOG0 takes a new count only while disabled (106: CNT kept the old value).
void guard_count_set(U32 count) {
    if (!guard_on) return;
    word(wdog_ctl) = wdog_disable; word(wdog_cnt) = count; word(wdog_stat) = 0; word(wdog_ctl) = wdog_enable;
}
void guard_disarm() {
    if (!guard_on) return;
    word(wdog_ctl) = wdog_disable;
    word(sec_sctl3) = guard_saved[3];                    // source 3 off again (its slot stays registered)
    guard_on = 0;
}
void marker_write(U32 reason, U32 crashes) {
    word(reboot_marker + 8) = reason; word(reboot_marker + 12) = crashes;
    word(reboot_marker) = reboot_magic; word(reboot_marker + 4) = ~reboot_magic;
    lines(reboot_marker, 16, false);                    // to SDRAM before any reset
}
void marker_clear() {
    for (U32 i = 0; i != 4; ++i) word(reboot_marker + 4 * i) = 0;
    lines(reboot_marker, 16, false);
}
void tick() { guard_kick(); menu_link::tick(); }

// Install log (usb_service.hpp, Test 035). Runs inside the stock writer: no
// session step here (poll() requires muted sinks), bounded CDC waits only.
volatile U32 log_open __attribute__((section(".data"))) = 0;   // INSTALL running and the host keeps up
U32 saved_sinks[4] __attribute__((section(".data"))) = {0, 0, 0, 0};
// Core IPEND (BF70x 0x1fc02108, read by the stock dispatcher 11a007a8): any of
// IVG5..IVG14 pending means a log call from an interrupt handler.
bool in_interrupt() { return (word(0x1fc02108u) & 0x7fe0u) != 0; }
bool raw_send(const uint8_t* frame, U32 n) {
    for (U32 at = 0; at < n;) {
#if defined(XYF_MTP_USB)
        const U32 most = write_chunk();
#else
        const U32 most = 512u;
#endif
        const U32 chunk = n - at < most ? n - at : most;
        const U32 start = now();
        int wrote = -1;
        while ((wrote = cdc_write(frame + at, chunk)) < 0) if (now() - start > 200u) return false;
        at += U32(wrote) ? U32(wrote) : chunk;
    }
    return true;
}
void log_line(const char* text) {
    Result& r = result;
    ++r.log_lines;
    if (!log_open || in_interrupt()) { ++r.log_skipped; return; }
    uint8_t* const payload = reinterpret_cast<uint8_t*>(log_frame);
    uint8_t* const out = payload + 0x100;
    P::put32(payload, P::s_ok); P::put32(payload + 4, now());
    U32 n = 8;
    for (; text && *text && n != 8 + log_text_max; ++text) payload[n++] = uint8_t(*text);
    const U32 size = P::encode(uint8_t(P::t_log | P::t_reply), 0, payload, n, out, 0x100);
    if (size && raw_send(out, size)) ++r.log_sent;
    else { ++r.log_skipped; log_open = 0; }        // host not reading: stop, never slow the writer
}
// Stock sink signature (11a01d96 calls each non-zero sink as sink(level, text)).
// text starts with 4 binary time bytes (88a34230), then "file[line]: message"
// (Test 036: a zero time byte emptied the line).
void log_sink(int, const char* text) { log_line(text ? text + log_time_bytes : text); }
void hook_logs() {
    Result& r = result;
    r.log_level_before = word(log_level_address);
    U32 any = 0;
    for (U32 i = 0; i != 4; ++i) { saved_sinks[i] = word(log_sinks + 4 * i); any |= saved_sinks[i]; word(log_sinks + 4 * i) = 0; }
    r.log_sink_before = any;
    word(log_sinks) = reinterpret_cast<U32>(&log_sink);
    if (r.log_level_before < log_level) word(log_level_address) = log_level;
}
void unhook_logs() {
    word(log_level_address) = result.log_level_before;
    for (U32 i = 0; i != 4; ++i) word(log_sinks + 4 * i) = saved_sinks[i];
}
// "xyf install step N value 0xXXXXXXXX"
void beacon(U32 step, U32 value) {
    result.install_step = step; result.install_step_ms = now();
    char text[40];
    const char* head = "xyf install step ";
    U32 n = 0;
    for (; *head; ++head) text[n++] = *head;
    text[n++] = char('0' + step % 10);
    const char* mid = " value 0x";
    for (; *mid; ++mid) text[n++] = *mid;
    for (U32 k = 0; k != 8; ++k) text[n++] = "0123456789abcdef"[value >> (28 - 4 * k) & 0xfu];
    text[n] = 0;
    log_line(text);
}

bool send(uint8_t type, uint8_t seq, const uint8_t* payload, U32 length) {
    const U32 n = P::encode(uint8_t(type | P::t_reply), seq, payload, length, tx, P::max_frame);
    if (!n) return false;
    for (U32 at = 0; at < n;) {
#if defined(XYF_MTP_USB)
        const U32 most = write_chunk();
#else
        const U32 most = 512u;
#endif
        const U32 chunk = n - at < most ? n - at : most;
        const U32 start = now();
        int wrote = -1;
        while ((wrote = cdc_write(tx + at, chunk)) < 0) {
            tick();
            if (now() - start > 500u) { ++result.write_errors; return false; }
        }
        at += U32(wrote) ? U32(wrote) : chunk;
    }
    ++result.replies;
    return true;
}
bool status_only(uint8_t type, uint8_t seq, U32 status) {
    uint8_t p[4]; P::put32(p, status);
    result.last_status = status;
    return send(type, seq, p, 4);
}

// HELLO text (docs/PROTOCOL.md): generated from protocol/v1.json; the build defines
// XYF_BUILD_NUMBER and XYF_BUILD_MODE, the build switches set the cmds bitmap. It ends in
// ";stage2=0": stage2_hello sets the loaded stage 2's version and its cmds bit.
static_assert(sizeof(XYF_USBP_HELLO_TEXT) - 1 <= P::max_hello, "hello text above max_hello");
constexpr bool ends_with(const char* text, U32 n, const char* end, U32 m) {
    return m <= n && (!m || (text[n - 1] == end[m - 1] && ends_with(text, n - 1, end, m - 1)));
}
#if !defined(XYF_SIM_HELLO_TEXT)                                       // a simulator variant's own text
static_assert(ends_with(XYF_USBP_HELLO_TEXT, sizeof(XYF_USBP_HELLO_TEXT) - 1, ";stage2=0", 9),
              "hello text must end in the stage2 key");
#endif
U32 stage2_hello(uint8_t* p, U32 n, U32 room);
void hello(uint8_t seq) {
    uint8_t p[4 + P::max_hello];
    P::put32(p, P::s_ok);
    const char* text = XYF_USBP_HELLO_TEXT;
    U32 n = 4;
    for (; *text && n != sizeof(p); ++text) p[n++] = uint8_t(*text);
    n = stage2_hello(p, n, sizeof(p));
    send(P::t_hello, seq, p, n);
}

// STATUS: u32 status, then blocks {u32 magic, u32 bytes, bytes...}.
U32 append_block(uint8_t* p, U32 n, const void* data, U32 bytes) {
    if (n + 8 + bytes > P::max_data) return n;
    const uint8_t* d = static_cast<const uint8_t*>(data);
    P::put32(p + n, P::get32(d)); P::put32(p + n + 4, bytes); n += 8;
    for (U32 i = 0; i != bytes; ++i) p[n++] = d[i];
    return n;
}
// Decision 025: where key_ui painted B's stack (0: not painted, as in the simulator).
volatile U32 stack_paint_begin __attribute__((section(".data"))) = 0;
void status(uint8_t seq) {
    uint8_t* p = files;
    if (stack_paint_begin)
        result.stack_used = stack_mark::used(reinterpret_cast<const volatile uint32_t*>(stack_paint_begin),
                                             reinterpret_cast<const volatile uint32_t*>(XYF_STACK_END));
    P::put32(p, P::s_ok);
    U32 n = append_block(p, 4, &result, sizeof(Result));
    n = append_block(p, n, &menu_link::result, sizeof(menu_link::Result));
    n = append_block(p, n, &storage_server::result, sizeof(storage_server::Result));
    n = append_block(p, n, &knob_input::result, sizeof(knob_input::Result));   // the knob reader's counters
#if defined(XYF_MENU_ON_FAILURE)
    n = append_block(p, n, &file_boot::result.timing, sizeof(xyf_boot::TimingInfo));
#endif
#if defined(XYF_ONE_SESSION)
    n = append_block(p, n, &xyf_boot::storage_start, sizeof(xyf_boot::StorageStart));   // 191
#endif
    if (word(dry_run_area) == dry_run_magic && word(dry_run_area + 4) <= dry_run_max_bytes)   // last dry-run boot
        n = append_block(p, n, reinterpret_cast<const void*>(dry_run_area + 8), word(dry_run_area + 4));
    send(P::t_status, seq, p, n);
}

// Before each file command, is a still XY Forever, serving b's read entry (a's bootloader,
// after a lone restart, would serve the request with no path policy)? One bounded link round trip
// per command (file_client::serving).
bool a_serving() { return (read_enabled = file_client::serving() == P::s_ok) != 0; }

// ---- Stage 2 (target/xyf_stage2.hpp ABI 3; docs/STORAGE.md) ------------------------
// At the first HELLO of a boot (a plain boot has no USB service and never gets here), B reads
// /yaffs2/xy-forever/stage2/main.xys2 through its file client (every request bounded): first its
// 96-byte header (a ranged read), then the whole file, no larger than the header says, into the
// staging buffer (nothing is staged before the first HELLO). accept() checks magic, ABI, header
// CRC, sizes, the load range inside the window, the entry inside the image, the image CRC and
// SHA-256 and min_stage1 <= this build. Only then is the image copied to load_address, its CRC
// checked again there, flushed (FLUSH, IFLUSH) and its init called through the one audited
// trampoline. Any failure leaves stage 1 exactly as before and stage2[] says why. Stage 2 is
// called only after init answered init_ok, and never again after an answer it does not define.
namespace S2 = xyf::stage2;
extern "C" uint32_t xyf_stage2_enter(uint32_t entry, const S2::Call* call) __asm__("xyf_stage2_enter");
constexpr U32 decimal(const char* s, U32 v = 0) { return *s ? decimal(s + 1, v * 10 + U32(*s - '0')) : v; }
const U32 stage1_build = decimal(XYF_BUILD_NUMBER);
volatile U32 s2_tried __attribute__((section(".data"))) = 0;
volatile U32 s2_entry __attribute__((section(".data"))) = 0;          // 0: not loaded (or stopped)
S2::Header s2_header __attribute__((section(".data"))) = {};
uint32_t s2_now() { return now(); }
void s2_trace(uint32_t code, uint32_t value) { result.stage2[s2w_trace_code] = code; result.stage2[s2w_trace_value] = value; }
uint32_t s2_reply(uint32_t type, uint32_t seq, const uint8_t* payload, uint32_t length) {
    return send(uint8_t(type), uint8_t(seq), payload, length) ? 1u : 0u;
}
const S2::Services3 s2_services = {S2::services_version, sizeof(S2::Services3), stage1_build, s2_now, s2_trace, s2_reply};
void s2_stop(U32 status, U32 detail) {
    s2_entry = 0;
    result.stage2[s2w_status] = status; result.stage2[s2w_detail] = detail;
}
U32 s2_call(S2::Call& c) {
    c.abi = S2::abi_service; c.bytes = sizeof(c); c.services = &s2_services; c.header = &s2_header;
    return xyf_stage2_enter(s2_entry, &c);
}
__attribute__((noinline)) void prepare_code(U32 begin, U32 end) {
    lines(begin, end - begin, false);                                  // FLUSH, SSYNC
    for (U32 a = begin & ~31u; a < end; a += 32) __asm__ volatile("IFLUSH [%0];" : : "a"(a) : "memory");
    __asm__ volatile("SSYNC; CSYNC;" : : : "memory");
}
__attribute__((noinline)) void stage2_load() {
    uint32_t* const r = result.stage2;
    s2_tried = 1;
    r[s2w_status] = s2_link;
    if (!a_serving()) return;
    ++r[s2w_reads];
    uint8_t head[S2::header_bytes];
    uint32_t bytes = 0;
    U32 code = file_client::read_file(S2::path, 0, true, head, S2::header_bytes, bytes, mounted);
    if (code == P::s_ok && bytes == S2::header_bytes) {
        const U32 image_bytes = P::get32(head + 24);                   // Header::image_bytes
        r[s2w_bytes] = image_bytes;
        if (!image_bytes || image_bytes > S2::max_image) { s2_stop(s2_check, U32(S2::Check::size)); return; }
        code = file_client::read_file(S2::path, 0, false, staged, S2::header_bytes + image_bytes, bytes, mounted);
    } else if (code == P::s_ok) code = P::s_range;                     // shorter than a header
    if (code != P::s_ok) { s2_stop(code == P::s_not_found ? s2_missing : code == P::s_link ? s2_link : s2_read, code); return; }
    S2::Header h;
    const S2::Check check = S2::accept(staged, bytes, stage1_build, h);
    if (check != S2::Check::ok) { s2_stop(s2_check, U32(check)); return; }
    volatile uint8_t* to = reinterpret_cast<volatile uint8_t*>(h.load_address);
    for (U32 i = 0; i != h.image_bytes; ++i) to[i] = staged[S2::header_bytes + i];
    if (xyf::crc::update_volatile(0, to, h.image_bytes) != h.image_crc32) { s2_stop(s2_copy, 0); return; }
    prepare_code(h.load_address, h.load_address + h.image_bytes);
    s2_header = h;
    s2_entry = h.entry;
    S2::Call c = {};
    c.op = S2::op_init;
    const U32 answer = s2_call(c);
    if (answer != S2::init_ok) { s2_stop(s2_init, answer); return; }
    r[s2w_status] = s2_loaded; r[s2w_detail] = 0;
    r[s2w_version] = h.version; r[s2w_load_ms] = now();
}
// Every home_pump pass while stage 2 is loaded.
void stage2_turn() {
    if (!s2_entry) return;
    S2::Call c = {};
    c.op = S2::op_turn;
    const U32 answer = s2_call(c);
    if (answer != S2::turn_ok) s2_stop(s2_hook, answer);
}
// A request type stage 1 does not answer: true when stage 2 replied.
bool stage2_command(uint8_t type, uint8_t seq, const uint8_t* payload, U32 length) {
    if (!s2_entry) return false;
    S2::Call c = {};
    c.op = S2::op_command; c.type = type; c.seq = seq; c.length = length; c.payload = payload;
    const U32 answer = s2_call(c);
    if (answer == S2::handled) return true;
    if (answer != S2::not_handled) s2_stop(s2_hook, answer);
    return false;
}
// HELLO: ";stage2=0" becomes the loaded version, and stage2.info's cmds bit is set.
U32 stage2_hello(uint8_t* p, U32 n, U32 room) {
    if (!s2_entry) return n;
    for (U32 i = 4; i + 5 + 32 <= n; ++i) {
        if (p[i] != 'c' || p[i + 1] != 'm' || p[i + 2] != 'd' || p[i + 3] != 's' || p[i + 4] != '=') continue;
        uint8_t& d = p[i + 5 + 31 - (P::t_stage2_info >> 2)];
        U32 v = d <= '9' ? U32(d - '0') : U32(d - 'a' + 10);
        v |= 1u << (P::t_stage2_info & 3u);
        d = uint8_t(v < 10 ? '0' + v : 'a' + v - 10);
        break;
    }
    static const U32 powers[10] = {1000000000u, 100000000u, 10000000u, 1000000u, 100000u, 10000u, 1000u, 100u,
                                   10u, 1u};
    uint8_t digits[10];
    U32 k = 0, v = s2_header.version;
    for (U32 i = 0; i != 10; ++i) {                                    // decimal without a division (no libgcc)
        U32 d = 0;
        while (v >= powers[i]) { v -= powers[i]; ++d; }
        if (d || k || i == 9) digits[k++] = uint8_t('0' + d);
    }
    if (n - 1 + k > room) return n;
    --n;                                                               // the trailing 0
    for (U32 i = 0; i != k; ++i) p[n++] = digits[i];
    return n;
}

// READ_FILE: payload "/yaffs2/..." NUL [u32 offset, u32 length]. Reply: u32
// status, then data: the whole file (<= max_data), or the range (length <=
// read_range_max; fewer bytes = end of file).
void read_file(uint8_t seq, const uint8_t* payload, U32 length) {
    U32 path_bytes = 0;
    while (path_bytes != length && payload[path_bytes]) ++path_bytes;
    const bool ranged = path_bytes + 9 == length;                  // NUL + u32 offset + u32 length
    if (length < 9 || path_bytes + 1 > storage_server::max_path || (path_bytes + 1 != length && !ranged)) {
        status_only(P::t_read_file, seq, P::s_bad_request); return;
    }
    const U32 offset = ranged ? P::get32(payload + path_bytes + 1) : 0;
    const U32 want = ranged ? P::get32(payload + path_bytes + 5) : P::max_data;
    if (!want || want > (ranged ? read_range_max : P::max_data)) { status_only(P::t_read_file, seq, P::s_bad_request); return; }
    if (!a_serving()) { status_only(P::t_read_file, seq, P::s_link); return; }
    uint32_t bytes = 0;
    file_client::Open left;
    const U32 code = file_client::read_file(reinterpret_cast<const char*>(payload), offset, ranged, files + 4, want,
                                            bytes, mounted, &left);
    ++result.reads; result.read_bytes += bytes;
    P::put32(files, code);
    result.last_status = code;
    send(P::t_read_file, seq, files, 4 + (code == P::s_ok ? bytes : 0));
    // The probe and close after the reply, while the host sends its next request (this pays
    // for the a_serving() check); past its bound either marks the mount stale (op 9 before the next open).
    file_client::finish_read(left, mounted);
}

// Test 054: the service screen (b's display); no-op without XYF_SERVICE_SCREEN,
// and before the first HELLO of this boot: a boot without the host tool never
// draws (normal use is unchanged even if drawing were ever to fail).
volatile U32 screen_on __attribute__((section(".data"))) = 0;
volatile U32 home __attribute__((section(".data"))) = 0;             // Test 065: our reboot -> the menu is home
volatile U32 home_guarded __attribute__((section(".data"))) = 0;
volatile U32 home_greeted __attribute__((section(".data"))) = 0;
volatile U32 menu_usb __attribute__((section(".data"))) = 0;     // Test 058: USB kept up for the menu
void say(const char* a, const char* b = "", const char* c = "", const char* d = "") {
#if defined(XYF_SERVICE_SCREEN)
    if (screen_on) key_ui::service_screen(a, b, c, d);
#else
    (void)a; (void)b; (void)c; (void)d;
#endif
}

// SCREEN (Test 053): reply u32 status, u32 framebuffer address, then up to
// read_range_max bytes of the framebuffer at offset. Reads memory only.
void screen(uint8_t seq, const uint8_t* payload, U32 length) {
    const U32 fb = word(screen_pointer);
    const U32 offset = length == 4 ? P::get32(payload) : screen_bytes;
    if (fb < 0x80000000u || fb > 0x90000000u - screen_bytes || (fb & 1u) || offset >= screen_bytes) {
        uint8_t p[8]; P::put32(p, P::s_range); P::put32(p + 4, fb);
        send(P::t_screen, seq, p, 8); return;
    }
    const U32 n = screen_bytes - offset < read_range_max ? screen_bytes - offset : read_range_max;
    lines(fb + offset, n, true);                       // the display path writes it by DMA/cache
    P::put32(files, P::s_ok); P::put32(files + 4, fb);
    for (U32 i = 0; i != n; ++i) files[8 + i] = byte(fb + offset + i);
    send(P::t_screen, seq, files, 8 + n);
}

// Read-only diagnostics: compare stock code in memory with the authenticated image (CRC
// spans), and read the stock interrupt tables (L1 data). Never L1 instruction memory
// (data loads from it stall the processor) or anything else.
bool within(U32 address, U32 bytes, U32 start, U32 end) { return address >= start && address <= end - bytes; }
bool peek_allowed(U32 address, U32 bytes) {
    return bytes && bytes <= read_range_max &&
           (within(address, bytes, 0x88000000u, 0x88b00000u) ||
            within(address, bytes, 0x08000000u, 0x08040000u) ||
            within(address, bytes, 0x11b00000u, 0x11b02000u) ||  // L1 data: stock IRQ/driver tables
            within(address, bytes, 0x8bfff000u, 0x8bfff800u) ||  // 111: the reboot-marker page
            within(address, bytes, 0x8b810000u, 0x8b811000u));   // the menu's catalog (xyf_boot catalog_buffer)
}
// 095/096: interrupt-controller state, 32-bit reads only, and only registers the stock
// bootloader itself reads or writes: SEC0 GCTL/GSTAT, FCTL/FSTAT, core interface 0
// CCTL/CSTAT, SCTL/SSTAT of sources 0..0x4f (stock uses up to 0x4e); core IMASK/IPEND/ILAT.
// 095 allowed SCTL of sources 0..127: one 1 KiB read of them hung b (2026-11-13), most
// likely on a source the BF707 does not implement.
bool mmr_allowed(U32 address, U32 bytes) {
    return bytes && bytes <= read_range_max && !(address & 3u) && !(bytes & 3u) &&
           (within(address, bytes, 0x20004000u, 0x20004008u) || within(address, bytes, 0x20004010u, 0x20004018u) ||
            within(address, bytes, 0x20004400u, 0x20004408u) || within(address, bytes, 0x20004800u, 0x20004a80u) ||
            within(address, bytes, 0x1fc02104u, 0x1fc02110u) ||
            // 101: RCU0 registers the stock reset 11a03518 writes: CTL/STAT, 0x1c, 0x68
            within(address, bytes, 0x20000000u, 0x20000008u) || within(address, bytes, 0x2000001cu, 0x20000020u) ||
            within(address, bytes, 0x20000068u, 0x2000006cu) ||
            within(address, bytes, 0x20046000u, 0x2004600cu));   // 106: WDOG0 CTL/CNT/STAT only (105 allowed +0xc: a read hung b)
}
void peek(uint8_t seq, const uint8_t* payload, U32 length) {
    const U32 address = length == 8 ? P::get32(payload) : 0, bytes = length == 8 ? P::get32(payload + 4) : 0;
    const bool mmr = mmr_allowed(address, bytes);
    if (!mmr && !peek_allowed(address, bytes)) { status_only(P::t_peek, seq, P::s_range); return; }
    P::put32(files, P::s_ok); P::put32(files + 4, address);
    if (mmr) for (U32 i = 0; i != bytes; i += 4) P::put32(files + 8 + i, word(address + i));
    else for (U32 i = 0; i != bytes; ++i) files[8 + i] = byte(address + i);
    send(P::t_peek, seq, files, 8 + bytes);
}

#if defined(XYF_MTP)
// 097: MTP self-test: MTP's storage calls from b's main loop (each one request to a, served by the
// engine's run entry), each step on the screen first. Through B's file client (every request
// bounded; a link error ends the test with link).
void mtp_step(uint32_t n) {
    static const char* const names[] = {"", "1 mount (op 8)", "2 storage info (0x23)", "3 object info (0x22)",
        "4 open folder (0x2e)", "5 list folder (0x2f)", "6 close folder (0x30)", "7 open/read/close file",
        "8 idle with the window open", "9 info again (0x22, 0x23)", "10 unmount (op 9)"};
    say("MTP self-test: running step", n < 11 ? names[n] : "?", "If this stays, power-cycle", "and report the step.");
}
// Results, in the reply's order.
struct MtpSelfTest { U32 step, mounted, space, stat, dir, entries, closedir, fd, read, closed,
                     wait_ms, stat2, space2, unmounted, entry_bytes, reserved; };
// The value of one client request into a result word; false when a did not answer (the test ends:
// link). A path the engine's policy refuses is not sent: its result is -1 (no folder: 0).
bool mtp_value(const file_client::Reply& r, U32& out, U32 refused = ~0u) {
    if (r.outcome == file_client::refused) { out = refused; return true; }
    out = U32(r.value);
    return r.outcome == file_client::done;
}
// One step of MTP's storage calls (results into t). 1 mount, 2 storage info, 3 object info,
// 4 open folder, 5 list it, 6 close it, 7 open/read/close a file, 9 info again, 10 unmount.
bool mtp_do(U32 n, MtpSelfTest& t, uint8_t* out) {
    namespace C = file_client;
    t.step = n;
    U32 v = 0;
    if (n == 1) {                                                         // op 8: a opens the read-write window
        if (!mtp_value(C::mount(), v)) return false;
        t.mounted = v == 0;
    } else if (n == 2) {
        return mtp_value(C::statfs("/yaffs2/", out), t.space);           // op 0x23 (storage info)
    } else if (n == 3 || n == 9) {
        if (!mtp_value(C::stat("/yaffs2/xy-forever", out), n == 3 ? t.stat : t.stat2)) return false;   // op 0x22
        if (n == 9) return mtp_value(C::statfs("/yaffs2/", out), t.space2);
    } else if (n == 4) {
        return mtp_value(C::opendir("/yaffs2/"), t.dir, 0u);             // op 0x2e (object handles)
    } else if (n == 5 && t.dir) {
        for (U32 k = 0; k != 64; ++k) {
            if (!mtp_value(C::readdir(t.dir, out), v)) return false;    // op 0x2f
            if (C::readdir_result(int32_t(v)) != C::folder_entry) break;   // file.list's reading
            ++t.entries;
        }
    } else if (n == 6 && t.dir) {
        if (!mtp_value(C::closedir(t.dir), t.closedir)) return false;   // op 0x30
        t.dir = 0;
    } else if (n == 7) {
        if (!mtp_value(C::open("/yaffs2/xy-forever/state-a.txt", 0), t.fd)) return false;   // op 0x29, O_RDONLY
        if (int(t.fd) >= 0) {
            if (!mtp_value(C::read(int(t.fd), out, 0x100u), t.read)) return false;      // op 0x2a
            if (!mtp_value(C::close(int(t.fd)), t.closed)) return false;                // op 0x2d
        }
    } else if (n == 10) {
        if (!mtp_value(C::unmount(), v)) return false;                  // op 9: a closes the window
        t.unmounted = v == 1 || v == 3;                                  // stock success is 1 (3 closed the window)
    }
    return true;
}
bool mtp_selftest(MtpSelfTest& t, U32 hold_ms, uint8_t* out) {
    for (U32 n = 1; n != 8; ++n) {
        mtp_step(n);
        if (!mtp_do(n, t, out)) return false;
        if (n == 1 && !t.mounted) return true;
    }
    mtp_step(8);                                                          // window open, a idle (as MTP between requests)
    const U32 start = now();
    while (now() - start < hold_ms) {                                     // the guard stays armed
        guard_kick();
        const U32 kicked = now();
        while (now() - kicked < 10u && now() - start < hold_ms) {}
    }
    t.wait_ms = now() - start;
    for (U32 n = 9; n != 11; ++n) { mtp_step(n); if (!mtp_do(n, t, out)) return false; }
    t.step = 11;
    return true;
}
U32 mtp_enter() {
    if (storage_server::engine_identity() || file_client::identity()) return P::s_identity;
    if (mounted && !read_enabled && !(read_enabled = menu_link::usb_command(storage_server::command_item)))
        return P::s_link;
    file_client::release(mounted);
    if (!menu_link::usb_command(storage_server::engine_run_item)) return P::s_link;
    storage_used = 1;                                  // the window opens: CONTINUE needs a REBOOT
    return P::s_ok;
}
U32 mtp_leave(MtpSelfTest& t) {
    if (t.mounted && !t.unmounted) mtp_do(10, t, files + 0x1000);
    read_enabled = 0;                                  // the next read latches read mode again
    return menu_link::usb_command(storage_server::engine_leave_item) ? P::s_ok : P::s_link;
}
void mtp_test(uint8_t seq, const uint8_t* payload, U32 length) {
    MtpSelfTest t = {};
    U32 code = P::s_ok;
    const U32 hold = length == 4 ? P::get32(payload) : 0;
    if (length != 4 || hold > 60000u) code = P::s_bad_request;
    else if ((code = mtp_enter()) == P::s_ok) {
        const bool answered = mtp_selftest(t, hold, files + 0x1000);    // no guard pause
        code = mtp_leave(t);
        if (!answered) code = P::s_link;
        say(code == P::s_ok && t.step == 11 ? "MTP self-test finished." : "MTP self-test FAILED.");
    }
    P::put32(files, code);
    const U32* w = reinterpret_cast<const U32*>(&t);
    for (U32 i = 0; i != 16; ++i) P::put32(files + 4 + 4 * i, w[i]);
    result.last_status = code;
    send(P::t_mtp_test, seq, files, 4 + 64);
}
#endif
void put_begin(uint8_t seq, const uint8_t* payload, U32 length) {
    // Any stage.begin, refused or not, forgets the earlier staging: no stage.data after a
    // refused one writes into the old announcement or continues its hashes.
    put_expected = 0; result.put_ok = 0; result.put_received = 0; result.put_size = 0; result.put_crc = 0;
    if (length != 4 + 4 + 32) { status_only(P::t_put_begin, seq, P::s_bad_request); return; }
    const U32 size = P::get32(payload);
    if (!size || size > image_capacity) { status_only(P::t_put_begin, seq, P::s_too_large); return; }   // Test 049: any size
    put_expected = size; result.put_size = size; result.put_crc = P::get32(payload + 4);
    for (U32 i = 0; i != 32; ++i) put_sha[i] = payload[8 + i];
    put_running_crc = 0;
    put_hash().reset();
    status_only(P::t_put_begin, seq, P::s_ok);
}

void put_data(uint8_t seq, const uint8_t* payload, U32 length) {
    if (!put_expected || length < 5) { status_only(P::t_put_data, seq, P::s_state); return; }
    const U32 offset = P::get32(payload), n = length - 4;
    // A resend of bytes already received unchanged (the host's retry after a lost reply) is
    // ok with the count unchanged; it neither stores nor hashes them again.
    bool again = offset < result.put_received && offset + n <= result.put_received;
    for (U32 i = 0; again && i != n; ++i) again = staged[offset + i] == payload[4 + i];
    if (again) { uint8_t p[8]; P::put32(p, P::s_ok); P::put32(p + 4, result.put_received); send(P::t_put_data, seq, p, 8); return; }
    if (offset != result.put_received || offset + n > put_expected) { status_only(P::t_put_data, seq, P::s_range); return; }
    for (U32 i = 0; i != n; ++i) staged[offset + i] = payload[4 + i];
    put_running_crc = xyf::crc::update(put_running_crc, payload + 4, n);
    put_hash().update(payload + 4, n);
    result.put_received = offset + n;
    uint8_t p[8]; P::put32(p, P::s_ok); P::put32(p + 4, result.put_received);
    send(P::t_put_data, seq, p, 8);
}

bool sha_equal(const uint8_t* a, const uint8_t* b) { U32 d = 0; for (U32 i = 0; i != 32; ++i) d |= a[i] ^ b[i]; return !d; }

void put_end(uint8_t seq) {
    U32 code = P::s_ok;
    xyf_core::hash::Digest digest;
    // A repeated stage.end (the host's retry after a lost reply) answers ok again and keeps
    // the staging; the hashes were checked once and finish() must not run twice.
    if (result.put_ok && put_expected && result.put_received == put_expected) { status_only(P::t_put_end, seq, P::s_ok); return; }
    if (!put_expected || result.put_received != put_expected) code = P::s_state;
    else if (put_running_crc != result.put_crc) code = P::s_crc;
    else if (!put_hash().finish(digest) || !sha_equal(digest.bytes, put_sha)) code = P::s_sha;
    result.put_ok = code == P::s_ok;
    if (!result.put_ok) put_expected = 0;
    status_only(P::t_put_end, seq, code);
}

// Structural checks before anything is written (refuse on any doubt).
U32 validate_image(U32 size, bool uninstall) {
    if (!accept_image(uninstall, has_descriptor(staged, size), size, put_sha)) return P::s_image;
    static const xyf_core::Range allowed[] = {{0x08000000u, 0x08100000u}, {0x11800000u, 0x11c00000u},
                                              {0x80000000u, 0x90000000u}};
    const xyf_core::Policy policy = {allowed, 3, 0, 0};
    xyf_core::Summary summary;
    if (xyf_core::inspect(staged, size, policy, summary) != xyf_core::Error::ok) return P::s_image;
    return P::s_ok;
}

// Read the freshly written boot partition back and compare every byte.
U32 verify(U32 size, U32 crc) {
    uint8_t* back = reinterpret_cast<uint8_t*>(readback_buffer);
    U32 active = 0;
    for (U32 p = 1; p <= 2 && !active; ++p) {
        if (select_partition(p) != 0) continue;
        lines(readback_buffer, 512, true);
        if (read_sector0(0, back) == 0) { lines(readback_buffer, 512, true); if (header_matches(back, size, crc)) active = p; }
    }
    result.verify_partition = active;
    U32 code = active ? P::s_ok : P::s_install;
    if (active && select_partition(active) == 0) {
        const U32 sectors = (size >> 9) + 1;       // as the stock reader 88a07240
        for (U32 s = 0; s < sectors && code == P::s_ok; s += readback_sectors) {
            const U32 count = sectors - s < readback_sectors ? sectors - s : readback_sectors;
            lines(readback_buffer, count * 512, true);
            if (read_sectors(1 + s, back, count)) { code = P::s_install; break; }
            lines(readback_buffer, count * 512, true);
            const U32 base = s * 512, limit = size - base < count * 512 ? size - base : count * 512;
            for (U32 i = 0; i != limit; ++i) if (back[i] != staged[base + i]) { ++result.verify_mismatch; code = P::s_install; break; }
            result.verify_sectors = s + count;
            tick();
        }
    }
    for (U32 tries = 0; tries != 8 && select_partition(0) != 0; ++tries) {}   // back to the user partition
    return code;
}

#if defined(XYF_USB_FILE_WRITE)
// A keeps its read-write window across this boot's FILE_WRITEs. B closes it (op 9, bounded)
// once no write came for storage_server::window_settle_ms, and before a command A would close it
// for (install, set default, CONTINUE, REBOOT): A closing it in its own loop (window_idle_ms, the
// fallback) would leave B's probes unanswered while yaffs writes its checkpoint. Then B fetches the
// catalog A scanned again after the close and the menu shows it (no fresh boot).
volatile U32 writes_due __attribute__((section(".data"))) = 0;
volatile U32 writes_ms __attribute__((section(".data"))) = 0;
// 1 while an op 9 started by settle_begin is out (file commands answer busy: job_blocks).
volatile U32 closing __attribute__((section(".data"))) = 0;
void settle_end(U32 v, bool catalog);
void settle_writes(bool catalog = true) {
    if (!writes_due) return;
    writes_due = 0;
    U32 v = ~0u;
    if (menu_link::usb_command(storage_server::command_item)) {        // A in RPC mode for op 9
        read_enabled = 1;
        const file_client::Reply u = file_client::unmount();             // op 9: 1, or 3 = closed the window
        if (!u.outcome) v = U32(u.value);
    }
    settle_end(v, catalog);
}
// The same close at the mod job's end without waiting in op 9: yaffs's
// checkpoint after megabytes took 0.3 s to about 17 s on the device, and B's loop answered no USB
// meanwhile (not even HELLO). settle_turn, every pass, ends it once A answered or the bound passed (then
// no catalog fetch: it would find the request still out and end the link; a late answer frees it).
void settle_begin() {
    if (!writes_due) return;
    writes_due = 0;
    if (menu_link::usb_command(storage_server::command_item)) {        // A in RPC mode for op 9
        read_enabled = 1;
        if (!file_client::unmount_start().outcome) { closing = 1; return; }
    }
    settle_end(~0u, true);
}
void settle_turn() {
    if (!closing) return;
    const file_client::Reply u = file_client::unmount_poll();
    if (u.outcome == file_client::busy) return;
    closing = 0;
    settle_end(u.outcome ? ~0u : U32(u.value), !u.outcome);              // past its bound: no catalog request
}
void settle_end(U32 v, bool catalog) {
    storage_server::Result& s = storage_server::result;
    mounted = v == ~0u ? file_client::mounted_stale : 0;
    s.b_window = v;
    if (v == 3) ++s.b_windows;
    if (!catalog) return;
    if (xyf_boot::refresh_catalog()) ++s.b_catalogs;
    key_ui::refresh_catalog();
}
#else
void settle_writes(bool = true) {}
#endif

// The catalog item B's menu lists for a library id (n bytes), or 0.
const xyf::catalog::Item* catalog_item(const char* id, U32 n) {
    const xyf::catalog::Catalog* catalog = xyf_boot::catalog();
    for (U32 i = 0; catalog && i != catalog->count; ++i) {
        const char* e = catalog->items[i].id;
        U32 k = 0;
        while (k != n && e[k] == id[k]) ++k;
        if (k == n && !e[n]) return &catalog->items[i];
    }
    return 0;
}

#if defined(XYF_USB_SET_DEFAULT)
// Test 042: compiled out (module space). Test 048: back in the product build
// (wide layout); the first storage write run on hardware.
// SET_DEFAULT: payload = library id NUL. b passes CRC-32(id) to a as eight
// tagged nibble commands (low three bits 0) and a commit; a looks up the unique usable entry and
// writes the next A/B state slot (the only storage write). Reply: u32 status
// (ok = a received all nine commands), u32 hash. The write result is in a's
// report (a_state_write); the host verifies by READ_FILE of the slot.
#if defined(XYF_CONTENT_SWITCH)
U32 content_begin(const char* id, bool menu, bool write_default = true);
#endif
// The A/B state write of the default (the entry with this id hash): eight tagged nibble commands and the commit,
// the barrier, B's catalog fetched again and checked. Also the content switch's last step.
U32 default_commit(U32 hash) {
    U32 code = P::s_ok;
    for (U32 k = 0; k != 8 && code == P::s_ok; ++k)
        if (!menu_link::usb_command_value(xyf::state_write::id_nibble_command(hash, k))) code = P::s_link;
    if (code == P::s_ok && !menu_link::usb_command(xyf::state_write::item_set_default_by_id)) code = P::s_link;
    if (code == P::s_ok) {
        // a writes right after it answers the commit. One more command is the barrier:
        // a's loop answers it only once the write has returned (bounded, guard reloaded).
        storage_used = 1;
        menu_link::usb_patience(write_patience_ms);
        if (!menu_link::usb_command(storage_server::usb_mode_item)) code = P::s_link;
        menu_link::usb_patience(0);
        // The barrier covers A's write and rescan. Refresh B and the displayed
        // default before reporting success; disk read-back alone misses stale UI.
        if (!xyf_boot::refresh_catalog()) code = P::s_link;
        key_ui::refresh_catalog();
        const xyf::catalog::Catalog* catalog = xyf_boot::catalog();
        if (code == P::s_ok && (!catalog || catalog->default_index >= catalog->count ||
            xyf::state_write::id_hash(catalog->items[catalog->default_index].id) != hash)) code = P::s_storage;
    }
    return code;
}
void set_default(uint8_t seq, const uint8_t* payload, U32 length) {
    U32 code = P::s_ok;
    const char* id = reinterpret_cast<const char*>(payload);
    if (length < 2 || length > xyf::store::max_id + 1 || payload[length - 1] || !xyf::store::valid_id(id))
        code = P::s_bad_request;
    const U32 hash = code == P::s_ok ? xyf::state_write::id_hash(id) : 0;
    if (code == P::s_ok) settle_writes();
    // An entry whose content is not installed never becomes the default as it is (nothing sent to A);
    // The content switch installs it first (a job) and writes the default when it succeeded.
    const xyf::catalog::Item* item = code == P::s_ok ? catalog_item(id, length - 1) : 0;
    if (item && (item->flags & xyf::catalog::flag_content_missing)) {
#if defined(XYF_CONTENT_SWITCH)
        code = content_begin(id, false);
        if (code == P::s_ok) code = P::s_started;
#else
        code = P::s_no_content;
#endif
    }
    if (code == P::s_ok) code = default_commit(hash);
    uint8_t p[8]; P::put32(p, code); P::put32(p + 4, hash);
    result.last_status = code;
    send(P::t_set_default, seq, p, 8);
}
#endif

// LIBRARY_BOOT: payload = library id NUL + 32-byte confirmation (the SHA-256 of the id
// bytes). As PLAY on that entry in the Firmware library menu (key_ui.cpp: xyf_boot::choose of its
// catalog index): no boot over a lost link (link); the entry must be in B's catalog (not_found;
// written entries are fetched first: settle_writes) and usable, i.e. have an os.ldr, as xyf_menu's
// usable() (state), and its content installed (no_content). Nothing is written: the default and the state slots stay. Returns the catalog
// index + 1 to boot after the ok reply, else 0.
U32 library_boot(uint8_t seq, const uint8_t* payload, U32 length) {
    U32 n = 0;
    while (n < length && payload[n]) ++n;
    const char* id = reinterpret_cast<const char*>(payload);
    U32 code = P::s_ok, chosen = 0;
    xyf_core::hash::Sha256 h;
    xyf_core::hash::Digest d;
    if (n + 1 + 32 != length || n > xyf::store::max_id || !xyf::store::valid_id(id)) code = P::s_bad_request;
    else if (!h.update(payload, n) || !h.finish(d) || !sha_equal(d.bytes, payload + n + 1)) code = P::s_sha;
    else if (!menu_link::usb_link_up()) code = P::s_link;
    if (code == P::s_ok) {
        settle_writes();                               // A's window closed, the catalog fetched again
        const xyf::catalog::Item* item = catalog_item(id, n);
        code = !menu_link::usb_link_up() ? P::s_link : !item ? P::s_not_found : (item->flags & 1u) ? P::s_ok :
               (item->flags & xyf::catalog::flag_content_missing) ? P::s_no_content : P::s_state;
        if (item) chosen = U32(item - xyf_boot::catalog()->items) + 1;
        if (code == P::s_no_content) say("Its content is not installed:", "make it the default to install its content.");
    }
    result.last_status = code;
    status_only(P::t_library_boot, seq, code);
    return code == P::s_ok ? chosen : 0;
}

#if defined(XYF_USB_FILE_WRITE)
// Before a delete, rmdir or mkdir, as file_write does before its write: a request left past its
// bound (stale) is closed first (op 9, which must reach a in RPC mode), then A's write entry.
U32 enter_writes() {
    if (mounted == file_client::mounted_stale && !read_enabled &&
        !(read_enabled = menu_link::usb_command(storage_server::command_item)))
        return P::s_link;
    if (mounted == file_client::mounted_stale) file_client::release(mounted);
    return menu_link::usb_command(storage_server::file_write_item) ? P::s_ok : P::s_link;
}
// Path NUL + 32-byte confirmation (the SHA-256 of the path bytes). The path's length (0: the
// request does not match its path), else 0.
U32 confirmed_path(const uint8_t* payload, U32 length, bool& confirmed) {
    U32 n = 0;
    while (n < length && payload[n]) ++n;
    confirmed = false;
    if (n + 1 + 32 != length || n + 1 > storage_server::max_path) return 0;
    xyf_core::hash::Sha256 h;
    xyf_core::hash::Digest d;
    confirmed = h.update(payload, n) && h.finish(d) && sha_equal(d.bytes, payload + n + 1);
    return n;
}
// FILE_DELETE / FILE_RMDIR / FILE_MKDIR: the path in the write area and through the engine's
// policy (refused; valid_change_path, as FILE_WRITE), its
// confirmation (sha), then one change in A's write window (writes_due: B closes it as after a
// FILE_WRITE). Reply: u32 status, u32 A's error number.
void file_change(uint8_t type, uint8_t seq, const uint8_t* payload, U32 length) {
    const file_engine::Operation policy = type == P::t_delete_file ? file_engine::op_unlink :
                                          type == P::t_remove_dir ? file_engine::op_rmdir : file_engine::op_mkdir;
    const U32 op = type == P::t_delete_file ? 0x25u : type == P::t_remove_dir ? 0x27u : 0x26u;
    const char* path = reinterpret_cast<const char*>(payload);
    bool confirmed;
    uint32_t error = 0;
    U32 code = P::s_ok;
    if (!confirmed_path(payload, length, confirmed)) code = P::s_bad_request;
    else if (!storage_server::valid_change_path(path, policy)) code = P::s_refused;   // the write area
    else if (!confirmed) code = P::s_sha;
    else if ((code = enter_writes()) == P::s_ok) {
        storage_used = 1;
        code = file_client::change(op, path, error, mounted);
        writes_due = 1; writes_ms = now();
    }
    uint8_t p[8]; P::put32(p, code); P::put32(p + 4, error);
    result.last_status = code;
    send(type, seq, p, 8);
}
// FILE_RENAME: source NUL + target NUL + 32-byte confirmation (the SHA-256 of source, NUL,
// target). Both paths in the write area and the pair through the engine's policy (refused; nothing
// sent), its confirmation (sha), then A renames in its write window (writes_due, as FILE_DELETE). The
// target must not exist (state). Reply: u32 status, u32 A's error number.
void file_rename(uint8_t seq, const uint8_t* payload, U32 length) {
    U32 a = 0, b;
    while (a < length && payload[a]) ++a;
    for (b = a + 1; b < length && payload[b]; ++b) {}
    const char* from = reinterpret_cast<const char*>(payload);
    const char* to = from + a + 1;
    uint32_t error = 0;
    U32 code = P::s_ok;
    xyf_core::hash::Sha256 h;
    xyf_core::hash::Digest d;
    if (a >= length || b + 1 + 32 != length || a + 1 > storage_server::max_path || b - a > storage_server::max_path)
        code = P::s_bad_request;
    else if (!storage_server::valid_rename(from, to)) code = P::s_refused;
    else if (!h.update(payload, b) || !h.finish(d) || !sha_equal(d.bytes, payload + b + 1)) code = P::s_sha;
    else if ((code = enter_writes()) == P::s_ok) {
        storage_used = 1;
        code = file_client::rename(from, to, error, mounted);
        writes_due = 1; writes_ms = now();
    }
    uint8_t p[8]; P::put32(p, code); P::put32(p + 4, error);
    result.last_status = code;
    send(P::t_rename_file, seq, p, 8);
}
// FILE_COPY: source NUL + target NUL + 32-byte confirmation (as FILE_RENAME). B copies through
// its staging buffer with requests the device has proven, no new stock call: the target and its folder
// checked (stat, as file.list), the source read whole (as READ_FILE and stage 2's load; a staged PUT is
// discarded), then FILE_WRITE's write and read-back in A's write window (writes_due). After a failed
// write the target is removed (unlink, as FILE_DELETE). Reply: u32 status, u32 bytes read, u32 A's error
// number, u32 partial (1: a partial target may remain).
void file_copy(uint8_t seq, const uint8_t* payload, U32 length) {
    U32 a = 0, b;
    while (a < length && payload[a]) ++a;
    for (b = a + 1; b < length && payload[b]; ++b) {}
    const char* from = reinterpret_cast<const char*>(payload);
    const char* to = from + a + 1;
    uint32_t error = 0, bytes = 0, written = 0, back = 0, partial = 0;
    U32 code = P::s_ok;
    xyf_core::hash::Sha256 h;
    xyf_core::hash::Digest d;
    if (a >= length || b + 1 + 32 != length || a + 1 > storage_server::max_path || b - a > storage_server::max_path)
        code = P::s_bad_request;
    else if (!storage_server::valid_path(from) || !storage_server::valid_write_path(to) ||
             file_engine::decide_engine(file_engine::op_read, from) != file_engine::allow)
        code = P::s_refused;
    else if (!h.update(payload, b) || !h.finish(d) || !sha_equal(d.bytes, payload + b + 1)) code = P::s_sha;
    else if (!a_serving()) code = P::s_link;
    else if ((code = file_client::copy_target(to, error, mounted)) == P::s_ok) {
        storage_used = 1;
        put_expected = 0; result.put_ok = 0;                             // the staging holds the copy now
        say("Copying a file...", to, "Do not unplug or power off.");
        code = file_client::read_file(from, 0, false, staged, image_capacity, bytes, mounted);
        if (code == P::s_ok && (code = enter_writes()) == P::s_ok) {
            code = file_client::write_file(to, staged, bytes, xyf::crc::update(0, staged, bytes), written, back, mounted);
            if (code != P::s_ok) partial = file_client::remove_partial(to, mounted);
            writes_due = 1; writes_ms = now();
        }
        say(code == P::s_ok ? "File copied and verified." : "File copy FAILED.", to);
    }
    uint8_t p[16]; P::put32(p, code); P::put32(p + 4, bytes); P::put32(p + 8, error); P::put32(p + 12, partial);
    result.last_status = code;
    send(P::t_copy_file, seq, p, 16);
}
// FILE_LIST: path NUL + u32 skip. Reply: u32 status, u32 count, u32 more, entries
// (file_client::list_entry), at most read_range_max reply bytes after the status.
void list_dir(uint8_t seq, const uint8_t* payload, U32 length) {
    U32 n = 0;
    while (n != length && payload[n]) ++n;
    const char* path = reinterpret_cast<const char*>(payload);
    uint32_t count = 0, more = 0, bytes = 0;
    U32 code = P::s_ok;
    if (n + 5 != length || n + 1 > storage_server::max_path) code = P::s_bad_request;
    else if (file_engine::decide_engine(file_engine::op_list, path) != file_engine::allow) code = P::s_refused;
    else if (!a_serving()) code = P::s_link;
    else code = file_client::list(path, P::get32(payload + n + 1), files + 12, read_range_max - 8, count, more, bytes,
                                  mounted);
    P::put32(files, code); P::put32(files + 4, count); P::put32(files + 8, more);
    result.last_status = code;
    send(P::t_list_dir, seq, files, code == P::s_ok ? 12 + bytes : 4);
}

// FILE_WRITE (Test 049): payload = path NUL + 32-byte confirm token (SHA-256
// of the staged PUT data). Writes the staged bytes to path in the write area
// (all of /yaffs2 but the marker and /yaffs2/upgrade, since 1.13) through a's write allowlist, reads the file back and
// compares its CRC-32. a's read-write window stays open for the next
// write; settle_writes closes it. Reply: u32 status, u32 bytes written, u32
// read-back CRC-32.
void file_write(uint8_t seq, const uint8_t* payload, U32 length) {
    U32 code = P::s_ok;
    uint32_t written = 0, back = 0;
    U32 path_bytes = 0;
    while (path_bytes < length && payload[path_bytes]) ++path_bytes;
    const char* path = reinterpret_cast<const char*>(payload);
    if (path_bytes + 1 + 32 != length) code = P::s_bad_request;
    else if (!storage_server::valid_write_path(path)) code = P::s_refused;
    else if (!result.put_ok || !put_expected) code = P::s_state;
    else if (!sha_equal(payload + path_bytes + 1, put_sha)) code = P::s_sha;
    // Test 066: the mount is shared. A request left past its bound (stale) is closed first
    // (op 9: A closes b's files and its window); op 9 must reach a in RPC mode (read allowlist).
    else if (mounted == file_client::mounted_stale && !read_enabled &&
             !(read_enabled = menu_link::usb_command(storage_server::command_item)))
        code = P::s_link;
    else if (mounted == file_client::mounted_stale && (file_client::release(mounted), false)) code = P::s_state;
    else if (!menu_link::usb_command(storage_server::file_write_item)) code = P::s_link;
    else {
        storage_used = 1;
        say("Writing a library file...", path, "Do not unplug or power off.");
        // Every request bounded, the guard reloaded while b waits (never paused). A's
        // window stays open for the next write; settle_writes closes it.
        code = file_client::write_file(path, staged, put_expected, result.put_crc, written, back, mounted);
        writes_due = 1; writes_ms = now();
        say(code == P::s_ok ? "Library file written and verified." : "Library file write FAILED.", path);
    }
    uint8_t p[12]; P::put32(p, code); P::put32(p + 4, written); P::put32(p + 8, back);
    result.last_status = code;
    send(P::t_file_write, seq, p, 12);
}

// ---- The mod assembly job on B (native/mod_job; decision 019's flow, run on B) ----------------
// mods.apply starts it, B's menu loop runs it in slices of steps (job_pump), job.status reads it and
// job.cancel stops it. Its state and work region live in XYF_JOB_REGION (cold_layout.hpp: 8 MiB on B, the
// builder's memory map keeps everything else out). Every file call is one bounded file-client request
// (reads and writes 1 KiB: mod_job accepts short reads and writes), and every path is limited here: reads
// only under the generation folder, the base entry folder, the output folder and the one stock catalog
// file; writes, mkdir, remove and rename only under the output folder /yaffs2/xy-forever/tmp/job-<id>/
// (and through the engine's own policy, which A applies again). One job at a time; while it runs, every
// command that needs A, the staging or a boot answers busy (job_blocks). A request that runs out its bound
// (or a lost link) fails the job with error link at once: later calls send nothing. A failed or cancelled
// job removes its files (mod_job) and folders (wipe_step); debris = 1 when that could not be done. B
// closes A's write window when the job ends (settle_writes).
// Before the job's first step B sweeps leftover tmp/job-* folders (js_sweep); after the last, it commits the entry into the library (decision 019
// section 5, run by B): the target id (job_target, chosen before anything is written) is never the default
// or the boot-once entry; the old entry is renamed into tmp/job-<id>-old, the new one onto the target, then
// the old tree is removed. One request (or one list page and one change) per loop pass; A rescans the
// library when B closes the window (settle_begin, as after a put).
namespace mj = xyf_core::mod_job;
const U32 job_slot_bytes = 0x10000u, job_slice_ms = 20, job_ids = 32, job_say_ms = 1000;
#if defined(XYF_MTP_SESSION)
const U32 mtp_slot_bytes = 0x10000u, job_work_end = XYF_JOB_REGION_END - mtp_slot_bytes;   // the MTP session's slot above
#else
const U32 job_work_end = XYF_JOB_REGION_END;
#endif
const U32 max_job_machines = 64, max_job_extensions = 8, job_link_error = 21;
const char job_firmware[] = "/yaffs2/xy-forever/firmware/", job_mods[] = "/yaffs2/xy-forever/mods/";
const char job_tmp[] = "/yaffs2/xy-forever/tmp", job_catalog[] = "/yaffs2/content/op1_factory.json";
enum JobState : U32 { job_none = 0, job_busy, job_ended };
// Removing one folder tree under tmp/ in bounded steps (tree_step), and the sweep of leftovers.
struct Tree { char path[file_engine::max_path]; U32 root; };
struct Sweep { Tree tree; U32 removing, skip, calls; char keep[file_engine::max_path]; };
enum JobStage : U32 { js_sweep = 0, js_assemble, js_away, js_in, js_clean, js_wipe };   // js_wipe
struct JobSlot {
    mj::Job job;
    mj::Input input;
    mj::Files calls;
    const char* machines[max_job_machines];
    const char* extensions[max_job_extensions];
    char text[1536];                                                     // the request's ids and name
    char base[file_engine::max_path], stock[file_engine::max_path], official[file_engine::max_path];
    char generation[file_engine::max_path], out[file_engine::max_path], sweep[file_engine::max_path];
    char line[48];
    U32 id, started_ms, ended_ms, longest_ms, link, debris, said_ms, said_phase;
    uint8_t page[read_range_max];                                        // the tree steps' listing
    // The commit and the sweep.
    char target[8], entry[file_engine::max_path], old[file_engine::max_path];
    U32 stage, had_old, commit_error, commit_code, extra_steps, clean_calls;
    Tree tree;                                                           // the old entry's tree in tmp/
    Sweep leftovers;
    char state_text[xyf::store::max_text];
};
static_assert(sizeof(JobSlot) <= job_slot_bytes, "the job's state fits before its work region");
volatile U32 job_state __attribute__((section(".data"))) = job_none;
volatile U32 job_counter __attribute__((section(".data"))) = 0;
volatile U32 job_last_target __attribute__((section(".data"))) = 0;   // 1 mods-a, 2 mods-b (this boot)
volatile U32 job_kind __attribute__((section(".data"))) = 0;          // 0 mods.apply, 1 content switch, 2 config, 4 MTP session (3 reserved: record 004)
volatile U32 job_usb __attribute__((section(".data"))) = 0;           // the job was started over USB
JobSlot& slot() { return *reinterpret_cast<JobSlot*>(XYF_JOB_REGION_BEGIN); }
bool job_running() { return job_state == job_busy; }
#if defined(XYF_MTP_SESSION)
bool mtp_busy();                                                        // an MTP operation in flight
#define XYF_OR_MTP_BUSY || mtp_busy()
#define XYF_AND_NOT_MTP_BUSY && !mtp_busy()
#else
#define XYF_OR_MTP_BUSY                                                 // the product: the same tokens as before
#define XYF_AND_NOT_MTP_BUSY
#endif
bool job_blocks(U32 type) {                                             // also while settle_begin's op 9 is out
    if (!job_running() && !closing XYF_AND_NOT_MTP_BUSY) return false;
    return type == P::t_read_file || type == P::t_put_begin || type == P::t_put_data || type == P::t_put_end ||
           type == P::t_file_write || type == P::t_list_dir || type == P::t_delete_file || type == P::t_remove_dir ||
           type == P::t_make_dir || type == P::t_rename_file || type == P::t_copy_file || type == P::t_install ||
           type == P::t_uninstall || type == P::t_reboot || type == P::t_continue || type == P::t_library_boot ||
           type == P::t_mods_apply || type == P::t_mtp_test || type == P::t_set_default || type == P::t_tfw_check;
}
// String helpers (no library calls).
U32 text_bytes(const char* s) { U32 n = 0; while (s[n]) ++n; return n; }
bool join(char* out, const char* a, const char* b, const char* c = "") {
    const char* parts[3] = {a, b, c};
    U32 n = 0;
    for (U32 k = 0; k != 3; ++k)
        for (const char* s = parts[k]; *s; ++s) { if (n + 1 >= file_engine::max_path) return false; out[n++] = *s; }
    out[n] = 0;
    return true;
}
U32 decimal_text(char* at, U32 v) {                                     // no division (no libgcc)
    static const U32 tens[10] = {1000000000u, 100000000u, 10000000u, 1000000u, 100000u, 10000u, 1000u, 100u, 10u, 1u};
    U32 n = 0;
    for (U32 k = 0; k != 10; ++k) {
        char d = '0';
        while (v >= tens[k]) { v -= tens[k]; ++d; }
        if (d != '0' || n || k == 9) at[n++] = d;
    }
    at[n] = 0;
    return n;
}
bool inside(const char* path, const char* folder) {                    // folder/... (folder without its '/')
    U32 i = 0;
    for (; folder[i]; ++i) if (path[i] != folder[i]) return false;
    return path[i] == '/' && path[i + 1];
}
bool job_read_ok(const JobSlot& s, const char* path) {
    if (!storage_server::valid_path(path)) return false;
    if (inside(path, s.generation) || inside(path, s.base) || inside(path, s.out)) return true;
    const char* catalog = s.input.catalog;                              // or the saved official copy
    U32 i = 0;
    while (catalog[i] && path[i] == catalog[i]) ++i;
    return !catalog[i] && !path[i];
}
bool job_write_ok(const JobSlot& s, const char* path) {
    return storage_server::valid_path(path) && inside(path, s.out) && storage_server::valid_write_path(path);
}
// One printable token: 1-31 of a-z 0-9 _ - (and '.' when dot), a letter or digit first.
bool job_token(const char* t, bool dot) {
    U32 n = 0;
    for (; t[n]; ++n) {
        const char c = t[n];
        const bool alnum = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9');
        if (n >= 31 || !(alnum || c == '_' || c == '-' || (dot && c == '.')) || (!n && !alnum)) return false;
    }
    return n != 0;
}
// A comma-separated id list (text, NUL-terminated) split in place: ids into out, false when one is not a
// library id or there are more than max (an empty text: none).
bool job_ids_split(char* text, const char** out, U32 max, U32& count) {
    count = 0;
    if (!*text) return true;
    for (char* at = text;;) {
        if (count == max) return false;
        out[count++] = at;
        while (*at && *at != ',') ++at;
        const bool more = *at == ',';
        *at = 0;
        if (!xyf::store::valid_id(out[count - 1])) return false;
        if (!more) return true;
        ++at;
    }
}
// ---- the file calls (mod_job::Files), one request each
JobSlot& of(void* c) { return *static_cast<JobSlot*>(c); }
int job_fail(JobSlot& s, const file_client::Reply& r) {
    if (r.outcome == file_client::link) mounted = file_client::mounted_stale;
    if (r.outcome != file_client::refused) s.link = 1;                // nothing more is sent
    return -1;
}
int job_size(void* c, const char* path, xyf_core::U32* bytes) {
    JobSlot& s = of(c);
    if (s.link || !job_read_ok(s, path)) return -1;
    uint8_t record[file_client::record_bytes];
    const file_client::Reply r = file_client::stat(path, record);       // op 0x22
    if (r.outcome) return job_fail(s, r);
    if (r.value != 0 || (P::get32(record + 8) & 0xf000u) != 0x8000u) return -1;
    *bytes = P::get32(record + 0x1c);
    return 0;
}
int job_open(void* c, const char* path, bool write) {
    JobSlot& s = of(c);
    if (s.link || !(write ? job_write_ok(s, path) : job_read_ok(s, path))) return -1;
    const file_client::Reply r = file_client::open(path, write ? storage_server::write_open_flags : 0u);   // op 0x29
    if (r.outcome) return job_fail(s, r);
    return r.value < 0 ? -1 : r.value;
}
int job_read(void* c, int handle, xyf_core::Byte* data, xyf_core::U32 bytes) {
    JobSlot& s = of(c);
    if (s.link || handle < 0) return -1;
    const U32 n = bytes < file_client::chunk ? bytes : file_client::chunk;
    if (!n) return 0;
    const file_client::Reply r = file_client::read(handle, data, n);    // op 0x2a
    if (r.outcome) return job_fail(s, r);
    return r.value < 0 ? -1 : U32(r.value) > n ? int(n) : r.value;
}
int job_write(void* c, int handle, const xyf_core::Byte* data, xyf_core::U32 bytes) {
    JobSlot& s = of(c);
    if (s.link || handle < 0) return -1;
    const U32 n = bytes < file_client::chunk ? bytes : file_client::chunk;
    if (!n) return 0;
    const file_client::Reply r = file_client::write(handle, data, n);   // op 0x2b
    if (r.outcome) return job_fail(s, r);
    return r.value < 0 || U32(r.value) > n ? -1 : r.value;
}
int job_close(void* c, int handle) {
    JobSlot& s = of(c);
    if (s.link || handle < 0) return -1;
    const file_client::Reply r = file_client::close(handle);            // op 0x2d
    if (r.outcome) return job_fail(s, r);
    return r.value ? -1 : 0;
}
int job_rename(void* c, const char* from, const char* to) {
    JobSlot& s = of(c);
    if (s.link || !job_write_ok(s, from) || !job_write_ok(s, to)) return -1;
    uint32_t error = 0;
    const uint32_t code = file_client::rename(from, to, error, mounted); // op 0x21 twice
    if (code == P::s_link || code == P::s_state) s.link = 1;
    return code == P::s_ok ? 0 : -1;
}
int job_remove(void* c, const char* path) {
    JobSlot& s = of(c);
    if (s.link || !job_write_ok(s, path)) return -1;
    const file_client::Reply r = file_client::unlink(path);             // op 0x25
    if (r.outcome) return job_fail(s, r);
    return r.value < 0 ? -1 : 0;
}
int job_mkdir(void* c, const char* path) {
    JobSlot& s = of(c);
    if (s.link || !job_write_ok(s, path)) return -1;
    const file_client::Reply r = file_client::mkdir(path);              // op 0x26
    if (r.outcome) return job_fail(s, r);
    if (r.value >= 0) return 0;
    if (r.error != file_client::eexist) return -1;
    uint8_t record[file_client::record_bytes];                           // there already: a folder?
    const file_client::Reply q = file_client::stat(path, record);
    if (q.outcome) return job_fail(s, q);
    return q.value == 0 && (P::get32(record + 8) & 0xf000u) == 0x4000u ? 0 : -1;
}
// ---- Bounded tree removal under tmp/, the leftover sweep, the target id and the commit
const U32 one_entry = 6 + file_client::max_name;                         // a file.list page of one entry
const U32 sweep_cap = 600, clean_cap = 600, wipe_cap = 1024;             // calls per sweep, old tree, output
enum TreeStep : U32 { tree_more = 0, tree_done, tree_failed };
bool same_text(const char* a, const char* b) { while (*a && *a == *b) ++a, ++b; return *a == *b; }
bool tmp_ok(const char* path) {
    return storage_server::valid_path(path) && inside(path, job_tmp) && storage_server::valid_write_path(path);
}
bool tree_begin(Tree& t, const char* folder) {
    if (!tmp_ok(folder) || !join(t.path, folder, "")) return false;
    t.root = text_bytes(t.path);
    return true;
}
// One step, depth first, the root last: a page of one entry, then its unlink, the empty folder's rmdir or
// nothing (a folder: into it). At most one change per call; only paths under tmp/.
U32 tree_change(U32 op, const char* path) {
    uint32_t error = 0;
    U32 code = enter_writes();
    if (code == P::s_ok) { storage_used = 1; code = file_client::change(op, path, error, mounted); }
    writes_due = 1; writes_ms = now();
    return code;
}
U32 tree_step(Tree& t, uint8_t* page) {
    uint32_t count = 0, more = 0, bytes = 0;
    if (file_client::list(t.path, 0, page, one_entry, count, more, bytes, mounted) != P::s_ok) return tree_failed;
    U32 n = text_bytes(t.path);
    if (!count) {
        if (tree_change(0x27u, t.path) != P::s_ok) return tree_failed;   // rmdir
        if (n == t.root) return tree_done;
        while (n > t.root && t.path[n] != '/') --n;
        t.path[n] = 0;
        return tree_more;
    }
    const U32 name = page[5];
    if (!name || n + 1 + name + 1 > file_engine::max_path) return tree_failed;
    t.path[n] = '/';
    for (U32 i = 0; i != name; ++i) t.path[n + 1 + i] = char(page[6 + i]);
    t.path[n + 1 + name] = 0;
    if (!tmp_ok(t.path)) return tree_failed;
    if (page[0] == 1) return tree_more;                                  // a folder: into it
    const U32 code = tree_change(0x25u, t.path);                         // unlink
    t.path[n] = 0;
    return code == P::s_ok ? tree_more : tree_failed;
}
// The leftover sweep: tmp/'s entries one per call; a folder named job-* (not keep, the running job's output)
// is removed by tree_step. False: the sweep ended (nothing left, a failure, or sweep_cap calls).
bool sweep_step(Sweep& w, uint8_t* page) {
    if (++w.calls > sweep_cap) return false;
    if (w.removing) {
        const U32 t = tree_step(w.tree, page);
        if (t == tree_done) w.removing = 0;
        return t != tree_failed;
    }
    uint32_t count = 0, more = 0, bytes = 0;
    if (file_client::list(job_tmp, w.skip, page, one_entry, count, more, bytes, mounted) != P::s_ok || !count)
        return false;
    char path[file_engine::max_path], name[file_client::max_name + 1];
    const U32 n = page[5];
    for (U32 i = 0; i != n; ++i) name[i] = char(page[6 + i]);
    name[n] = 0;
    const bool job = page[0] == 1 && n > 4 && name[0] == 'j' && name[1] == 'o' && name[2] == 'b' && name[3] == '-';
    if (job && join(path, job_tmp, "/", name) && !same_text(path, w.keep) && tree_begin(w.tree, path)) w.removing = 1;
    else ++w.skip;
    return true;
}
void sweep_begin(Sweep& w, const char* keep) {
    w.removing = w.skip = w.calls = 0;
    join(w.keep, keep, "");
}
// Decision 019 section 5 (1): the mods id that is neither the default nor the boot-once entry; of two, the
// absent one, else the one not written last this boot (B sees no file times), else mods-a. Reads the
// state slots (as A's scan: a, b, legacy) and stats both folders; s_state when both ids are taken.
const char* const job_ids_mods[2] = {"mods-a", "mods-b"};
U32 job_choice(JobSlot& s, xyf::state::Choice& choice) {                // the state slots, as A's scan
    namespace st = xyf::state;
    for (U32 i = 0; i != sizeof(choice); ++i) reinterpret_cast<char*>(&choice)[i] = 0;
    static const char* const files[3] = {"/yaffs2/xy-forever/state-a.txt", "/yaffs2/xy-forever/state-b.txt",
                                         "/yaffs2/xy-forever/state.txt"};
    static const st::Source sources[3] = {st::source_a, st::source_b, st::source_legacy};
    for (U32 k = 0; k != 3; ++k) {
        uint32_t bytes = 0;
        const U32 code = file_client::read_file(files[k], 0, false, reinterpret_cast<uint8_t*>(s.state_text),
                                                xyf::store::max_text, bytes, mounted);
        if (code == P::s_ok) st::consider(choice, sources[k], s.state_text, bytes);
        else if (code != P::s_not_found && code != P::s_too_large) return code;
    }
    return P::s_ok;
}
U32 job_target(JobSlot& s) {
    xyf::state::Choice choice;
    if (const U32 code = job_choice(s, choice)) return code;
    bool open[2], present[2];
    for (U32 i = 0; i != 2; ++i) {
        open[i] = !same_text(job_ids_mods[i], choice.state.default_id) && !same_text(job_ids_mods[i], choice.state.once);
        char path[file_engine::max_path];
        join(path, job_firmware, job_ids_mods[i]);
        uint8_t record[file_client::record_bytes];
        const file_client::Reply r = file_client::stat(path, record);
        if (r.outcome) { if (r.outcome == file_client::link) mounted = file_client::mounted_stale; return file_client::status(r.outcome); }
        present[i] = r.value == 0;
    }
    const int pick = open[0] && open[1] ? (!present[0] ? 0 : !present[1] ? 1 : job_last_target == 1 ? 1 : 0) :
                     open[0] ? 0 : open[1] ? 1 : -1;
    if (pick < 0) return P::s_state;
    join(s.target, job_ids_mods[pick], "");
    join(s.entry, job_firmware, s.target);
    return P::s_ok;
}
// ---- The save-time log (xyf_save_log.hpp): the times of the running Save, USB build or switch ----------
namespace xl = xyf::save_log;
xl::Times save_times __attribute__((section(".data"))) = {};
volatile U32 save_active __attribute__((section(".data"))) = 0;   // a menu Save holds save_times
void save_lap(U32 part) { xl::lap(save_times, now(), part); }
void save_log_write(bool ok);                                           // after the content switch's slot
#if !defined(XYF_CONTENT_SWITCH)
void save_log_write(bool) { save_times.kind = xl::k_none; save_active = 0; }
#endif
void job_text(JobSlot& s, const char* a, const char* b = "") {          // the job's detail (job.status)
    U32 n = 0;
    for (const char* t = a; *t && n + 1 < mj::detail_bytes; ++t) s.job.detail[n++] = *t;
    for (const char* t = b; *t && n + 1 < mj::detail_bytes; ++t) s.job.detail[n++] = *t;
    s.job.detail[n] = 0;
}
void job_finish(JobSlot& s);
// A commit step failed: error rename (17, code A's error number) or link; nothing more is sent after a link.
// Past the first rename (the old entry in tmp/), B renames it back, so the old entry stays usable.
void commit_fail(JobSlot& s, U32 code, uint32_t error, const char* step) {
    if (code == P::s_link) s.link = 1;
    s.commit_error = s.link ? job_link_error : U32(mj::Error::rename);
    s.commit_code = error ? error : code;
    job_text(s, "commit ", step);
    if (s.stage == js_in && s.had_old && !s.link) {
        uint32_t e = 0;
        if (file_client::rename(s.old, s.entry, e, mounted) == P::s_ok) s.had_old = 0;
    }
    job_finish(s);
}
void commit_done(JobSlot& s) {
    job_last_target = s.target[5] == 'a' ? 1u : 2u;
    job_text(s, s.target);
    job_finish(s);
}
// One commit step per loop pass (js_away, js_in, js_clean).
void commit_step(JobSlot& s) {
    uint32_t error = 0;
    U32 code = P::s_ok;
    ++s.extra_steps;
    if (s.stage == js_away) {                                            // the old entry into tmp/, if any
        xyf::state::Choice choice;                                       // still neither default nor once?
        if ((code = job_choice(s, choice)) != P::s_ok) return commit_fail(s, code, 0, "state");
        if (same_text(s.target, choice.state.default_id) || same_text(s.target, choice.state.once))
            return commit_fail(s, P::s_state, 0, "taken");
        uint8_t record[file_client::record_bytes];
        const file_client::Reply r = file_client::stat(s.entry, record);
        if (r.outcome) { if (r.outcome == file_client::link) mounted = file_client::mounted_stale; code = file_client::status(r.outcome); }
        else if (r.value != 0) { s.stage = js_in; save_lap(xl::p_in); return; }
        else if ((code = file_client::rename(s.entry, s.old, error, mounted)) == P::s_ok) { s.had_old = 1; s.stage = js_in; save_lap(xl::p_in); return; }
        return commit_fail(s, code, error, "away");
    }
    if (s.stage == js_in) {                                              // the new entry onto the target
        if ((code = file_client::rename(s.out, s.entry, error, mounted)) != P::s_ok) return commit_fail(s, code, error, "in");
        writes_ms = now();
        if (!s.had_old) return commit_done(s);
        s.stage = js_clean; save_lap(xl::p_clean);
        if (!tree_begin(s.tree, s.old)) { s.debris = 1; return commit_done(s); }
        return;
    }
    const U32 t = tree_step(s.tree, s.page);                             // js_clean: the old tree
    if (t == tree_more && ++s.clean_calls < clean_cap) return;
    if (t != tree_done) s.debris = 1;                                    // left for the next sweep
    commit_done(s);
}
const char* job_phase_text(U32 phase) { return mj::phase_name(mj::Phase(phase)); }
void job_say(JobSlot& s, bool force) {
    mj::Progress p;
    mj::progress(s.job, p);
    const U32 t = now();
    if (!force && U32(p.phase) == s.said_phase && t - s.said_ms < job_say_ms) return;
    s.said_phase = U32(p.phase); s.said_ms = t;
    U32 n = 0;
    for (const char* a = job_phase_text(U32(p.phase)); *a && n < 20; ++a) s.line[n++] = *a;
    s.line[n++] = ' ';
    n += decimal_text(s.line + n, p.done);
    if (p.total) { s.line[n++] = '/'; n += decimal_text(s.line + n, p.total); }
    s.line[n] = 0;
    say("Assembling mod firmware...", s.line, "Do not unplug or power off.");
}
// The job ended (done, failed or cancelled): a failed one's folders removed, A's window closed (A rescans the
// library: a committed entry is listed).
// Review 024, 6: a failed one's output is removed by job_pump, one tree_step per loop pass (stage
// js_wipe, "cleaning up": the job still owns the slot, so storage commands answer busy), then job_end.
void job_end(JobSlot& s) {
    const bool done = s.job.phase == mj::Phase::done && !s.commit_error;
    job_state = job_ended;
    save_lap(xl::p_close);
    if (!save_active && !s.link) save_log_write(done);                  // a USB build's line, in its window
    settle_begin();                                                      // op 9 (B's loop stays live), catalog
    if (done) say("Mod firmware added to the library.", s.entry + 19, "Its content is not installed yet.");
    else say(s.job.phase == mj::Phase::cancelled ? "Mod firmware: cancelled." : "Mod firmware assembly FAILED.",
             s.out + 19, s.debris ? "Leftovers stay in tmp/." : "");
}
void job_finish(JobSlot& s) {
    s.ended_ms = now();
    if (s.job.phase == mj::Phase::done && !s.commit_error) return job_end(s);
    if (s.link || !tree_begin(s.tree, s.out)) { s.debris = 1; return job_end(s); }   // nothing sent after a link
    s.stage = js_wipe; s.clean_calls = 0; s.debris = 0; save_lap(xl::p_clean);
    say("Mod firmware: cleaning up...", s.out + 19, "Do not unplug or power off.");
}
void wipe_step(JobSlot& s) {                                             // js_wipe: one step of the output's tree
    ++s.extra_steps;
    const U32 t = tree_step(s.tree, s.page);
    if (t == tree_more && ++s.clean_calls < wipe_cap) return;
    s.debris = t == tree_done ? 0u : 1u;
    job_end(s);
}
#if defined(XYF_CONTENT_SWITCH)
void content_pump();
void config_pump();
#endif
void job_pump() {
    if (!job_running()) return;
#if defined(XYF_CONTENT_SWITCH)
    if (job_kind == 2) return config_pump();                             // the config job
    if (job_kind) return content_pump();
#endif
    JobSlot& s = slot();
    const U32 start = now();
    if (s.stage == js_sweep) {                                           // leftovers first
        ++s.extra_steps;
        if (!sweep_step(s.leftovers, s.page)) s.stage = js_assemble;
    } else if (s.stage == js_assemble) {
        mj::Phase p;
        do {
            p = mj::step(s.job);
            const U32 part = xl::p_job_base + mj::timing_part(s.job);
            if (save_times.kind && part != save_times.current) save_lap(part);
        } while (p != mj::Phase::done && p != mj::Phase::failed && p != mj::Phase::cancelled && now() - start < job_slice_ms);
        if (p == mj::Phase::done) {
            s.stage = js_away; job_text(s, "commit"); say("Adding to the library...", s.target);
            save_lap(xl::p_away); save_times.entry_files = s.job.out_count;
        }
        else if (p == mj::Phase::failed || p == mj::Phase::cancelled) job_finish(s);
        else job_say(s, false);
    } else if (s.stage == js_wipe) wipe_step(s);                         // a failed job's output
    else commit_step(s);                                                 // into the library
    const U32 pass = now() - start;
    if (pass > s.longest_ms) s.longest_ms = pass;
}
#if defined(XYF_CONTENT_SWITCH)
// ---- The content switch (xyf_content_job.hpp) as the job slot's second kind (job_kind 1) ----------
// library.set_default or the menu's SHIFT+PLAY on an entry whose content is not installed starts it: the same
// single-job rule (job_state; job_blocks answers busy), job.status reports it, job.cancel answers state (a
// switch is repaired by running it again, not undone). Its state follows the mod job's slot, its work region
// after that. One file request per step, steps in slices of job_slice_ms. At the end B closes A's write window
// (A rescans: the catalog shows the new state), then, after a success, writes the default (default_commit).
// Every write passed xyf::content::allowed (the job), the USB write area (ContentIo) and A's engine.
namespace xc = xyf::content;
struct ContentIo {
    U32 link;
    int fail(const file_client::Reply& r) {
        if (r.outcome == file_client::link) mounted = file_client::mounted_stale;
        if (r.outcome != file_client::refused) link = 1;                // nothing more is sent
        return -1;
    }
    static bool writable(const char* p) { return storage_server::own_area(p) && storage_server::valid_write_path(p); }
    int open(const char* p, bool write) {
        if (link || !storage_server::valid_path(p) || (write && !writable(p))) return -1;
        const file_client::Reply r = file_client::open(p, write ? storage_server::write_open_flags : 0u);
        if (r.outcome) return fail(r);
        return r.value >= 0 ? r.value : !write && r.error == file_client::enoent ? -2 : -1;
    }
    int read(int fd, uint8_t* out, U32 n) {
        if (link || fd < 0 || !n || n > file_client::chunk) return -1;
        const file_client::Reply r = file_client::read(fd, out, n);
        if (r.outcome) return fail(r);
        return r.value < 0 || U32(r.value) > n ? -1 : r.value;
    }
    int write(int fd, const uint8_t* data, U32 n) {
        if (link || fd < 0 || !n || n > file_client::chunk) return -1;
        const file_client::Reply r = file_client::write(fd, data, n);
        if (r.outcome) return fail(r);
        return r.value < 0 || U32(r.value) > n ? -1 : r.value;
    }
    int close(int fd) {
        if (link || fd < 0) return -1;
        const file_client::Reply r = file_client::close(fd);
        if (r.outcome) return fail(r);
        return r.value ? -1 : 0;
    }
    int unlink(const char* p) {
        if (link || !writable(p)) return -1;
        const file_client::Reply r = file_client::unlink(p);
        if (r.outcome) return fail(r);
        return r.value >= 0 ? 0 : r.error == file_client::enoent ? 1 : -1;
    }
    int rmdir(const char* p) {
        if (link || !writable(p)) return -1;
        const file_client::Reply r = file_client::rmdir(p);
        if (r.outcome) return fail(r);
        return r.value >= 0 || r.error == file_client::enoent ? 0 : r.error == file_client::enotempty ? 1 : -1;
    }
    int mkdir(const char* p) {
        if (link || !writable(p)) return -1;
        const file_client::Reply r = file_client::mkdir(p);
        if (r.outcome) return fail(r);
        if (r.value >= 0) return 0;
        if (r.error != file_client::eexist) return -1;
        uint8_t record[file_client::record_bytes];                       // there already: a folder?
        const file_client::Reply q = file_client::stat(p, record);
        if (q.outcome) return fail(q);
        return q.value == 0 && (P::get32(record + 8) & 0xf000u) == 0x4000u ? 0 : -1;
    }
    int rename(const char* from, const char* to) {
        if (link) return -1;
        uint32_t error = 0;
        const U32 code = file_client::rename(from, to, error, mounted);
        if (code == P::s_link || code == P::s_state) link = 1;
        return code == P::s_ok ? 0 : -1;
    }
    bool lost() const { return link != 0; }
};
enum ContentStage : U32 { cs_switch = 0, cs_closing, cs_ended, cs_opening };
// 193: the switch opens A's write window first without waiting in it (B keeps answering USB: job.status
// says "opening storage for writing"); the first read-write mount after an unclean shutdown is slow.
const char window_folder[] = "/yaffs2/xy-forever/tmp";
const char opening_text[] = "opening storage for writing";
struct ContentSlot {
    xc::Job job;
    ContentIo io;
    U32 id, started_ms, ended_ms, longest_ms, stage, menu, said_ms, said_phase, line_seq;
    U32 write_default;                                                   // 0 for the save sequence's switch
    char line[64], progress[24];
};
const U32 content_slot_bytes = (sizeof(ContentSlot) + 63u) & ~63u;
// One line appended to the log inside A's open write window (before its close); every failure ignored.
void save_log_write(bool ok) {
    if (save_times.kind && writes_due && a_serving()) {
        save_times.ok = ok ? 1u : 0u;
        char* const text = reinterpret_cast<char*>(XYF_JOB_REGION_BEGIN + job_slot_bytes + content_slot_bytes + 0x10000u);
        char* const line = text + xl::max_text + 64;
        const U32 line_n = xl::format(line, xl::max_line, save_times, now());
        uint32_t error = 0, old_n = 0;
        file_client::change(0x26u, xl::folder, error, mounted);           // mkdir (it may exist)
        if (file_client::read_file(xl::path, 0, false, reinterpret_cast<uint8_t*>(text), xl::max_text, old_n, mounted) != P::s_ok)
            old_n = 0;
        const U32 n = line_n ? xl::merge(text, old_n, line, line_n) : 0u;
        const file_client::Reply o = n ? file_client::open(xl::part_path, storage_server::write_open_flags) : file_client::Reply();
        bool written = n && !o.outcome && o.value >= 0;
        for (U32 at = 0; written && at != n;) {
            const U32 want = n - at < file_client::chunk ? n - at : file_client::chunk;
            const file_client::Reply w = file_client::write(o.value, reinterpret_cast<const uint8_t*>(text + at), want);
            written = !w.outcome && w.value > 0 && U32(w.value) <= want;
            if (written) at += U32(w.value);
        }
        if (n && !o.outcome && o.value >= 0) written = !file_client::close(o.value).outcome && written;
        if (written) {
            file_client::change(0x25u, xl::path, error, mounted);           // unlink: A never renames onto a file
            file_client::rename(xl::part_path, xl::path, error, mounted);
        }
    }
    save_times.kind = xl::k_none; save_active = 0;
}
ContentSlot& cslot() { return *reinterpret_cast<ContentSlot*>(XYF_JOB_REGION_BEGIN + job_slot_bytes); }
volatile U32 content_line_shown __attribute__((section(".data"))) = 0;
U32 content_phase(const ContentSlot& c) {                                 // final only once the default is written
    const U32 ph = xc::phase(c.job);
    return c.stage != cs_ended && ph >= xc::phase_done ? U32(xc::phase_finish) : ph;
}
void content_text(ContentSlot& c, const char* a, const char* b = "", const char* d = "") {   // the menu's line
    U32 n = 0;
    for (const char* parts[3] = {a, b, d}, **k = parts; k != parts + 3; ++k)
        for (const char* t = *k; *t && n + 1 < sizeof(c.line); ++t) c.line[n++] = *t;
    c.line[n] = 0;
    ++c.line_seq;
}
void content_say(ContentSlot& c, bool force) {
    const U32 t = now(), ph = content_phase(c);
    if (c.stage == cs_ended) {
        if (c.job.stage == xc::st_done) {
            say("Default firmware set.", c.job.target, "Its content is installed.");
            content_text(c, "Default: ", c.job.target, " (content installed)");
        } else {
            say("Content switch FAILED.", c.job.detail, "Set the default again to repair.");
            content_text(c, "Content switch failed: ", c.job.detail);
        }
        return;
    }
    if (!force && ph == c.said_phase && t - c.said_ms < job_say_ms) return;
    c.said_phase = ph; c.said_ms = t;
    if (c.stage == cs_opening) {                                         // 193
        say("Opening storage for writing...", c.job.target, "", "Do not unplug or power off.");
        content_text(c, "Opening storage for writing: ", c.job.target);
        return;
    }
    U32 n = decimal_text(c.progress, c.job.done);
    c.progress[n++] = '/';
    decimal_text(c.progress + n, c.job.total);
    say("Installing content...", c.job.target, c.progress, "Do not unplug or power off.");
    content_text(c, "Installing content ", c.progress);
}
U32 content_begin(const char* id, bool menu, bool write_default) {
    if (job_running() || closing XYF_OR_MTP_BUSY || (put_expected && result.put_received != put_expected)) return P::s_busy;
    if (!a_serving()) return P::s_link;
    U32 code = file_client::mount_ready(mounted);
    if (code != P::s_ok) return code;
    ContentSlot& c = cslot();
    const xyf::catalog::Catalog* catalog = xyf_boot::catalog();         // every entry's content.sha256 is a candidate
    const char* candidates[xc::max_candidates];
    U32 n = 0;
    for (U32 i = 0; catalog && i != catalog->count && n != xc::max_candidates; ++i) candidates[n++] = catalog->items[i].id;
    uint8_t* const work = reinterpret_cast<uint8_t*>(XYF_JOB_REGION_BEGIN + job_slot_bytes + content_slot_bytes);
    if (!xc::start(c.job, id, candidates, n, work, job_work_end - XYF_JOB_REGION_BEGIN - job_slot_bytes - content_slot_bytes))
        return P::s_bad_request;
    if ((code = enter_writes()) != P::s_ok) return code;
    storage_used = 1;
    writes_due = 1; writes_ms = now();                                  // closed when the switch ends
    c.io.link = 0; c.id = ++job_counter; c.started_ms = c.ended_ms = now(); c.longest_ms = 0;
    c.stage = file_client::mkdir_start(window_folder).outcome ? cs_switch : cs_opening;   // 193: not sent: the first change opens it
    c.menu = menu ? 1u : 0u; c.said_ms = 0; c.said_phase = ~0u; c.write_default = write_default ? 1u : 0u;
    if (save_active) save_lap(xl::p_switch); else xl::begin(save_times, xl::k_switch, now(), xl::p_switch);
    job_kind = 1; job_state = job_busy; job_usb = menu ? 0u : 1u;
    content_say(c, true);
    return P::s_ok;
}
void content_pump() {
    ContentSlot& c = cslot();
    const U32 start = now();
    if (c.stage == cs_opening) {                                         // 193: A opens its write window
        const file_client::Reply r = file_client::unmount_poll();
        if (r.outcome == file_client::busy) { content_say(c, false); return; }
        if (r.outcome == file_client::link) mounted = file_client::mounted_stale;   // past its bound: still out
        if (r.outcome) c.io.link = 1;                                    // the switch fails (link), then closes
        c.stage = cs_switch;
        content_say(c, true);
    }
    if (c.stage == cs_switch) {
        U32 ph;
        do ph = xc::step(c.job, c.io);
        while (ph < xc::phase_done && now() - start < job_slice_ms);
        if (ph >= xc::phase_done) {                                      // op 9: A rescans, B fetches the catalog
            c.stage = cs_closing;
            save_times.compared = c.job.compared; save_times.read = c.job.read; save_times.written = c.job.written;
            save_times.removed_files = c.job.removed; save_times.bytes = c.job.bytes;
            save_lap(xl::p_switch_close);
            if (!save_active) save_log_write(c.job.stage == xc::st_done && !c.io.link);   // in its window
            settle_begin();
        }
        else content_say(c, false);
    } else if (c.stage == cs_closing && !closing) {
        if (c.job.stage == xc::st_done && c.write_default) {             // the default, only after a success
            const U32 code = default_commit(xyf::state_write::id_hash(c.job.target));
            if (code != P::s_ok) {
                c.job.stage = xc::st_failed; c.job.error = code == P::s_link ? xc::e_link : xc::e_write; c.job.code = code;
                xc::note(c.job, "default");
            }
        }
        c.ended_ms = now(); c.stage = cs_ended;
        job_state = job_ended;
        content_say(c, true);
    }
    const U32 pass = now() - start;
    if (pass > c.longest_ms) c.longest_ms = pass;
}
// The catalog mods.apply reads: the live one in the official state, else the saved official copy.
const char* content_job_catalog() {
    uint8_t head[xc::head_bytes];
    uint32_t bytes = 0;
    xc::State state;
    const U32 code = file_client::read_file(xc::record_path, 0, true, head, sizeof(head), bytes, mounted);
    if (code == P::s_not_found)                                          // only a .old: mid-switch, not official
        return file_client::read_file(xc::record_old, 0, true, head, sizeof(head), bytes, mounted) == P::s_ok ?
               xc::saved_catalog : job_catalog;
    if (code != P::s_ok) return job_catalog;
    xc::parse(head, bytes, state);
    return state.kind == xc::official ? job_catalog : xc::saved_catalog;
}
void config_status(uint8_t seq, U32 want);
// JOB_STATUS of a content switch: as the mod job's fields (phase inputs / content / finish; done, total: files).
void content_status(uint8_t seq, U32 want) {
    ContentSlot& c = cslot();
    uint8_t* p = files;
    const U32 code = want && want != c.id ? P::s_not_found : P::s_ok;
    P::put32(p, code);
    if (code != P::s_ok) { result.last_status = code; send(P::t_job_status, seq, p, 4); return; }
    const bool ended = c.stage == cs_ended;
    const U32 values[11] = {c.id, content_phase(c), c.job.done, c.job.total, ended ? c.job.error : 0u, ended ? c.job.code : 0u,
                            (ended ? c.ended_ms : now()) - c.started_ms, c.job.steps, c.longest_ms, c.job.peak, 0};
    for (U32 i = 0; i != 11; ++i) P::put32(p + 4 + 4 * i, values[i]);
    for (U32 i = 0; i != 32; ++i) p[48 + i] = 0;
    const char* detail = c.stage == cs_opening ? opening_text : c.job.detail;   // 193
    for (U32 i = 0; i != 31 && detail[i]; ++i) p[48 + i] = uint8_t(detail[i]);
    result.last_status = code;
    send(P::t_job_status, seq, p, 80);
}
#endif
#if defined(XYF_CONTENT_SWITCH)
// ---- The config job (job_kind 2): the saved configuration's record (xyf_config.hpp), then the default ---
// The save sequence's last step (xyf_mods_io): config.txt.part written, then config.txt -> config.old (A never
// renames onto a file), .part -> config.txt, config.old removed; A's window closed; then, after a success, the
// default (default_commit) unless it is that entry already. A failure or cut leaves the old record (or only its
// .old, which the reader takes) and the old default. One file request per step, as the content switch.
namespace xg = xyf::config;
struct ConfigSlot {
    ContentIo io;
    int fd;
    U32 id, step, at, length, error, code, had, started_ms, ended_ms, hash;
    const char *path, *old_path, *part_path;     // config.txt's, or settings.txt's (xyf_settings.hpp)
    char text[xg::max_text];
};
// In the content switch's work region (one job at a time; the switch has ended when this starts).
ConfigSlot& gslot() { return *reinterpret_cast<ConfigSlot*>(XYF_JOB_REGION_BEGIN + job_slot_bytes + content_slot_bytes); }
enum ConfigStep : U32 { cg_open = 0, cg_write, cg_close, cg_stat, cg_unlink_old, cg_away, cg_in, cg_drop, cg_closing, cg_ended };
U32 config_begin(const char* text, U32 length, const char* id, const char* path = xg::path,
                 const char* old_path = xg::old_path, const char* part_path = xg::part_path) {
    if (job_running() || closing XYF_OR_MTP_BUSY || (put_expected && result.put_received != put_expected)) return P::s_busy;
    if (!a_serving()) return P::s_link;
    if (!length || length > xg::max_text) return P::s_bad_request;
    U32 code = file_client::mount_ready(mounted);
    if (code != P::s_ok || (code = enter_writes()) != P::s_ok) return code;
    ConfigSlot& g = gslot();
    for (U32 i = 0; i != length; ++i) g.text[i] = text[i];
    g.path = path; g.old_path = old_path; g.part_path = part_path;
    g.length = length; g.at = 0; g.fd = -1; g.step = cg_open; g.error = g.code = g.had = 0; g.io.link = 0;
    g.hash = id && *id ? xyf::state_write::id_hash(id) : 0u;
    storage_used = 1;
    writes_due = 1; writes_ms = now();                                  // closed when the record is in
    g.id = ++job_counter; g.started_ms = g.ended_ms = now();
    job_kind = 2; job_state = job_busy; job_usb = 0;
    if (save_active && path == xg::path) save_lap(xl::p_config);       // a Save's record
    return P::s_ok;
}
void config_step(ConfigSlot& g) {
    const U32 failed = g.step;
    switch (g.step) {
    case cg_open:
        g.fd = g.io.open(g.part_path, true);
        if (g.fd >= 0) g.step = cg_write;
        break;
    case cg_write: {
        const U32 want = g.length - g.at < file_client::chunk ? g.length - g.at : file_client::chunk;
        const int put = g.io.write(g.fd, reinterpret_cast<const uint8_t*>(g.text + g.at), want);
        if (put > 0 && U32(put) <= want) { g.at += U32(put); if (g.at == g.length) g.step = cg_close; }
        break;
    }
    case cg_close:
        if (!g.io.close(g.fd)) g.step = cg_stat;
        g.fd = -1;
        break;
    case cg_stat: {
        uint8_t record[file_client::record_bytes];
        const file_client::Reply r = file_client::stat(g.path, record);
        if (r.outcome) { g.io.fail(r); break; }
        g.had = r.value == 0 ? 1u : 0u;
        g.step = g.had ? cg_unlink_old : cg_in;
        break;
    }
    case cg_unlink_old: if (g.io.unlink(g.old_path) >= 0) g.step = cg_away; break;   // a stale .old
    case cg_away: if (!g.io.rename(g.path, g.old_path)) g.step = cg_in; break;
    case cg_in: if (!g.io.rename(g.part_path, g.path)) g.step = g.had ? cg_drop : cg_closing; break;
    case cg_drop: if (g.io.unlink(g.old_path) >= 0) g.step = cg_closing; break;
    default: return;
    }
    if (g.step == failed) {                                             // this step failed: nothing more is written
        g.error = g.io.lost() ? xc::e_link : failed == cg_away || failed == cg_in ? xc::e_rename : xc::e_write;
        g.code = g.io.lost() ? P::s_link : P::s_storage;
        g.step = cg_closing;
    }
    if (g.step == cg_closing) {                                         // op 9: A rescans, B fetches the catalog
        if (save_active && g.path == xg::path) save_log_write(!g.error && !g.io.link);   // the Save's line
        settle_begin();
    }
}
void config_pump() {
    ConfigSlot& g = gslot();
    const U32 start = now();
    while (g.step < cg_closing && now() - start < job_slice_ms) config_step(g);
    if (g.step != cg_closing || closing) return;
    const xyf::catalog::Catalog* catalog = xyf_boot::catalog();
    const bool already = catalog && catalog->default_index < catalog->count &&
                         xyf::state_write::id_hash(catalog->items[catalog->default_index].id) == g.hash;
    if (!g.error && g.hash && !already) {
        const U32 code = default_commit(g.hash);
        if (code != P::s_ok) { g.error = code == P::s_link ? xc::e_link : xc::e_write; g.code = code; }
    }
    g.step = cg_ended; g.ended_ms = now();
    job_state = job_ended;
}
// JOB_STATUS of the config job: the switch's fields (phase 0 running, done 8, failed 9; done, total: steps).
void config_status(uint8_t seq, U32 want) {
    ConfigSlot& g = gslot();
    uint8_t* p = files;
    const U32 code = want && want != g.id ? P::s_not_found : P::s_ok;
    P::put32(p, code);
    if (code != P::s_ok) { result.last_status = code; send(P::t_job_status, seq, p, 4); return; }
    const bool ended = g.step == cg_ended;
    const U32 values[11] = {g.id, ended ? (g.error ? U32(xc::phase_failed) : U32(xc::phase_done)) : 0u, g.step, cg_ended,
                            ended ? g.error : 0u, ended ? g.code : 0u, (ended ? g.ended_ms : now()) - g.started_ms, g.step, 0, 0, 0};
    for (U32 i = 0; i != 11; ++i) P::put32(p + 4 + 4 * i, values[i]);
    for (U32 i = 0; i != 32; ++i) p[48 + i] = 0;
    const char detail[] = "config";
    for (U32 i = 0; detail[i]; ++i) p[48 + i] = uint8_t(detail[i]);
    result.last_status = code;
    send(P::t_job_status, seq, p, 80);
}
#endif
#if defined(XYF_MTP_SESSION)
#if defined(XYF_SIM)
}
namespace xyf_sim { void log(const char* format, ...); }                // the session screen in the log (sim/runtime.hpp)
namespace {
#endif
// ---- The MTP session (mtp_session.hpp). Not a job: enabled when the composite starts (the test build)
// or from the menu's MTP mode row / diag.mtp op 0 (the simulator), it stays until STOP / op 1 (session_leave:
// cancel, the open files closed one per pass, A's window closed) or the boot's end. Each pass runs responder
// steps for at most mtp_slice_ms (one file request each). Only an operation in flight holds the file client
// (mtp_busy): serial file commands, jobs and the menu's writes answer busy meanwhile, never wait; the reverse,
// an MTP operation that needs the files while a job, a window close or a serial upload runs, answers Device_Busy
// (mtp_prepare). An operation without USB progress for mtp_stall_ms is cancelled (the responder closes its file
// and removes a partial upload). A's write window: opened by the first change, kept while operations run, closed
// by the menu loop's settle window_settle_ms after the last one (as after serial writes).
namespace ms = xy_forever::mtp_session;
namespace mr = xyf_core::mtp;
ms::Session& mslot() { return *reinterpret_cast<ms::Session*>(job_work_end); }
static_assert(sizeof(ms::Session) <= mtp_slot_bytes, "the session fits its slot");
const uint8_t t_mtp_tunnel = 0x61;                                      // diag.mtp (dev, test only): the test transport
const U32 mtp_slice_ms = 8, mtp_stall_ms = 10000, mtp_serving_ms = 1000;
volatile U32 mtp_pass_ms __attribute__((section(".data"))) = 0;          // the composite's longest menu-loop pass
volatile U32 mtp_on __attribute__((section(".data"))) = 0;               // the session is enabled
uint32_t mtp_window() {                                                  // the first change: A's write window
    U32 code = enter_writes();
    if (code == P::s_ok) { storage_used = 1; writes_due = 1; writes_ms = now(); code = file_client::mount_ready(mounted); }
    return code;                                                         // then op 8 again if needed, as file.change
}
bool put_running() { return put_expected && result.put_received != put_expected; }
bool mtp_busy() {                                                        // an operation in flight, or the leave's closes
    if (!mtp_on) return false;
    const U32 stage = mslot().stage;
    return stage == ms::st_leaving || stage == ms::st_closing || (stage == ms::st_running && mslot().responder.busy());
}
xyf_core::U32 mtp_prepare(void*) {                                                 // before an operation that needs the files
    ms::Session& m = mslot();
    if (job_running() || closing || put_running()) { ++m.refused_busy; return mr::r_device_busy; }
    if (!writes_due) m.files.window = 0;                                 // the settle closed A's window
    // Speed (2026-11-15): A's check at most once per mtp_serving_ms (a request each; the open window
    // already skipped it).
    const bool serving = writes_due || (m.served_ms && now() - m.served_ms < mtp_serving_ms);
    if (!serving && a_serving()) m.served_ms = now() | 1u;
    if ((!serving && !m.served_ms) || file_client::mount_ready(mounted) != P::s_ok) {   // A gone or silent
        m.files.link = 1; m.served_ms = 0;
        return mr::r_device_busy;
    }
    m.files.link = 0;
    m.op_started_ms = now();
    return 0;
}
void mtp_log(void*, xyf_core::U32 code, const xyf_core::U32* params, xyf_core::U32 nparams, xyf_core::U32 response,
             xyf_core::U32 data) {
    ms::Session& m = mslot();
    uint32_t* e = m.log[m.log_count % ms::log_entries];
    e[0] = (code & 0xffffu) | response << 16; e[1] = params[0]; e[2] = params[1]; e[3] = params[2];
    e[4] = (data & 0xfu) | (nparams & 0xfu) << 4 | now() << 8;
    ++m.log_count;
    if (response && data < 3) {
        const U32 op_ms = now() - m.command_ms;                          // speed: the command to its response
        ++m.hist[op_ms < 2 ? 0 : op_ms < 5 ? 1 : op_ms <= 20 ? 2 : 3];
        ++m.operations;
        const U32 took = now() - m.op_started_ms;
        if (m.op_started_ms && took > m.longest_op_ms) m.longest_op_ms = took;
        m.op_started_ms = 0;
    }
}
void mtp_say(bool force) {
#if defined(XYF_SIM)
    static char mtp_numbers[2][12];
    ms::Session& m = mslot();
    const U32 t = now();
    if (!force && t - m.said_ms < job_say_ms) return;
    m.said_ms = t;
    decimal_text(mtp_numbers[0], m.bytes_in);
    decimal_text(mtp_numbers[1], m.bytes_out);
    ::xyf_sim::log("mtp screen: stage=%u in=%s out=%s link=%u", m.stage, mtp_numbers[0], mtp_numbers[1], m.files.link);
    if (m.stage == ms::st_ended) say("MTP mode ended.", m.files.link ? "Processor A did not answer." : "", "", "");
    else if (m.stage != ms::st_running) say("MTP mode", "Leaving...", "", "");
    else say("MTP mode", mtp_numbers[0], mtp_numbers[1], m.files.link ? "A lost. STOP to leave" : "STOP to leave");
#else
    (void)force;                                                         // the device: the menu stays (the MTP mark)
#endif
}
U32 session_enter() {
    if (mtp_on && mslot().stage != ms::st_ended) return P::s_busy;
    ms::Session& m = mslot();
    ms::files_init(m.files, &mounted, mtp_window);
    mr::FileOps ops = ms::file_ops(m.files);
    ops.prepare = mtp_prepare; ops.log = mtp_log;
    m.responder.init(ops);
    m.in.length = m.in.sent = 0;
    m.stage = ms::st_running; m.bytes_in = m.bytes_out = m.longest_ms = m.steps = m.cancels = m.said_ms = 0;
    m.stalls = m.refused_busy = m.log_count = m.operations = m.longest_op_ms = m.op_started_ms = 0;
    m.hist[0] = m.hist[1] = m.hist[2] = m.hist[3] = m.command_ms = m.served_ms = 0;
    m.started_ms = m.progress_ms = now();
    mtp_on = 1;
    mtp_say(true);
    return P::s_ok;
}
U32 session_leave() {
    if (!mtp_on || mslot().stage == ms::st_ended) return P::s_state;
    ms::Session& m = mslot();
    if (m.stage == ms::st_running) { m.responder.cancel(); m.stage = ms::st_leaving; mtp_say(true); }
    return P::s_ok;
}
void mtp_pump() {
    if (!mtp_on) return;
    ms::Session& m = mslot();
    const U32 start = now();
    if (m.stage == ms::st_running) {
        if (job_running()) m.responder.invalidate();                     // speed: the job changes files (cache)
        while (m.responder.step()) {                                     // one file request per step
            ++m.steps; m.progress_ms = now();
            if (now() - start >= mtp_slice_ms) break;
        }
        if (m.responder.busy()) {
            if (writes_due) writes_ms = now();                           // A's window stays while operations run
            if (now() - m.progress_ms > mtp_stall_ms) { m.responder.cancel(); ++m.stalls; m.progress_ms = now(); }
        } else m.progress_ms = now();
        mtp_say(false);
    } else if (m.stage == ms::st_leaving) {
        if (m.responder.step()) return;                                  // the cancel: close, remove a partial upload
        U32 i = 0;
        while (i != ms::max_open && m.files.open[i] < 0) ++i;
        const mr::FileOps ops = ms::file_ops(m.files);
        if (i != ms::max_open) ops.close(ops.context, m.files.open[i]);  // one request per pass
        else { m.stage = ms::st_closing; settle_begin(); }               // op 9 when a change opened the window
    } else if (m.stage == ms::st_closing && !closing) {
        m.stage = ms::st_ended;
        mtp_say(true);
    }
    const U32 pass = now() - start;
    if (pass > m.longest_ms) m.longest_ms = pass;
}
#if defined(XYF_MTP_USB)
// The composite's MTP interface (mtp_usb::Handler) over the session's responder.
bool mtp_live() { return mtp_on && mslot().stage == ms::st_running; }
bool h_deliver(const uint8_t* data, uint32_t n) {
    ms::Session& m = mslot();
    const bool command = mtp_live() && !m.responder.busy() && n >= 12 && data[4] == 1 && !data[5];
    if (!mtp_live() || !m.responder.receive(data, n)) return false;
    if (command) m.command_ms = now();                                   // speed: the operation's start
    m.bytes_in += n; m.progress_ms = now();
    return true;
}
uint32_t h_pending() { return mtp_live() ? mslot().responder.pending() : 0u; }
uint32_t h_length() { return mtp_live() ? mslot().responder.staged_length() : 0u; }
uint32_t h_take(uint8_t* out, uint32_t n) {
    ms::Session& m = mslot();
    const U32 got = mtp_live() ? m.responder.take(out, n) : 0u;
    m.bytes_out += got;
    if (got) m.progress_ms = now();
    return got;
}
bool h_idle() { return !mtp_live() || !mslot().responder.busy(); }
void h_cancel(bool reset) {
    ms::Session& m = mslot();
    if (!mtp_live() || (!m.responder.busy() && !(reset && m.responder.session_open()))) return;
    if (reset) m.responder.reset(); else m.responder.cancel();
    ++m.cancels;
}
uint16_t h_status() { return mtp_live() ? uint16_t(mslot().responder.device_status()) : uint16_t(mr::r_ok); }
const xyf::mtp_usb::Handler mtp_handler = {h_deliver, h_pending, h_length, h_take, h_idle, h_cancel, h_status};
#endif
// diag.mtp: u8 op, then: 0 enter, 1 leave, 2 one bulk-OUT transfer (s_busy: not taken now, send it again),
// 3 u32 room: bulk-IN bytes (reply u32 status, u32 zlp, bytes), 4 cancel, 5 state (u32 status, then stage,
// bytes in, bytes out, longest pass ms, device status, link, steps, file requests, busy, stalls, refused busy,
// operations, longest operation ms, log count; speed, 2026-11-15: GetObjectInfo answered from the listing, read
// and write requests, their bytes, operations under 2 ms, 2-4, 5-20, over 20 ms, the longest menu-loop pass ms
// (the composite's; 0 without it)), 6 the operations log (u32 status, u32 count, then up to
// log_entries entries of 5 words, oldest first: mtp_session.hpp).
void mtp_tunnel(uint8_t seq, const uint8_t* payload, U32 length) {
    ms::Session& m = mslot();
    const uint8_t op = length ? payload[0] : 0xff;
    const bool live = mtp_on && m.stage == ms::st_running;
    U32 code = P::s_ok, n = 4;
    if (op == 0 && length == 1) code = session_enter();
    else if (op == 1 && length == 1) code = session_leave();
    else if (op == 2 && length > 1 && length - 1 <= mr::buffer_bytes) {
        if (!live) code = P::s_state;
        else if (m.responder.receive(payload + 1, length - 1)) m.bytes_in += length - 1;
        else code = P::s_busy;
    } else if (op == 3 && length == 5) {
        U32 room = P::get32(payload + 1);
        if (room > read_range_max - 8) room = read_range_max - 8;
        U32 got = 0, zlp = 0;
        if (!live) code = P::s_state;
        else {
            got = m.responder.take(files + 8, ms::phase_room(m.in, room));   // never past a container's end
            zlp = got && ms::phase_zlp(m.in, files + 8, got) ? 1u : 0u;
            m.bytes_out += got;
        }
        P::put32(files + 4, zlp);
        n = 8 + got;
    } else if (op == 4 && length == 1) {
        if (!live) code = P::s_state;
        else { m.responder.cancel(); ++m.cancels; m.in.length = m.in.sent = 0; }
    } else if (op == 5 && length == 1) {
        const U32 values[22] = {mtp_on ? m.stage : 0u, m.bytes_in, m.bytes_out, m.longest_ms,
                                live ? U32(m.responder.device_status()) : 0u,
                                m.files.link, m.steps, m.files.requests, mtp_busy() ? 1u : 0u,
                                m.stalls, m.refused_busy, m.operations, m.longest_op_ms, m.log_count,
                                m.responder.cache_hits(), m.files.data_requests, m.files.data_bytes,
                                m.hist[0], m.hist[1], m.hist[2], m.hist[3], mtp_pass_ms};
        for (U32 i = 0; i != 22; ++i) P::put32(files + 4 + 4 * i, values[i]);
        n = 92;
    } else if (op == 6 && length == 1) {
        const U32 count = mtp_on ? m.log_count : 0u, first = count > ms::log_entries ? count - ms::log_entries : 0u;
        P::put32(files + 4, count);
        n = 8;
        for (U32 i = first; i != count; ++i)
            for (U32 k = 0; k != 5; ++k, n += 4) P::put32(files + n, m.log[i % ms::log_entries][k]);
    } else code = op > 6 ? P::s_unsupported : P::s_bad_request;   // no op: as an unlisted type (not in protocol/v1.json yet)
    P::put32(files, code);
    result.last_status = code;
    send(t_mtp_tunnel, seq, files, n);
}
#endif
// MODS_APPLY: base NUL profile NUL generation NUL machines NUL extensions NUL name NUL u32 flags +
// SHA-256 of every byte before it. Reply: u32 status, u32 job id, u32 missing input. mods_begin is the
// start itself (the menu's Mods page starts the job through it too).
U32 mods_begin(const uint8_t* payload, U32 length, U32& id, U32& missing, U32& taken) {
    JobSlot& s = slot();
    const char* field[6];
    const U32 most[6] = {31, 31, 31, 1023, 255, 47};   // 64 machines
    U32 at = 0, code = P::s_ok, machines = 0, extensions = 0;
    id = 0; missing = 0; taken = 0;
    for (U32 k = 0; k != 6 && code == P::s_ok; ++k) {
        const U32 begin = at;
        while (at < length && payload[at]) ++at;
        if (at >= length || at - begin > most[k]) code = P::s_bad_request;
        field[k] = reinterpret_cast<const char*>(payload + begin);
        ++at;
    }
    U32 flags = 0;
    if (code == P::s_ok && at + 4 + 32 != length) code = P::s_bad_request;
    if (code == P::s_ok) {
        flags = P::get32(payload + at);
        U32 t = 0;                                                       // the lists and name into the slot
        for (U32 k = 3; k != 6; ++k) { const char* f = field[k]; do s.text[t++] = *f; while (*f++); }
        char* list_m = s.text;
        char* list_e = list_m + text_bytes(list_m) + 1;
        const char* name = list_e + text_bytes(list_e) + 1;
        const U32 name_bytes = text_bytes(name);
        bool name_ok = name_bytes && name[0] != ' ' && name[name_bytes - 1] != ' ';
        for (U32 i = 0; i != name_bytes; ++i) if (name[i] < 0x20 || name[i] > 0x7e) name_ok = false;
        if (flags > 3 || !xyf::store::valid_id(field[0]) || !job_token(field[1], true) || !job_token(field[2], true) ||
            !name_ok || !job_ids_split(list_m, s.machines, max_job_machines, machines) || !machines ||
            !job_ids_split(list_e, s.extensions, max_job_extensions, extensions))
            code = P::s_bad_request;
        else {
            s.input.name = name;
            xyf_core::hash::Sha256 h;
            xyf_core::hash::Digest d;
            if (!h.update(payload, at + 4) || !h.finish(d) || !sha_equal(d.bytes, payload + at + 4)) code = P::s_sha;
        }
    }
    if (code == P::s_ok && put_expected && result.put_received != put_expected) code = P::s_busy;   // an upload
    if (code == P::s_ok && !xy_forever_cold_policy) code = P::s_state;
    if (code == P::s_ok && (!join(s.base, job_firmware, field[0]) || !join(s.stock, s.base, "/os.ldr") ||
                            !join(s.official, s.base, "/content.sha256") ||
                            !join(s.generation, job_mods, field[1], "/") ||
                            !join(s.generation + text_bytes(s.generation), field[2], "")))
        code = P::s_bad_request;
    if (code == P::s_ok && !a_serving()) code = P::s_link;
    if (code == P::s_ok) code = file_client::mount_ready(mounted);
    if (code == P::s_ok) { code = job_target(s); taken = code == P::s_state; }   // the library id it takes
    // Every input there (one stat each, read-only), before anything is written.
    char probe[file_engine::max_path];
    const char* catalog = job_catalog;
#if defined(XYF_CONTENT_SWITCH)
    if (code == P::s_ok) catalog = content_job_catalog();               // the saved copy while mods are in
#endif
    const char* inputs[4] = {s.stock, s.official, catalog, probe};
    join(probe, s.generation, "/base/info.txt");
    for (U32 k = 0; k != 4 && code == P::s_ok; ++k) {
        uint8_t record[file_client::record_bytes];
        const file_client::Reply r = file_client::stat(inputs[k], record);
        if (r.outcome) { if (r.outcome == file_client::link) mounted = file_client::mounted_stale; code = file_client::status(r.outcome); }
        else if (r.value != 0) { code = P::s_not_found; missing = k + 1; }
    }
    // A job id whose output folder does not exist yet.
    for (U32 tries = 0; code == P::s_ok; ++tries) {
        char number[12];
        if (tries == job_ids) { code = P::s_state; break; }
        id = ++job_counter;
        decimal_text(number, id);
        join(s.out, job_tmp, "/job-", number);
        join(s.old, s.out, "-old");                                      // the old entry's place
        uint8_t record[file_client::record_bytes];
        file_client::Reply r = file_client::stat(s.out, record);
        if (!r.outcome && r.value != 0) r = file_client::stat(s.old, record);
        if (r.outcome) { if (r.outcome == file_client::link) mounted = file_client::mounted_stale; code = file_client::status(r.outcome); }
        else if (r.value != 0) break;
    }
    if (code == P::s_ok && (code = enter_writes()) == P::s_ok) {
        storage_used = 1;
        writes_due = 1; writes_ms = now();                               // closed when the job ends
        uint32_t error = 0;
        code = file_client::change(0x26u, job_tmp, error, mounted);       // mkdir tmp (it may be there)
        if (code != P::s_ok && code != P::s_link && error == file_client::eexist) code = P::s_ok;
        if (code == P::s_ok) code = file_client::change(0x26u, s.out, error, mounted);
    }
    if (code == P::s_ok) {
        s.input.selection.machines = s.machines; s.input.selection.machine_count = machines;
        s.input.selection.extensions = s.extensions; s.input.selection.extension_count = extensions;
        s.input.selection.layout = 0; s.input.selection.layout_count = 0; s.input.selection.has_layout = false;
        s.input.selection.expanded = (flags & 2u) != 0;                      // bit 1, the 4 MiB layout
        s.input.generation = s.generation; s.input.stock = s.stock; s.input.catalog = catalog;
        s.input.official = s.official; s.input.out = s.out; s.input.policy = xy_forever_cold_policy;
        s.input.profiling = false; s.input.unverified = (flags & 1u) != 0;
        s.calls.context = &s; s.calls.size = job_size; s.calls.open = job_open; s.calls.read = job_read;
        s.calls.write = job_write; s.calls.close = job_close; s.calls.rename = job_rename; s.calls.remove = job_remove;
        s.calls.mkdir = job_mkdir;
        s.id = id; s.started_ms = s.ended_ms = now(); s.longest_ms = 0; s.link = 0; s.debris = 0;
        s.said_ms = s.started_ms; s.said_phase = ~0u;
        s.stage = js_sweep; s.had_old = s.commit_error = s.commit_code = s.extra_steps = s.clean_calls = 0;
        sweep_begin(s.leftovers, s.out);                                     // leftovers, never this output
        job_kind = 0; job_usb = 0;                                       // mods_apply sets it
        job_state = job_busy;
        if (save_active) save_lap(xl::p_job_base);                       // a Save's build, else a USB one
        else {
            xl::begin(save_times, xl::k_build, now(), xl::p_job_base);
            save_times.mods = machines + extensions; save_times.expanded = (flags & 2u) ? 1u : 0u;
        }
        xyf_core::Byte* region = reinterpret_cast<xyf_core::Byte*>(XYF_JOB_REGION_BEGIN + job_slot_bytes);
        if (mj::start(s.job, s.input, s.calls, region, job_work_end - XYF_JOB_REGION_BEGIN - job_slot_bytes) !=
            mj::Error::none)
            job_finish(s);                                               // refused: failed at once (job.status)
        else job_say(s, true);
    }
    if (code != P::s_ok) id = 0;
    return code;
}
void mods_apply(uint8_t seq, const uint8_t* payload, U32 length) {
    U32 id = 0, missing = 0, taken = 0;
    const U32 code = mods_begin(payload, length, id, missing, taken);
    if (code == P::s_ok && job_running()) job_usb = 1;                  // the menu names it
    uint8_t p[12]; P::put32(p, code); P::put32(p + 4, id); P::put32(p + 8, missing);
    result.last_status = code;
    send(P::t_mods_apply, seq, p, 12);
}
}
namespace xy_forever { namespace usb_service {      // the Mods page's hooks (usb_service.hpp)
uint32_t page_serving() { return a_serving() ? uint32_t(P::s_ok) : uint32_t(P::s_link); }
volatile uint32_t& page_mount() { return mounted; }
bool page_busy() { return job_running() || closing XYF_OR_MTP_BUSY; }    // as job_blocks (an MTP transfer)
void save_log_begin() { xl::begin(save_times, xl::k_save, now(), xl::p_list); save_active = 1; }
void save_log_selection(uint32_t mods, bool expanded, uint32_t added, uint32_t removed) {
    if (!save_active) return;
    save_times.mods = mods; save_times.expanded = expanded ? 1u : 0u; save_times.added = added; save_times.removed = removed;
}
void save_log_lap(uint32_t part) { if (save_active) save_lap(part); }
void save_log_drop() { if (save_active) { save_times.kind = xl::k_none; save_active = 0; } }
uint32_t mods_start(const uint8_t* payload, uint32_t length, uint32_t& job, uint32_t& missing) {
    U32 id = 0, why = 0, taken = 0;
    job = 0; missing = 0;
    if (job_blocks(P::t_mods_apply)) return P::s_busy;                  // as mods.apply (a job, the close)
    const U32 code = mods_begin(payload, length, id, why, taken);
    job = id; missing = taken ? mods_ids_taken : why;                 // the page says why
    return code;
}
void job_view(JobView& v) {                                             // as job_status (the commit too)
#if defined(XYF_CONTENT_SWITCH)
    if (job_kind == 2) {                                                 // the config job
        const ConfigSlot& g = gslot();
        v.state = job_state; v.id = g.id; v.phase = g.step != cg_ended ? 0u : g.error ? U32(xc::phase_failed) : U32(xc::phase_done);
        v.done = g.step; v.total = cg_ended; v.error = g.step == cg_ended ? g.error : 0u; v.status = g.code; v.debris = 0; v.commit = 0;
        return;
    }
    if (job_kind) {                                                      // a content switch (its own id)
        const ContentSlot& c = cslot();
        v.state = job_state; v.id = c.id; v.phase = content_phase(c); v.done = c.job.done; v.total = c.job.total;
        v.error = c.stage == cs_ended ? c.job.error : 0u; v.status = c.job.code; v.debris = 0; v.commit = 0;
        return;
    }
#endif
    JobSlot& s = slot();
    mj::Progress g;
    mj::progress(s.job, g);
    const bool committing = job_running() && s.stage >= js_away && s.stage != js_wipe;
    v.state = job_state; v.id = s.id;
    v.phase = s.commit_error ? U32(mj::Phase::failed) : committing ? U32(mj::Phase::finish) : U32(g.phase);
    v.done = committing ? s.stage - js_away + 1 : g.done; v.total = committing ? 3u : g.total;
    v.error = s.commit_error ? s.commit_error : s.link && g.phase == mj::Phase::failed ? job_link_error : U32(g.error);
    v.status = s.commit_error ? s.commit_code : g.status;
    v.debris = s.debris; v.commit = committing ? 1u : 0u;
}
uint32_t job_stop() {                                                   // as job.cancel (not in the commit)
    if (!job_running() || job_kind) return P::s_not_found;              // a content switch is not a mod job
    if (slot().stage >= js_away) return P::s_state;
    mj::cancel(slot().job);
    job_finish(slot());
    return P::s_ok;
}
#if defined(XYF_CONTENT_SWITCH)
const char* content_default(uint32_t entry) {                            // the menu's SHIFT+PLAY
    const xyf::catalog::Catalog* catalog = xyf_boot::catalog();
    if (!catalog || entry >= catalog->count || !(catalog->items[entry].flags & xyf::catalog::flag_content_missing))
        return 0;
    if (!menu_usb) return "Content switch: needs USB on in the menu.";
    const U32 code = content_begin(catalog->items[entry].id, true);
    return code == P::s_ok ? "Installing content..." : code == P::s_busy ? "Busy: a job is running." :
           code == P::s_link ? "Storage link lost." : "Content switch: could not start.";
}
uint32_t content_switch(const char* id) { return content_begin(id, true, false); }
uint32_t config_store(const char* text, uint32_t length, const char* default_id) { return config_begin(text, length, default_id); }
// settings.txt (xyf_settings.hpp; usb_service.hpp).
namespace xs = xyf::settings;
xs::Record settings_record __attribute__((section(".data"))) = {1};
U32 settings_loaded __attribute__((section(".data"))) = 0;
bool settings_load() {
    if (settings_loaded) return true;
    if (!menu_link::usb_idle()) return false;                           // 191: not while A's catalog is on its way
    if (!a_serving() || job_running() || closing) return false;         // later: once A serves, the slot is free
    char text[xs::max_text];
    uint32_t bytes = 0;
    U32 code = file_client::mount_ready(mounted);
    if (code == P::s_ok) code = file_client::read_file(xs::path, 0, false, reinterpret_cast<uint8_t*>(text), sizeof(text), bytes, mounted);
    if (code == P::s_not_found) code = file_client::read_file(xs::old_path, 0, false, reinterpret_cast<uint8_t*>(text), sizeof(text), bytes, mounted);
    if (code == P::s_ok) xs::parse(text, bytes, settings_record);
    else xs::defaults(settings_record);                                 // absent or unreadable: the defaults
    settings_loaded = 1;                                                // one try: bounded
    return true;
}
xs::Record& settings() { return settings_record; }
xs::Gate confirm_gate __attribute__((section(".data"))) = {xs::idle, 0, ""};
bool confirm_key(uint32_t key, uint32_t pressed, bool injected) {
    if (confirm_gate.state != xs::pending) return false;
    xs::gate_key(confirm_gate, key, pressed, injected);
    return true;
}
uint32_t settings_store() {
    char text[xs::max_text];
    const U32 n = xs::format(text, sizeof(text), settings_record);
    return config_begin(text, n, "", xs::path, xs::old_path, xs::part_path);
}
const char* job_target() { return slot().target; }
const char* content_line() {
    ContentSlot& c = cslot();
    if (job_kind != 1 || c.line_seq == content_line_shown) return 0;
    content_line_shown = c.line_seq;
    return c.line;
}
#endif
} }
namespace {
// JOB_STATUS: u32 job (0: the running or last). Reply: u32 status, then the job_status fields.
void job_status(uint8_t seq, const uint8_t* payload, U32 length) {
    JobSlot& s = slot();
    const U32 want = length == 4 ? P::get32(payload) : 0;
#if defined(XYF_CONTENT_SWITCH)
    if (length == 4 && job_kind == 2) return config_status(seq, want);
    if (length == 4 && job_kind) return content_status(seq, want);
#endif
    uint8_t* p = files;
    U32 code = length != 4 ? P::s_bad_request : job_state == job_none || (want && want != s.id) ? P::s_not_found : P::s_ok;
    P::put32(p, code);
    if (code != P::s_ok) { result.last_status = code; send(P::t_job_status, seq, p, 4); return; }
    mj::Progress g;
    mj::progress(s.job, g);
    const bool failed_link = s.link && g.phase == mj::Phase::failed;
    // The commit is phase finish (done = its step, of 3) until it ends; a failed commit is failed.
    const bool committing = job_running() && s.stage >= js_away && s.stage != js_wipe;
    const U32 phase = s.commit_error ? U32(mj::Phase::failed) : committing ? U32(mj::Phase::finish) : U32(g.phase);
    const U32 values[11] = {s.id, phase, committing ? s.stage - js_away + 1 : g.done, committing ? 3u : g.total,
                            s.commit_error ? s.commit_error : failed_link ? job_link_error : U32(g.error),
                            s.commit_error ? s.commit_code : g.status,
                            (job_running() ? now() : s.ended_ms) - s.started_ms, s.job.steps + s.extra_steps,
                            s.longest_ms, s.job.peak, s.debris};
    for (U32 i = 0; i != 11; ++i) P::put32(p + 4 + 4 * i, values[i]);
    for (U32 i = 0; i != 32; ++i) p[48 + i] = i + 1 < mj::detail_bytes ? uint8_t(s.job.detail[i]) : 0;
    bool end = false;
    for (U32 i = 0; i != 32; ++i) { if (!p[48 + i]) end = true; if (end) p[48 + i] = 0; }
    result.last_status = code;
    send(P::t_job_status, seq, p, 80);
}
// JOB_CANCEL: u32 job (0: the running one). mod_job closes its handle and removes its files,
// job_finish its folders and the window. Reply: u32 status, u32 debris.
void job_cancel(uint8_t seq, const uint8_t* payload, U32 length) {
    JobSlot& s = slot();
    const U32 want = length == 4 ? P::get32(payload) : 0;
#if defined(XYF_CONTENT_SWITCH)
    if (length == 4 && job_kind) {                                       // a switch is repaired, not cancelled
        const U32 code = want && want != cslot().id ? P::s_not_found : P::s_state;
        uint8_t p[8]; P::put32(p, code); P::put32(p + 4, 0);
        result.last_status = code;
        send(P::t_job_cancel, seq, p, 8);
        return;
    }
#endif
    U32 code = length != 4 ? P::s_bad_request : job_state == job_none || (want && want != s.id) ? P::s_not_found :
               !job_running() || s.stage >= js_away ? P::s_state : P::s_ok;   // not in the commit
    if (code == P::s_ok) { mj::cancel(s.job); job_finish(s); }
    uint8_t p[8]; P::put32(p, code); P::put32(p + 4, code == P::s_bad_request || code == P::s_not_found ? 0 : s.debris);
    result.last_status = code;
    send(P::t_job_cancel, seq, p, 8);
}
#endif

#if defined(XYF_TFW_CHECK)
// TFW_CHECK: path NUL. Is the file an official package that this device's own keys
// open (native/tfw_check)? Reads the file through a's read-only server into a window of the file
// buffer, and the device's keys from b's OTP (otp_keys). Reply: u32 status, then (ok) u32 verdict
// (0 pass, 1 fail), u32 reason (tfw_check::Reason), the SHA-256 of the 56-byte public key read
// (zero when none was read); nothing derived from the AES key. Nothing is written or extracted.
struct CheckFile { const char* path; U32 base, bytes; };
const U32 check_window = 0x4000u;
int check_read(void* context, U32 offset, uint8_t* out, U32 count) {
    CheckFile& f = *static_cast<CheckFile*>(context);
    U32 done = 0;
    while (done != count) {
        const U32 at = offset + done;
        if (at < f.base || at - f.base >= f.bytes) {
            tick();
            uint32_t got = 0;
            f.base = at & ~0x1ffu; f.bytes = 0;
            if (file_client::read_file(f.path, f.base, true, files, check_window, got, mounted) != P::s_ok)
                return -1;
            f.bytes = got;
            if (at - f.base >= f.bytes) break;            // the end of the file
        }
        const U32 n = count - done < f.bytes - (at - f.base) ? count - done : f.bytes - (at - f.base);
        for (U32 i = 0; i != n; ++i) out[done + i] = files[at - f.base + i];
        done += n;
    }
    return int(done);
}
__attribute__((noinline)) U32 tfw_check_run(const char* path, uint8_t* reply) {
    CheckFile f = {path, 0, 0};
    uint32_t got = 0;
    const U32 code = file_client::read_file(path, 0, true, files, check_window, got, mounted);
    if (code != P::s_ok) return code;                     // bad_request, refused, not_found, storage
    f.bytes = got;
    say("Checking a firmware package...", "Reads the device keys.", "If this stays, power-cycle.");
    xyf_core::tfw_check::Workspace work;
    xyf_core::tfw_check::Result r;
    const xyf_core::tfw_check::File file = {&f, check_read};
    const xyf_core::tfw_check::Keys keys = {0, otp_keys::public_key, {0, otp_keys::aes_key}};
    const xyf_core::tfw_check::Reason reason = xyf_core::tfw_check::check(file, keys, work, r);
    P::put32(reply, reason == xyf_core::tfw_check::Reason::pass ? 0u : 1u);
    P::put32(reply + 4, U32(reason));
    for (U32 i = 0; i != 32; ++i) reply[8 + i] = r.public_key_read ? r.public_key_sha256.bytes[i] : 0;
    say(reason == xyf_core::tfw_check::Reason::pass ? "Package check: pass." : "Package check: fail.");
    return P::s_ok;
}
void tfw_check(uint8_t seq, const uint8_t* payload, U32 length) {
    uint8_t p[4 + 8 + 32];
    for (U32 i = 0; i != sizeof(p); ++i) p[i] = 0;
    U32 path_bytes = 0;
    while (path_bytes != length && payload[path_bytes]) ++path_bytes;
    U32 code = P::s_ok;
    if (length < 10 || path_bytes + 1 != length || length > storage_server::max_path) code = P::s_bad_request;
    else if (!a_serving()) code = P::s_link;
    else code = tfw_check_run(reinterpret_cast<const char*>(payload), p + 4);
    P::put32(p, code);
    result.last_status = code;
    send(P::t_tfw_check, seq, p, code == P::s_ok ? sizeof(p) : 4);
}
#endif

// INSTALL / UNINSTALL: payload = 32-byte confirm token = SHA-256 of the
// staged image. UNINSTALL differs only in the image acceptance (the exact
// stock 1.2.1 os.ldr instead of an XY Forever build) and the reply type.
void install(uint8_t seq, const uint8_t* payload, U32 length, bool uninstall) {
    U32 code = P::s_ok;
    log_open = 1;                                   // step lines and the writer's log go to the host
    if (length != 32 || !result.put_ok) code = P::s_state;
    else if (!sha_equal(payload, put_sha)) code = P::s_sha;
    else code = validate_image(put_expected, uninstall);
    if (code == P::s_ok) beacon(step_checked, put_expected);
    if (code == P::s_ok) settle_writes();                 // A's window closed before install mode
    if (code == P::s_ok && !menu_link::usb_command(storage_server::install_item)) code = P::s_link;
    const bool a_install = code == P::s_ok;         // a answered install_item: its exemption is on
    if (code == P::s_ok) {
        beacon(step_a_install, menu_link::result.answers);
        read_enabled = 1;                           // install mode also serves reads
        result.capacity = boot_capacity();
        beacon(step_capacity, result.capacity);
        if (put_expected + 512 > result.capacity) code = P::s_too_large;
    }
    if (code == P::s_ok) {
        const U32 start = now();
        lines(image, put_expected, false);          // the stock writer moves data by SPI DMA
        say("Installing XY Forever...", "Do not unplug or power off.");
        beacon(step_writer, put_expected);
        hook_logs();
        guard_count_set(guard_writer_count);        // no reloads inside the stock writer
        result.install_written = write_boot(staged, put_expected);
        guard_count_set(guard_count);
        unhook_logs();                              // before any session step (verify ticks)
        result.install_ms = now() - start;
        // The writer may have encrypted the staging in place: never write it again.
        result.put_ok = 0;
        beacon(step_writer_done, result.install_written);
        code = result.install_written ? verify(put_expected, result.put_crc) : P::s_install;
        // A failed writer stops before its own select 0 (verify ends with one): back to the
        // user partition here, or a's engine refuses all storage until a restarts.
        if (!result.install_written) for (U32 tries = 0; tries != 8 && select_partition(0) != 0; ++tries) {}
        say(code == P::s_ok ? "Install finished and verified." : "Install FAILED.",
            code == P::s_ok ? "Restart to use it." : "Use the dev menu to recover if needed.");
        put_expected = 0;
        beacon(step_verify_done, code | result.verify_partition << 8 | result.verify_mismatch << 16);
    }
    log_open = 0;
    // The writer's turn is over: one more command ends a's install exemption (F1 again; a boot
    // partition still selected keeps it). A lost link here is found by the next command anyway.
    if (a_install) menu_link::usb_command(storage_server::command_item);
    result.install_status = code;
    uint8_t p[16];
    P::put32(p, code); P::put32(p + 4, result.install_written);
    P::put32(p + 8, result.verify_partition); P::put32(p + 12, result.verify_sectors);
    result.last_status = code;
    send(uninstall ? P::t_uninstall : P::t_install, seq, p, 16);
}
}

namespace xy_forever { namespace usb_service {
uint32_t presence() {
    // 1: the stock bootloader started USB (MSC). It does so only on its "wait
    // for battery" path (88a1dbae: 88a1c8fc -> 88a35044(0x40)), i.e. powered on
    // from the USB-powered off state (Tests 017 run 2 / 021 vs 017 run 1 / 023).
    // 2: not started, but the PSoC power status says USB power (stock 88a1c8fc:
    // [0x88a1cdb4] == 0x16): fast power cycle or software reset (Test 038:
    // REBOOT and a quick off/on both skipped the service).
    if (byte(0x88a35934u) == 1 && (word(0x88a3593cu) & 0x40u)) return 1;
    // 3: our own REBOOT left the marker (usb_service.hpp). The PSoC power status
    // is not used: it read 0x10 with and without USB.
    if (word(reboot_marker) == reboot_magic && word(reboot_marker + 4) == ~reboot_magic) return 3;
    return 0;
}

#if defined(XYF_USB_SET_DEFAULT)
void a_set_default(cold_storage::Stock121ReadOnlySession& storage, uint32_t hash) {
    // No library scan on the USB path (module budget): find the one firmware
    // folder whose name hashes to hash and has an os.ldr, read the two state
    // slots (and legacy state.txt), plan the next slot, write it.
    using namespace xyf;
    U32 status = state_write::status_not_mounted, seq = 0, written = 0;
    services::U32 step = 0;
    if (storage_server::ensure_mounted(storage) && storage.direct_ready() && !storage_server::held_files()) {
        char id[store::max_id + 1];
        for (U32 i = 0; i != sizeof(id); ++i) id[i] = 0;
        U32 matches = 0;
        const U32 dir = storage.direct_opendir("/yaffs2/xy-forever/firmware");
        if (dir) {
            for (U32 guard = 0; guard != 256; ++guard) {
                const uint8_t* entry = storage.direct_readdir(dir);
                if (!entry) break;
                char name[store::max_id + 2];
                U32 n = 0;
                for (; n != sizeof(name) - 1 && entry[library::dirent_name + n]; ++n)
                    name[n] = char(entry[library::dirent_name + n]);
                name[n] = 0;
                if (entry[library::dirent_type] == library::dirent_regular || !store::valid_id(name) ||
                    state_write::id_hash(name) != hash) continue;
                if (!matches++) for (U32 k = 0; k <= n; ++k) id[k] = name[k];
            }
            storage.direct_closedir(dir);
        }
        char path[96];
        bool present = false;
        U32 at = 0;
        const char* const parts[3] = {"/yaffs2/xy-forever/firmware/", id, "/os.ldr"};
        for (U32 k = 0; k != 3; ++k)
            for (const char* c = parts[k]; *c && at + 1 < sizeof(path); ++c) path[at++] = *c;
        path[at] = 0;
        if (matches == 1) {
            const int fd = storage.direct_open(path);
            present = fd >= 0 && storage.direct_close(fd) == 0;
        }
        if (!present) status = state_write::status_hash;
        else {
            state::Choice choice;
            for (U32 i = 0; i != sizeof(choice); ++i) reinterpret_cast<char*>(&choice)[i] = 0;
            static const char* const files[3] = {"/yaffs2/xy-forever/state-a.txt", "/yaffs2/xy-forever/state-b.txt",
                                                 "/yaffs2/xy-forever/state.txt"};
            static const state::Source sources[3] = {state::source_a, state::source_b, state::source_legacy};
            uint8_t* text = reinterpret_cast<uint8_t*>(state_write::a_scan);
            for (U32 k = 0; k != 3; ++k) {
                services::U32 length = 0;
                if (library::detail::read_text(storage, files[k], text, store::max_text, length))
                    state::consider(choice, sources[k], reinterpret_cast<const char*>(text), length);
            }
            state_write::Plan& plan = *reinterpret_cast<state_write::Plan*>(state_write::a_plan);
            if (!state_write::plan_default_id(choice, id, plan)) status = state_write::status_plan;
            else {
                seq = plan.seq;
                // Test 048: never a watchdog reset mid-write. Disarm/arm (both proven)
                // rather than a count change while enabled (cf. the Test 045 reset).
                const bool guarded = guard_on != 0;
                guard_disarm();
                const cold_storage::Result w = storage.write_state_slot(plan.path,
                    reinterpret_cast<const uint8_t*>(plan.text), plan.length,
                    reinterpret_cast<uint8_t*>(state_write::a_readback), step);
                if (guarded) guard_arm();
                written = U32(w);
                status = w == cold_storage::Result::ok ? state_write::status_ok : state_write::status_storage;
            }
        }
    }
    result.a_state_write = status | (written & 0xffu) << 8 | (step & 0xffu) << 16 | (seq & 0xffu) << 24;
}
#endif

bool dry_run_requested() { return dry_run != 0; }
void a_dry_run() { dry_run = 1; }
void dry_run_finish(const void* block, uint32_t bytes) {
    if (result.role == 1) {
        // The session already ended; a link not shown closed halts b (never the OS).
        if (!b_link_closed(false)) { say("Internal link lost.", "Turn the OP-XY off and on."); for (;;) {} }
        menu_release();                                // USB off (already off after the boot's release)
        const U32 n = bytes < dry_run_max_bytes ? bytes : dry_run_max_bytes;
        const uint8_t* from = static_cast<const uint8_t*>(block);
        for (U32 i = 0; i != n; ++i) byte(dry_run_area + 8 + i) = from[i];
        word(dry_run_area + 4) = n; word(dry_run_area) = dry_run_magic;
        lines(dry_run_area, 8 + n, false);
        marker_write(marker_reboot | carried_flags, 0);
    }
    reset_self();
}

// Test 058: the XY Forever menu keeps USB (key_ui opens the menu without PLAY).
bool menu_requested() { return menu_usb == 1u || menu_usb == 2u; }   // 3: USB kept, no menu asked
#if defined(XYF_SERVICE_ON_MARKER_ONLY)
// Test 060: PLAY at power-on: the menu starts USB itself so the host can attach.
volatile U32 usb_off __attribute__((section(".data"))) = 0;   // 097: why the menu has no USB (0 = none)
#if defined(XYF_MTP_USB)
volatile U32 usb_deferred __attribute__((section(".data"))) = 0;   // menu_usb 2, USB not started yet
#endif
void menu_start_usb() {
    if (menu_usb == 3u) { menu_usb = 1; return; }      // Test 062: USB kept from the service
    if (menu_usb) return;
    usb_off = identity() ? 1u : file_client::identity() ? 2u : 0u;
    if (usb_off) return;
    P::reset(parser, rx);
    result.stock_callback = cdc_callback();
#if defined(XYF_MTP_USB)
    usb_deferred = 1;                                  // home_pump's first pass reads the flag, then starts USB
#else
    set_cdc_callback(0);
    usb_start();
#endif
    menu_usb = 2;                                      // 2: started by the menu (1: kept from the service)
}
const char* usb_notice() {
    return usb_off == 1u ? "USB off: stock USB code changed (1)." :
           usb_off == 2u ? "USB off: stock storage code changed (2)." : 0;
}
#endif
// Test 062: file-boot progress on the service screen (only after a HELLO this boot).
void progress(const char* a, const char* b) { say(a, b); }
// ---- Test 065: one link session; the menu is home and answers every USB command ----
void rpc_reset() { read_enabled = 0; }               // a left RPC mode (a transfer item was sent)
bool guard_armed() { return guard_on != 0; }
void stack_watch(uint32_t painted_begin) { stack_paint_begin = painted_begin; }
void guard_reload() { guard_kick(); }
bool home_active() { return home != 0; }
bool home_usb() { return menu_usb != 0; }
// The menu's USB mark and activity line (usb_service.hpp).
volatile U32 host_framed __attribute__((section(".data"))) = 0;          // a valid frame arrived this boot
volatile U32 host_frame_ms __attribute__((section(".data"))) = 0;        // now() at the last one
volatile U32 host_change __attribute__((section(".data"))) = 0;          // the last changing request's line
volatile U32 host_change_ms __attribute__((section(".data"))) = 0;
enum HostLine : U32 { line_none = 0, line_stage, line_files, line_install, line_uninstall, line_default, line_build,
                      line_content };
const char* host_line(U32 line) {
    return line == line_stage ? "USB: receiving a file" : line == line_files ? "USB: writing files" :
           line == line_install ? "USB: installing XY Forever" : line == line_uninstall ? "USB: uninstalling XY Forever" :
           line == line_default ? "USB: setting the default" : line == line_build ? "USB: building Open-XY" :
           line == line_content ? "USB: changing content" : nullptr;
}
U32 change_line(uint8_t type) {                                          // the requests that change something
    if (type == P::t_put_begin || type == P::t_put_data || type == P::t_put_end) return line_stage;
    if (type == P::t_file_write || type == P::t_delete_file || type == P::t_remove_dir || type == P::t_make_dir ||
        type == P::t_rename_file || type == P::t_copy_file) return line_files;
    if (type == P::t_install) return line_install;
    if (type == P::t_uninstall) return line_uninstall;
    if (type == P::t_set_default) return line_default;
    if (type == P::t_mods_apply) return line_build;
    return line_none;
}
#if defined(XYF_MTP_USB)
// ---- Serial + MTP as one composite device (record 031 b2) ------------------------------------------
// USB is started once per boot and never stopped or restarted inside it (serial and MTP both always on).
// 188: the product (no flag, no trial, no fallback restart: a device used without a computer never sees a
// HELLO): every boot whose boot-time USB check passed (menu_start_usb) starts the composite in the menu loop's
// first pass when the MTP span gate passes, else the stock serial service (no switch inside a boot). The
// trial below (the flag, the marks, the result file, the 20 s fallback, diag op 0) is the test mode's only
// (xyf-mtp-usb, XYF_MTP_USB_TRIAL).
// Trial with a cheap net: diag.mtp_usb (0x62, dev, test only, not in the published spec) op 0 writes the flag
// file and does the product's full restart (home_reboot(reboot_menu): both processors, record 022). Every boot
// of this build defers its USB start to the first pass of the menu loop (usb_begin: B reaches files only
// through A, which serves by then): one bounded check of A (command item), a stat of the flag; found: deleted
// FIRST (A's write entry, unlink), then ours registered and start(0x200). No flag, A silent, the delete
// refused, the span gate refused or the slot taken: start(0x400), the stock serial service, as every boot of
// the product. On the composite, no valid HELLO within composite_hello_ms of the start: the same full restart
// (fallback_restart), and the next boot (no flag) is on stock serial. A HELLO within it keeps the composite.
// The MTP interface (GetDeviceInfo, OpenSession, CloseSession, the test sink/source/loopback) is always
// available while the composite is up: every pass of the menu loop pumps it (composite_turn).
// Op 1: the composite's words, the driver's, the last sink's SHA-256, the gate's refusal record.
// Not bounded by us: the controller init inside the stock start (88a0bf86) waits 5 ticks of timer 7 (record
// 030); timer 7 is open at a boot's first start. The progress marks name a step that hangs.
namespace mu = xyf::mtp_usb;
const uint8_t t_mtp_usb = 0x62;
const U32 composite_hello_ms = 20000, mtp_seen_ms = 5000;
const U32 usb_driver_slot = 0x11803af0u;                                // record 002: the one function-driver slot
const char flag_path[] = "/yaffs2/xy-forever/tmp/composite.flag";
enum CompositeState : U32 { cs_none, cs_deferred, cs_stock, cs_composite, cs_fallback };
enum FlagCheck : U32 { flag_unchecked, flag_absent, flag_found, flag_a_silent };
const U32 probe_words = 14;
// state, flag, delete (status of the unlink), gate (0 passed), slot (driver slot after begin), started_ms,
// first_byte_ms, hello_ms, passes, max_gap_ms, last_ms, sinks, mark_first_ms, mark_total_ms
U32 probe[probe_words] __attribute__((section(".data"))) = {0};
enum : U32 { p_state, p_flag, p_delete, p_gate, p_slot, p_started, p_first_byte, p_hello, p_passes, p_max_gap,
             p_last, p_sinks, p_mark_first, p_mark_total };
U32 last_commands __attribute__((section(".data"))) = 0;
U32 mtp_ms __attribute__((section(".data"))) = 0;                     // now() at the last MTP command
uint8_t probe_digest[32] __attribute__((section(".data"))) = {0};
U32 probe_hash_storage[(sizeof(xyf_core::hash::Sha256) + 3) / 4] __attribute__((section(".data"))) = {0};
xyf_core::hash::Sha256& probe_hash() { return *reinterpret_cast<xyf_core::hash::Sha256*>(probe_hash_storage); }
U32 hash_open __attribute__((section(".data"))) = 0;                   // a sink's hash not finished yet
void probe_sink(const uint8_t* data, uint32_t n, bool first) {          // main loop (pump): the sink's data, in order
    if (first) { probe_hash().reset(); hash_open = 1; ++probe[p_sinks]; return; }
    probe_hash().update(data, n);
}
namespace gate = xyf::mtp_usb_gate;
// Why the last check answered s_identity (mtp_usb_gate.hpp; op 1 reports it after the digest).
uint32_t probe_refusal[gate::record_words] __attribute__((section(".data"))) = {0};
uint32_t crc_at(uint32_t address, uint32_t length) {                      // stock code on B
    return xyf::crc::update_volatile(0, reinterpret_cast<const volatile uint8_t*>(address), length);
}
// Not identity() again (the live serial service changes five words of usb_spans.hpp, #65, #86).
// Rule (record 029): the MTP start skips the second identity() (mtp_usb_gate.hpp). The boot-time identity() must have
// passed (menu_start_usb: usb_off 0, menu_usb 2). Then engine_identity()'s spans (mtp_spans.hpp), then the
// driver's own (mtp_usb_spans.hpp, TX start), all on B. In usb_begin: before USB starts.
gate::Check probe_spans() {
    const gate::Spans engine = {xyf::mtp_spans::table, xyf::mtp_spans::count};
    const gate::Spans spans = {xyf::mtp_usb_spans::table, xyf::mtp_usb_spans::count};
    return gate::refusal<crc_at>(menu_usb == 2u && !usb_off, engine, spans, probe_refusal);
}
// ---- progress marks that survive a power cycle (record 030 section 5) ----------------------------------------
// mu::mark() calls probe_mark before each step that could freeze. Each mark appends one line to marks_text
// ("NN MS NAME": step, B's clock in ms, name) and rewrites the whole file through A's file engine: open "wb",
// one write (<= 1 KiB, one chunk), close: three requests, each bounded by the file client (change_ms). With A's
// write window closed (writes_due 0) it first enters A's write entry, mounts and creates tmp/ (A opens its
// window: 0.31 s for a whole put-file with checkpoint [019 M]); then writes_due: the menu loop's normal settle
// closes the window (window_settle_ms after the last write). Marks run from the flag's delete to the first
// valid HELLO (or the fallback restart). A failed mark is not retried: mu::mark tries no further mark this boot.
const char marks_path[] = "/yaffs2/xy-forever/tmp/mtp-probe-marks.txt";
const char marks_folder[] = "/yaffs2/xy-forever/tmp";
const U32 marks_capacity = 1024;                                         // file_client::chunk: one write
char marks_text[marks_capacity] __attribute__((section(".data"))) = {0};
U32 marks_length __attribute__((section(".data"))) = 0;
U32 marks_append(const char* text) {
    U32 n = 0;
    while (text[n]) ++n;
    if (marks_length + n > marks_capacity) return 0;
    for (U32 i = 0; i != n; ++i) marks_text[marks_length + i] = text[i];
    return marks_length += n;
}
U32 writes_open() {                                                      // A's write window, open from here on
    namespace C = file_client;
    if (writes_due) { writes_ms = now(); return P::s_ok; }               // still open: the settle waits again
    U32 code = enter_writes();                                           // A's write entry (stale mount closed)
    if (code == P::s_ok) code = C::mount_ready(mounted);                 // op 8
    if (code != P::s_ok) return code;
    writes_due = 1; writes_ms = now(); storage_used = 1;                 // the normal settle closes it
    const C::Reply d = C::mkdir(marks_folder);                          // op 0x26; EEXIST is fine
    if (d.outcome) { if (d.outcome == C::link) mounted = C::mounted_stale; return C::status(d.outcome); }
    return P::s_ok;
}
U32 file_put(const char* path, const char* text, U32 length) {         // the whole file: open "wb", write, close
    namespace C = file_client;
    const U32 entry = writes_open();
    if (entry != P::s_ok) return entry;
    const C::Reply o = C::open(path, storage_server::write_open_flags);   // op 0x29 "wb"
    if (o.outcome) { if (o.outcome == C::link) mounted = C::mounted_stale; return C::status(o.outcome); }
    if (o.value < 0) return P::s_storage;
    const C::Reply w = C::write(o.value, reinterpret_cast<const uint8_t*>(text), length);   // op 0x2b
    if (w.outcome) { if (w.outcome == C::link) mounted = C::mounted_stale; return C::status(w.outcome); }
    const C::Reply c = C::close(o.value);                               // op 0x2d
    if (c.outcome) { if (c.outcome == C::link) mounted = C::mounted_stale; return C::status(c.outcome); }
    writes_ms = now();
    return w.value == int(length) && !c.value ? P::s_ok : P::s_storage;
}
U32 marks_write() { return file_put(marks_path, marks_text, marks_length); }
bool probe_mark(uint32_t step) {
    const U32 t0 = now();
    char line[24];
    line[0] = step >= 10 ? '1' : '0'; line[1] = char('0' + (step >= 10 ? step - 10 : step)); line[2] = ' ';   // < 20
    U32 n = 3 + decimal_text(line + 3, t0);
    line[n++] = ' '; line[n] = 0;
    if (!marks_append(line) || !marks_append(mu::mark_name(step)) || !marks_append("\n")) return false;
    const bool ok = marks_write() == P::s_ok;
    const U32 cost = now() - t0;
    if (step == mu::mark_flag_found) probe[p_mark_first] = cost;
    probe[p_mark_total] += cost;
    return ok;
}
void marks_begin() {
    marks_length = 0;
    marks_append("xy-forever mtp-usb composite marks, build " XYF_BUILD_NUMBER ": step ms name\n");
    mu::set_mark_hook(probe_mark);
}
void marks_end() { mu::set_mark_hook(0); }                              // writes_due: A's window closes by the settle
// The composite's result as text (everything op 1 reports), one line per part, words as 8 hex digits:
// "probe" probe_words words, "driver" the driver words, "sha256" the last sink's digest, "refusal" gate::record_words.
const char result_path[] = "/yaffs2/xy-forever/tmp/mtp-probe-result.txt";
const U32 stack_attached = 0x88a137a0u;      // record 031: 88a13714 sets it to 1 only when attach 88a08d52 returned 0
const U32 n_driver_words = 32;
void driver_words(U32* w) {
    const mu::Stats& st = mu::stats();
    const U32 driver[] = {st.passes, st.longest_pass_urbs, st.longest_pass_bytes, st.urbs_received, st.urbs_dropped,
                          st.in_sent, st.kicks, st.kick_rx, st.deconfigures, st.resets, st.cancels, st.resets_bus,
                          U32(st.sink_bytes), U32(st.source_bytes), st.sink_sum, st.device_infos, st.sessions_opened,
                          st.sessions_closed, U32(mu::state()), st.marks_written, st.marks_failed, st.mtp_commands,
                          st.serial_rx, st.serial_tx, st.serial_held, st.serial_busy, st.line_codings, st.control_lines,
                          st.handler_in, st.handler_out, st.handler_held, st.handler_urbs_in};
    static_assert(sizeof(driver) / sizeof(driver[0]) == n_driver_words, "op 1 layout");
    for (U32 i = 0; i != n_driver_words; ++i) w[i] = driver[i];
}
U32 text_put(char* t, U32 n, const char* s) { while (*s) t[n++] = *s++; return n; }
U32 text_hex(char* t, U32 n, U32 v, U32 digits) {
    for (U32 i = 0; i != digits; ++i) t[n++] = "0123456789abcdef"[(v >> (4 * (digits - 1 - i))) & 15u];
    return n;
}
U32 text_words(char* t, U32 n, const char* label, const U32* w, U32 count) {
    n = text_put(t, n, label);
    for (U32 i = 0; i != count; ++i) { t[n++] = ' '; n = text_hex(t, n, w[i], 8); }
    t[n++] = '\n';
    return n;
}
void sink_digest() {                                                     // after the sink's response (the host asks)
    xyf_core::hash::Digest d;
    if (hash_open && probe_hash().finish(d)) for (U32 i = 0; i != 32; ++i) probe_digest[i] = d.bytes[i];
    hash_open = 0;
}
U32 result_text(char* t) {                   // <= 1024 (one write): about 700 bytes
    sink_digest();
    U32 n = text_put(t, 0, "xy-forever mtp-usb composite result, build " XYF_BUILD_NUMBER "\n");
    n = text_words(t, n, "probe", probe, probe_words);
    U32 driver[n_driver_words];
    driver_words(driver);
    n = text_words(t, n, "driver", driver, n_driver_words);
    n = text_put(t, n, "sha256 ");
    for (U32 i = 0; i != 32; ++i) n = text_hex(t, n, probe_digest[i], 2);
    t[n++] = '\n';
    U32 refusal[gate::record_words];                                     // uint32_t is long on the target
    for (U32 i = 0; i != gate::record_words; ++i) refusal[i] = probe_refusal[i];
    return text_words(t, n, "refusal", refusal, gate::record_words);
}
void home_reboot(U32 target);
// The first pass of the menu loop (menu_start_usb deferred the start): the flag, then the boot's only start.
void usb_begin() {
    probe[p_state] = cs_stock;
#if defined(XYF_MTP_USB_TRIAL)
    namespace C = file_client;
    bool trial = false;
    if (!a_serving()) probe[p_flag] = flag_a_silent;                     // one bounded request: stock serial
    else {
        uint8_t record[C::record_bytes];
        const C::Reply s = C::stat(flag_path, record);                   // op 0x22
        probe[p_flag] = !s.outcome && s.value == 0 ? flag_found : flag_absent;
    }
    if (probe[p_flag] == flag_found) {
        // Deleted FIRST (before any mark, before USB): a freeze anywhere after leaves no flag behind.
        U32 code = writes_open();
        if (code == P::s_ok) {
            const C::Reply u = C::unlink(flag_path);                    // op 0x25
            if (u.outcome == C::link) mounted = C::mounted_stale;
            code = u.outcome ? C::status(u.outcome) : u.value == 0 ? P::s_ok : P::s_storage;
        }
        probe[p_delete] = code;
        marks_begin();
        mu::mark(mu::mark_flag_found);                                   // written after the delete (see above)
        if (code == P::s_ok) { mu::mark(mu::mark_flag_deleted); trial = true; }
    }
#else
    const bool trial = true;                                             // 188: the product: every boot
#endif
    if (trial) {
        probe[p_gate] = probe_spans();                                   // stock code as checked, before any start
        if (!probe[p_gate]) {
            result.stock_callback = cdc_callback();                      // unchanged (menu_release restores it)
            probe[p_started] = probe[p_last] = now();
            mu::set_sink_hook(probe_sink);
            session_enter();                                             // the responder on interface 2
            mu::set_handler(&mtp_handler);
            if (mu::begin(mu::stock_port())) {                           // register, start(0x200): marks 3-5 / 14
                probe[p_slot] = word(usb_driver_slot);
                mu::mark(word(stack_attached) == 1u ? mu::mark_attach_ok : mu::mark_attach_failed);
                composite_on = 1; probe[p_state] = cs_composite;
                return;
            }
            mu::set_sink_hook(0);
            mu::set_handler(0); mtp_on = 0;
        }
        marks_end();
    }
    set_cdc_callback(0);                                                 // as every boot of the product
    usb_start();
}
// No valid HELLO within composite_hello_ms: leave (stop(0), unregister, frees), the last mark, the result file,
// A's window closed (op 9, as before a REBOOT), then the REBOOT from home (A resets on the reboot item, B stops
// USB and resets 100 ms later: both processors, record 022; never B alone). The next boot: no flag, stock serial.
#if defined(XYF_MTP_USB_TRIAL)
void fallback_restart() {
    probe[p_state] = cs_fallback;
    mu::mark(mu::mark_fallback_restart);
    mu::leave();
    composite_on = 0;
    probe[p_slot] = word(usb_driver_slot);                               // 0: our driver left the slot
    mu::set_sink_hook(0);
    marks_length = result_text(marks_text);
    file_put(result_path, marks_text, marks_length);                     // enters A's writes itself if closed
    marks_end();
    settle_writes(false);
    home_reboot(reboot_menu);                                            // returns only if the link was refused
    menu_usb = 0;                                    // then: no USB this boot (our driver left; no stock CDC to call)
}
#endif
// Every pass of home_pump with the composite up: the driver's pass, the MTP mark's clock, the HELLO bound.
void composite_turn() {
    if (!composite_on) return;
    const U32 t = now();
    const U32 gap = t - probe[p_last];                                   // one pass of the menu loop
    if (gap > probe[p_max_gap]) probe[p_max_gap] = gap;
    mtp_pass_ms = probe[p_max_gap];
    probe[p_last] = t; ++probe[p_passes];
    mu::pump();
    if (mu::stats().mtp_commands != last_commands) { last_commands = mu::stats().mtp_commands; mtp_ms = now(); }
#if defined(XYF_MTP_USB_TRIAL)
    if (!probe[p_hello] && now() - probe[p_started] >= composite_hello_ms) fallback_restart();
#endif
}
// Speed (2026-11-15): while MTP traffic is active (an operation in flight, IN bytes staged, or a command within
// mtp_recent_ms), the menu-loop pass keeps running driver passes and responder steps: a small operation is
// answered in this pass (command in, data and response out: 1-3 ms instead of two or three 6 ms passes) and a
// transfer streams (each file request's 1 KiB goes out while the next is read). The burst ends after
// mtp_quiet_ms without USB or responder progress, or after mtp_burst_ms (the menu still redraws between
// bursts; the guard is kicked every pass).
const U32 mtp_burst_ms = 40, mtp_quiet_ms = 3, mtp_recent_ms = 300;
void mtp_burst() {
    if (!composite_on || !mtp_live()) return;
    ms::Session& m = mslot();
    if (!m.responder.busy() && !m.responder.pending() && now() - mtp_ms > mtp_recent_ms) return;
    const U32 start = now();
    U32 moved = now();
    while (now() - start < mtp_burst_ms) {
        const U32 before = m.bytes_in + m.bytes_out + m.steps;
        mu::pump();
        if (mu::stats().mtp_commands != last_commands) { last_commands = mu::stats().mtp_commands; mtp_ms = now(); }
        mtp_pump();
        if (m.bytes_in + m.bytes_out + m.steps != before) moved = now();
        else if (now() - moved >= mtp_quiet_ms) break;
    }
}
void composite_byte() {                                                  // bytes arrived on the composite's serial
    if (!composite_on || probe[p_first_byte]) return;
    probe[p_first_byte] = (now() - probe[p_started]) | 1u;              // nonzero: the mark is written once
    mu::mark(mu::mark_first_serial_byte);
}
void composite_hello() {                                                 // after the reply to a valid HELLO
    if (!composite_on || probe[p_hello]) return;
    probe[p_hello] = (now() - probe[p_started]) | 1u;
#if defined(XYF_MTP_USB_TRIAL)
    mu::mark(mu::mark_first_hello);
    marks_length = result_text(marks_text);
    file_put(result_path, marks_text, marks_length);
    marks_end();                                                         // no more marks this boot
#endif
}
// diag.mtp_usb op 0: the trial. Refused while anything else runs, without the guard rule, on the composite
// already, or when the span gate refuses (s_identity, op 1 says which span). Else the flag file (s_ok after its
// close), the reply, A's window closed, the full restart.
U32 trial_start() {
#if !defined(XYF_MTP_USB_TRIAL)
    if (true) return P::s_unsupported;                                   // 188: the product has no trial
#endif
    if (job_running() || closing XYF_OR_MTP_BUSY || (put_expected && result.put_received != put_expected)) return P::s_busy;
    if (guard_on || !menu_usb || composite_on) return P::s_state;       // record 022: nothing restarts B alone
    if (probe_spans()) return P::s_identity;
    return file_put(flag_path, "T2a\n", 4);
}
void probe_result(uint8_t seq) {
    U32 driver[n_driver_words];
    driver_words(driver);
    sink_digest();
    P::put32(files, P::s_ok);
    U32 at = 4;
    for (U32 i = 0; i != probe_words; ++i, at += 4) P::put32(files + at, probe[i]);
    for (U32 i = 0; i != n_driver_words; ++i, at += 4) P::put32(files + at, driver[i]);
    for (U32 i = 0; i != 32; ++i) files[at++] = probe_digest[i];
    for (U32 i = 0; i != gate::record_words; ++i, at += 4) P::put32(files + at, probe_refusal[i]);
    send(t_mtp_usb, seq, files, at);
}
void mtp_usb_command(uint8_t seq, const uint8_t* payload, U32 length) {
    const uint8_t op = length ? payload[0] : 0xff;
    if (op == 0 && length == 1) {
        const U32 code = trial_start();
        status_only(t_mtp_usb, seq, code);
        if (code != P::s_ok) return;
        settle_writes(false);                                            // as REBOOT: A's window closed first
        const U32 t = now();
        while (now() - t < 50u) {}
        home_reboot(reboot_menu);
    }
    else if (op == 1 && length == 1) probe_result(seq);
    else status_only(t_mtp_usb, seq, op > 1 && op != 0xff ? P::s_unsupported : P::s_bad_request);
}
#endif
#if defined(XYF_MTP_USB)
// The MTP mark: a transfer runs or a command came within mtp_seen_ms (an open session alone does not light it).
bool mtp_seen() { return composite_on && (mtp_busy() || (last_commands && now() - mtp_ms < mtp_seen_ms)); }
#endif
bool host_seen() { return host_framed && now() - host_frame_ms < host_seen_ms; }
uint32_t host_marks() {
#if defined(XYF_MTP_USB)
    return (host_seen() ? 1u : 0u) | (mtp_seen() ? 2u : 0u);
#else
    return host_seen() ? 1u : 0u;
#endif
}
const char* host_activity() {
#if defined(XYF_MTP_USB)
    if (mtp_seen()) return mtp_busy() ? "MTP: transfer" : "MTP: active";   // redraws the MTP mark
#endif
#if defined(XYF_USB_FILE_WRITE)
    if (job_running() && job_usb) return host_line(job_kind == 1 ? line_content : line_build);
#endif
    return host_change && now() - host_change_ms < host_activity_ms ? host_line(host_change) : nullptr;
}
const char* build_label() { return "build " XYF_BUILD_NUMBER; }
bool home_hold() { return home != 0 || home_greeted != 0; }

// b, at the start of the file boot: a valid marker (our REBOOT, a dry-run return, or a guard
// reset while home) makes this boot go home; at most max_crash_restarts guard resets in a row.
bool home_begin() {
    Result& r = result;
    r.version = 4; r.role = 1;
    r.start_cycles = cycles_div1024();
    start_ms = xyf::clock::ms();
    r.power_status = word(power_status_address);
    r.boot_reason = half(0x88a35938u);
    r.present = presence() == 3 ? 3u : 0u;
    if (!r.present) return false;
    const U32 reason = word(reboot_marker + 8);
    r.crash_restarts = (reason & 0xffu) == marker_active ? word(reboot_marker + 12) + 1 : 0;
    home_guarded = (reason & marker_guard) ? 1 : 0;
    marker_clear();
    // No reset loop: at most max_crash_restarts in a row.
    if (r.crash_restarts >= max_crash_restarts) {
        r.present = 0; return false;
    }
    home = 1; held = 1;
    r.present |= 1u << 4;
    return true;
}

// b, right before the menu loop (home, PLAY or a failed default): USB on; a guarded home
// arms the guard on both processors; a watchdog reset comes back home.
void home_start() {
    menu_start_usb();
    if (home && home_guarded) guard_arm();
    if (home) marker_write(marker_active | marker_menu | flags(), result.crash_restarts);
}

// REBOOT from home: a resets on the reboot item, b 100 ms later; target OS = no marker,
// anything else = home again (held, guard carried).
void home_reboot(U32 target) {
    say("Restarting...");
    menu_link::usb_command(storage_server::reboot_item);
    const U32 t = now();
    while (now() - t < 100u) {}
    const U32 carry = flags();
    if (!b_link_closed(true)) return;                  // refused: home stays, the link lost
    guard_disarm();
    menu_release();
    if (target == reboot_os) marker_clear();
    else marker_write(marker_reboot | marker_menu | carry, 0);
    reset_self();
}


// Every pass of the menu loop: 0 = stay home, 1 = leave home and boot the default, 2 + i = leave
// home and boot catalog entry i once (library.boot).
uint32_t home_pump() {
    Result& r = result;
    if (!menu_usb) return 0;
    guard_kick();
#if defined(XYF_MTP_USB)
    if (usb_deferred) { usb_deferred = 0; usb_begin(); return 0; }   // the flag, then the boot's only start
#endif
    stage2_turn();
#if defined(XYF_USB_FILE_WRITE)
    job_pump();                                        // a slice of the mod job
    settle_turn();                                     // a window close still out
    if (writes_due && !job_running() XYF_AND_NOT_MTP_BUSY && now() - writes_ms > storage_server::window_settle_ms) settle_writes();
#endif
#if defined(XYF_MTP_USB)
    composite_turn();                                  // the driver's pass, the HELLO bound (fallback restart)
#endif
#if defined(XYF_USB_FILE_WRITE) && defined(XYF_MTP_SESSION)
    mtp_pump();                                        // responder steps for a slice (one file request each)
#endif
#if defined(XYF_MTP_USB)
    mtp_burst();                                       // speed: more passes of both while MTP traffic is active
#endif
    const U32 avail = cdc_available();
    if (!avail) {
        // A partial frame followed by idle_reset_ms of silence (nothing left in the receive
        // ring) is dropped, so it cannot swallow the start of the next request.
        if (parser.have && now() - rx_quiet_from > P::idle_reset_ms) {
            parser.dropped += parser.have; parser.have = 0; r.dropped = parser.dropped;
        }
        return 0;
    }
    uint8_t* const chunk = reinterpret_cast<uint8_t*>(rx_chunk);
    const U32 got = cdc_read(chunk, avail < rx_chunk_bytes ? avail : rx_chunk_bytes);
    ++r.rx_events; r.rx_bytes += got;
#if defined(XYF_MTP_USB)
    if (got) composite_byte();                         // mark 10 once
#endif
    for (U32 i = 0; i != got; ++i) {
        if (!P::feed(parser, chunk[i])) continue;
        const uint8_t type = P::frame_type(parser), seq = P::frame_seq(parser);
        const uint8_t* payload = P::frame_payload(parser);
        const U32 length = P::frame_length(parser);
        ++r.frames; r.last_type = type;
        host_framed = 1; host_frame_ms = now();                         // the USB mark
        if (const U32 line = change_line(type)) {
            host_change = line; host_change_ms = host_frame_ms;
#if defined(XYF_USB_FILE_WRITE) && defined(XYF_MTP_SESSION)
            if (mtp_on) mslot().responder.invalidate();    // speed: MTP's cached object facts (mtp_responder.hpp)
#endif
        }
#if defined(XYF_SERVICE_SCREEN)
        key_ui::host_view();                                           // drawn before a long request runs
#endif
        U32 leave = 0;
        if (type == P::t_hello) {
            if (!home_greeted) { home_greeted = 1; if (!r.hello_at_ms) r.hello_at_ms = now(); }
            if (!s2_tried) stage2_load();                  // once per boot, before the reply
            hello(seq);
#if defined(XYF_MTP_USB)
            composite_hello();                         // the composite stays this boot; mark 11, marks end
#endif
        }
        else if (!home_greeted) status_only(type, seq, P::s_state);
#if defined(XYF_MTP_USB)
        else if (type == t_mtp_usb) mtp_usb_command(seq, payload, length);   // the probe (test build only)
#endif
#if defined(XYF_USB_FILE_WRITE)
#if defined(XYF_MTP_SESSION)
        else if (type == t_mtp_tunnel) mtp_tunnel(seq, payload, length);   // the test transport
#endif
        else if (job_blocks(type)) status_only(type, seq, P::s_busy);   // one request slot
        else if (type == P::t_mods_apply) mods_apply(seq, payload, length);
        else if (type == P::t_job_status) job_status(seq, payload, length);
        else if (type == P::t_job_cancel) job_cancel(seq, payload, length);
#endif
        else if (type == P::t_status) status(seq);
        else if (type == P::t_read_file) read_file(seq, payload, length);
        else if (type == P::t_screen) screen(seq, payload, length);
        else if (type == P::t_peek) peek(seq, payload, length);
#if defined(XYF_MTP)
        else if (type == P::t_mtp_test) mtp_test(seq, payload, length);
#endif
        else if (type == P::t_put_begin) put_begin(seq, payload, length);
        else if (type == P::t_put_data) put_data(seq, payload, length);
        else if (type == P::t_put_end) put_end(seq);
        else if (type == P::t_install) install(seq, payload, length, false);
        else if (type == P::t_uninstall) install(seq, payload, length, true);
#if defined(XYF_USB_SET_DEFAULT)
        else if (type == P::t_set_default) set_default(seq, payload, length);
#endif
#if defined(XYF_USB_FILE_WRITE)
        else if (type == P::t_file_write) file_write(seq, payload, length);
        else if (type == P::t_list_dir) list_dir(seq, payload, length);
        else if (type == P::t_delete_file || type == P::t_remove_dir || type == P::t_make_dir)
            file_change(type, seq, payload, length);
        else if (type == P::t_rename_file) file_rename(seq, payload, length);
        else if (type == P::t_copy_file) file_copy(seq, payload, length);
#endif
#if defined(XYF_TFW_CHECK)
        else if (type == P::t_tfw_check) tfw_check(seq, payload, length);
#endif
        else if (type == P::t_key) {
            const bool ok = length == 8 && P::get32(payload) < 64u && P::get32(payload + 4) <= 1u;
            if (ok) key_ui::inject_key(P::get32(payload), P::get32(payload + 4));
            status_only(type, seq, ok ? P::s_ok : P::s_bad_request);
        }
        else if (type == P::t_library_boot) {         // boot a named entry once (as PLAY on it)
            const U32 chosen = library_boot(seq, payload, length);
            if (chosen) { dry_run = 0; carried_flags = flags(); held = 0; leave = 1 + chosen; }
        }
        else if (type == P::t_continue) {              // payload u32: 0 boot the default, 1 dry run
            const U32 mode = length == 4 ? P::get32(payload) : 0;
            bool ok = length == 0 || (length == 4 && mode <= 1);
            if (ok && !menu_link::usb_link_up()) ok = false;   // no boot over a lost link (menu stays)
            if (ok) settle_writes(false);                  // A's window closed before the boot
            if (ok && mode == 1) ok = menu_link::usb_command(storage_server::dry_run_item);
            if (ok) { dry_run = mode; carried_flags = flags(); held = 0; leave = 1; }
            status_only(type, seq, ok ? P::s_ok : P::s_link);
        }
        else if (type == P::t_hold || type == P::t_guard) {
            const U32 on = length == 4 ? P::get32(payload) : 2;
            if (type == P::t_guard && on == 2 && length == 4 && !guard_on) {
                // 105: guard expiry test without a hang: b only, ~3 s, no kicks; the menu loop
                // keeps running. The watchdog interrupt must restart b; if it does not, the
                // host can still read the watchdog and SEC registers.
                guard_arm();                                // b only (record 022): a follows once b is silent
                guard_count_set(guard_test_count); guard_muted = 1;
                marker_write(marker_active | marker_menu | flags(), r.crash_restarts);   // 109: back home
                status_only(type, seq, P::s_ok);
                P::next(parser);
                continue;
            }
            if (on <= 1) {
                if (type == P::t_hold) { held = on; home = on; }
                else if (on) guard_arm();                  // b only (record 022: a has no guard)
                else guard_disarm();
                // 109: a guarded PLAY menu (home == 0) also marks itself, so a guard reset comes home.
                if (home || guard_on) marker_write(marker_active | marker_menu | flags(), r.crash_restarts);
            }
            status_only(type, seq, on <= 1 ? P::s_ok : P::s_bad_request);
        }
        else if (type == P::t_reboot) {
            const U32 target = length == 4 ? P::get32(payload) : reboot_menu;
            if (target > reboot_menu) status_only(type, seq, P::s_bad_request);
            else { status_only(type, seq, P::s_ok); settle_writes(false); const U32 t = now(); while (now() - t < 50u) {} home_reboot(target); }
        }
        else if (type == P::t_test_hang) {
            const bool ok = length == 4 && P::get32(payload) == hang_token && guard_on;
            status_only(type, seq, ok ? P::s_ok : P::s_bad_request);
            if (ok) for (;;) {}                        // the guard must reset this processor
        }
        else if (stage2_command(type, seq, payload, length)) {}   // stage 2's types (stage2.info)
        else status_only(type, seq, P::s_unsupported);
        P::next(parser);
        if (leave) { r.exit = exit_continue; return leave; }
    }
    r.bad_crc = parser.bad_crc; r.dropped = parser.dropped;
    rx_quiet_from = now();                             // after any command: its run time is not silence
    return 0;
}
void menu_release() {
    guard_disarm();                                    // nothing after home runs guarded
    if (!menu_usb) return;
    menu_usb = 0;
#if defined(XYF_MTP_USB)
    if (usb_deferred) { usb_deferred = 0; return; }    // USB never started this boot: nothing to stop
    if (composite_on) {                                // our driver leaves (stop(0), unregister, frees)
        composite_on = 0;
        mu::leave();
        mu::set_mark_hook(0);
    }                                                  // then stop(1) as every boot (teardown skipped: flag 0)
#endif
    usb_stop();
    set_cdc_callback(result.stock_callback);
}
bool guard_pause() { const bool was = guard_on != 0; guard_disarm(); return was; }
void guard_resume(bool was) { if (was) guard_arm(); }

void reset_self() {
    // Test 046: the stock reset arms WDOG0 with a short count; a guard left
    // armed keeps its long count, so a stalled for seconds and missed b.
    guard_disarm();
    stock<0x11a03518u, void>(U32(*reinterpret_cast<volatile uint16_t*>(0x88a35938u)), 0u);   // stock "6. reset"
    for (;;) {}
}

// USB snapshots (diag, Tests 031-033) were removed in Test 041 for module space:
// the CDC receive cause is known; diag_count stays 0 (layout unchanged).
} }
#endif

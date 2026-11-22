// Part of usb_service.cpp, included there at this point: not compiled alone.
using namespace xy_forever;
using namespace xy_forever::usb_service;
namespace P = xyf::usbp;
typedef unsigned int U32;
template<U32 A, class R, class... Args> __attribute__((noinline, noclone)) R stock(Args... args) {
    return reinterpret_cast<R (*)(Args...)>(A)(args...);
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
    const int n = stock<0x88a13328u, int>(0u);
    return n > 0 ? U32(n) : 0;
}
U32 cdc_read(uint8_t* out, U32 n) {
    if (composite_on) return xyf::mtp_usb::serial_read(out, n);
    const int got = stock<0x88a1334au, int>(0u, out, n);
    return got > 0 ? U32(got) : 0;
}
int cdc_write(const uint8_t* data, U32 n) {
    if (composite_on) return xyf::mtp_usb::serial_write(data, n);
    return stock<0x88a132f6u, int>(0u, data, n);
}
U32 write_chunk() { return composite_on ? U32(xyf::mtp_usb::kSerialInMax) : 512u; }
#else
U32 cdc_available() {
    const int n = stock<0x88a13328u, int>(0u);
    return n > 0 ? U32(n) : 0;
}
U32 cdc_read(uint8_t* out, U32 n) {
    const int got = stock<0x88a1334au, int>(0u, out, n);
    return got > 0 ? U32(got) : 0;
}
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

volatile U32 held __attribute__((section(".data"))) = 0;
volatile U32 dry_run __attribute__((section(".data"))) = 0;      // this boot ends in a dry-run reset
volatile U32 carried_flags __attribute__((section(".data"))) = 0; // hold for the dry-run marker
U32 flags() { return held ? marker_held : 0; }
// Before resetting B, close its pending link request (at most close_ms), then the master link;
// stop USB afterwards. A lone B restart hangs in its stock bootloader until A restarts too.
// False: the link could not be shown closed, so B does not reset or retry this boot.
volatile U32 reset_refused __attribute__((section(".data"))) = 0;
#if defined(XYF_ONE_SESSION) && defined(XYF_SERVICE_ON_MARKER_ONLY)
bool b_link_closed(bool wait) {
    if (reset_refused || !xyf_boot::link_close(wait)) {
        reset_refused = 1;
        return false;
    }
    return true;
}
#else
bool b_link_closed(bool) { return true; }
#endif
void marker_write(U32 reason, U32 crashes) {
    word(reboot_marker + 8) = reason;
    word(reboot_marker + 12) = crashes;
    word(reboot_marker) = reboot_magic;
    word(reboot_marker + 4) = ~reboot_magic;
    lines(reboot_marker, 16, false);                    // to SDRAM before any reset
}
void marker_clear() {
    for (U32 i = 0; i != 4; ++i) word(reboot_marker + 4 * i) = 0;
    lines(reboot_marker, 16, false);
}
void tick() { menu_link::tick(); }

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
        const key_ui::Where where(xyf::where::usb_log);   // no wait(): inside the stock writer, 200 ms at most
        const U32 start = now();
        int wrote = -1;
        while ((wrote = cdc_write(frame + at, chunk)) < 0)
            if (now() - start > 200u) return false;
        at += U32(wrote) ? U32(wrote) : chunk;
    }
    return true;
}
void log_line(const char* text) {
    Result& r = result;
    ++r.log_lines;
    if (!log_open || in_interrupt()) {
        ++r.log_skipped;
        return;
    }
    uint8_t* const payload = reinterpret_cast<uint8_t*>(log_frame);
    uint8_t* const out = payload + 0x100;
    P::put32(payload, P::s_ok);
    P::put32(payload + 4, now());
    U32 n = 8;
    for (; text && *text && n != 8 + log_text_max; ++text) payload[n++] = uint8_t(*text);
    const U32 size = P::encode(uint8_t(P::t_log | P::t_reply), 0, payload, n, out, 0x100);
    if (size && raw_send(out, size)) ++r.log_sent;
    else {
        ++r.log_skipped;
        log_open = 0;
    }        // host not reading: stop, never slow the writer
}
// Stock sink signature (11a01d96 calls each non-zero sink as sink(level, text)).
// text starts with 4 binary time bytes (88a34230), then "file[line]: message"
// (Test 036: a zero time byte emptied the line).
void log_sink(int, const char* text) { log_line(text ? text + log_time_bytes : text); }
void hook_logs() {
    Result& r = result;
    r.log_level_before = word(log_level_address);
    U32 any = 0;
    for (U32 i = 0; i != 4; ++i) {
        saved_sinks[i] = word(log_sinks + 4 * i);
        any |= saved_sinks[i];
        word(log_sinks + 4 * i) = 0;
    }
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
    result.install_step = step;
    result.install_step_ms = now();
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
        const key_ui::Where where(xyf::where::usb_reply);
        const U32 start = now();
        int wrote = -1;
        while ((wrote = cdc_write(tx + at, chunk)) < 0) {
            tick();
            where.wait();
            if (now() - start > 500u) {
                ++result.write_errors;
                return false;
            }
        }
        at += U32(wrote) ? U32(wrote) : chunk;
    }
    ++result.replies;
    return true;
}
bool status_only(uint8_t type, uint8_t seq, U32 status) {
    uint8_t p[4];
    P::put32(p, status);
    result.last_status = status;
    return send(type, seq, p, 4);
}

// HELLO text (docs/PROTOCOL.md): generated from protocol/v1.json; the build defines
// XYF_VERSION and XYF_BUILD_MODE, the build switches set the cmds bitmap. hello_text writes the image
// (usb_service_identity.ipp). It ends in ";stage2=0": stage2_hello sets the loaded stage 2's version
// and its cmds bit.
static_assert(sizeof(XYF_USBP_HELLO_TEXT) - 1 <= P::max_hello, "hello text above max_hello");
constexpr bool ends_with(const char* text, U32 n, const char* end, U32 m) {
    return m <= n && (!m || (text[n - 1] == end[m - 1] && ends_with(text, n - 1, end, m - 1)));
}
#if !defined(XYF_SIM_HELLO_TEXT)                                       // a simulator variant's own text
static_assert(ends_with(XYF_USBP_HELLO_TEXT, sizeof(XYF_USBP_HELLO_TEXT) - 1, ";stage2=0", 9),
              "hello text must end in the stage2 key");
#endif
U32 stage2_hello(uint8_t* p, U32 n, U32 room);
U32 hello_text(uint8_t* p, U32 n, U32 room);
void hello(uint8_t seq) {
    uint8_t p[4 + P::max_hello];
    P::put32(p, P::s_ok);
    U32 n = hello_text(p, 4, sizeof(p));
    n = stage2_hello(p, n, sizeof(p));
    send(P::t_hello, seq, p, n);
}

// STATUS: u32 status, then blocks {u32 magic, u32 bytes, bytes...}.
U32 append_block(uint8_t* p, U32 n, const void* data, U32 bytes) {
    if (n + 8 + bytes > P::max_data) return n;
    const uint8_t* d = static_cast<const uint8_t*>(data);
    P::put32(p + n, P::get32(d));
    P::put32(p + n + 4, bytes);
    n += 8;
    for (U32 i = 0; i != bytes; ++i) p[n++] = d[i];
    return n;
}
// where key_ui painted B's stack (0: not painted, as in the simulator).
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

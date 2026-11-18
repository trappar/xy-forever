// Part of usb_service.cpp, included there at this point: not compiled alone.
// ---- Stage 2 (target/xyf_stage2.hpp ABI 3; docs/STORAGE.md) ------------------------
// At the first HELLO of a boot (a plain boot has no USB service and never gets here), B reads
// /yaffs2/xy-forever/stage2/main.xys2 through its file client (every request bounded): first its
// 96-byte header (a ranged read), then the whole file, no larger than the header says, into the
// staging buffer (nothing is staged before the first HELLO). accept() checks magic, ABI, header
// CRC, sizes, the load range inside the window, the entry inside the image, the image CRC and
// SHA-256 and min_stage1 <= this stage 1's release (XYF_VERSION_CODE). Only then is the image copied to load_address, its CRC
// checked again there, flushed (FLUSH, IFLUSH) and its init called through the one audited
// trampoline. Any failure leaves stage 1 exactly as before and stage2[] says why. Stage 2 is
// called only after init answered init_ok, and never again after an answer it does not define.
namespace S2 = xyf::stage2;
extern "C" uint32_t xyf_stage2_enter(uint32_t entry, const S2::Call* call) __asm__("xyf_stage2_enter");
const U32 stage1_version = XYF_VERSION_CODE;          // xy_forever/release.py code()
volatile U32 s2_tried __attribute__((section(".data"))) = 0;
volatile U32 s2_entry __attribute__((section(".data"))) = 0;          // 0: not loaded (or stopped)
S2::Header s2_header __attribute__((section(".data"))) = {};
uint32_t s2_now() { return now(); }
void s2_trace(uint32_t code, uint32_t value) { result.stage2[s2w_trace_code] = code; result.stage2[s2w_trace_value] = value; }
uint32_t s2_reply(uint32_t type, uint32_t seq, const uint8_t* payload, uint32_t length) {
    return send(uint8_t(type), uint8_t(seq), payload, length) ? 1u : 0u;
}
const S2::Services3 s2_services = {S2::services_version, sizeof(S2::Services3), stage1_version, s2_now, s2_trace, s2_reply};
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
    const S2::Check check = S2::accept(staged, bytes, stage1_version, h);
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


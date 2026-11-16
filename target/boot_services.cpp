#include "boot_services.hpp"
#include "../native/tfw.hpp"

namespace xy_forever { namespace services {
namespace {
bool length(const char* text, U32 capacity, U32& count) {
    if (!text) return false;
    for (count = 0; count != capacity; ++count) if (!text[count]) return true;
    return false;
}
}

bool loader_path(const char* path) {
    U32 size;
    if (!length(path, path_capacity, size)) return false;
    // The loader-owned root: XY Forever's own folder.
    static const char root[] = "/yaffs2/xy-forever/";
    const U32 prefix_size = sizeof(root) - 1;
    U32 matched = 0;
    while (matched != prefix_size && path[matched] == root[matched]) ++matched;
    if (matched != prefix_size || size <= prefix_size) return false;
    U32 component = prefix_size;
    for (U32 i = prefix_size; i <= size; ++i) {
        const unsigned char c = static_cast<unsigned char>(path[i]);
        if (c == '/' || !c) {
            const U32 n = i - component;
            if (!n || (n == 1 && path[component] == '.') ||
                (n == 2 && path[component] == '.' && path[component + 1] == '.')) return false;
            component = i + 1;
        } else if (c < 0x20 || c > 0x7e || c == '\\' || c == ':') return false;
    }
    return true;
}

bool read_update_key(const OtpIO& io, U32 index, Byte key32[32]) {
    if (!key32) return false;
    volatile Byte* key = key32;
    for (U32 i = 0; i != 32; ++i) key[i] = 0;
    if (index > 1 || !io.prepare || !io.read_selector || !io.prepare(io.user)) return false;
    // The retained BF707 compiler only promises4-byte stack alignment. Align
    // inside a larger automatic object instead of requesting unsupported8-byte
    // stack alignment from that compiler.
    Byte storage[520];
    Byte* scratch = reinterpret_cast<Byte*>((reinterpret_cast<uintptr_t>(storage) + 7u) &
                                            ~static_cast<uintptr_t>(7u));
    volatile Byte* clear = scratch;
    bool success = true;
    for (U32 part = 0; part != 2; ++part) {
        for (U32 i = 0; i != 512; ++i) clear[i] = 0;
        const U32 status = io.read_selector(io.user, 3 + index * 2 + part, scratch, 512);
        if (status != 1) { success = false; break; }
        for (U32 i = 0; i != 16; ++i) key[part * 16 + i] = scratch[i];
    }
    for (U32 i = 0; i != 512; ++i) clear[i] = 0;
    if (!success) for (U32 i = 0; i != 32; ++i) key[i] = 0;
    return success;
}
#if defined(__bfin__)
namespace {
template<class Function> Function* function(U32 address) { return reinterpret_cast<Function*>(address); }
U32 crc32(U32 address, U32 count) {
    const volatile Byte* bytes = reinterpret_cast<const volatile Byte*>(address);
    U32 crc = 0xffffffffu;
    for (U32 i = 0; i != count; ++i) {
        crc ^= bytes[i];
        for (U32 bit = 0; bit != 8; ++bit)
            crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}
bool stock_otp_prepare(void*) {
    // Exact stock RAM wrapper calls ROM OTP init0x04000054 with R0=0 once,
    // guarded by RAM byte0x11b01650. Neither endpoint programs OTP fields.
    function<void()>(0x11a05a32u)();
    return true;
}
U32 stock_otp_read(void*, U32 selector, Byte* scratch, U32 capacity) {
    if (selector < 3 || selector > 6 || capacity != 512) return 0;
    return function<U32(U32, void*)>(0x04000048u)(selector, scratch);
}
// Not linked by any build yet. Before it is: both CRC checks below read L1 instruction
// SRAM (0x11a05a1e) with data loads, which stalled the boot in Tests 002/005/006
// (docs/SAFETY.md). Check that wrapper another way first.
bool stock_key_provider(void*, xyf_core::U32 index, xyf_core::Byte key[32]) {
    if (crc32(0x11a05a1eu, 0x42) != 0x113c6900u) {
        if (key) { volatile Byte* clear = key; for (U32 i = 0; i != 32; ++i) clear[i] = 0; }
        return false;
    }
    OtpIO io;
    io.user = 0; io.prepare = stock_otp_prepare; io.read_selector = stock_otp_read;
    return read_update_key(io, index, key);
}
}
#endif
bool stock121_otp_provider(Phase phase, xyf_core::tfw::KeyProvider& output) {
    output.context = 0; output.read = 0;
    if (phase != Phase::live_bootloader && phase != Phase::cold_after_teardown) return false;
#if defined(__bfin__)
    if (crc32(0x11a05a1eu, 0x42) != 0x113c6900u) return false;
    output.read = stock_key_provider;
    return true;
#else
    return false;
#endif
}
} }

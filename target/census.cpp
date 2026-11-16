#include "census.hpp"
#include "cold_heap.hpp"
#include "key_ui.hpp"
#if defined(XYF_MENU_LINK)
#include "menu_link.hpp"
#endif
#if defined(XYF_REMOTE_READ)
#include "storage_server.hpp"
#endif
#if defined(XYF_FILE_BOOT)
#include "file_boot.hpp"
#endif
#if defined(XYF_USB_SERVICE)
#include "usb_service.hpp"
#endif
namespace xy_forever { namespace census {
namespace {
struct Region { uint32_t address, bytes; };
// Bootloader-initialized ranges only (exact 1.2.1 LDR loads/FILLs).
const Region fixed[] = {
    {0x1fc02104u, 12},          // IMASK, IPEND, ILAT (IMASK read safely since 008)
    {0x20040000u, 0x48},        // PORTA FER..LOCK (PORTA_DATA read since 004)
    {0x20040080u, 0x48},        // PORTB
    {0x20040100u, 0x48},        // PORTC
    {0x11800000u, 0x4000},      // L1 data A (loaded union)
    {0x11900000u, 0x8000},      // L1 data B
    {0x11b00000u, 0x1f00},      // L1 scratch (loaded union)
    {0x0803c000u, 0x24e0},      // loaded L2 only (rest may be ECC-uninitialized)
    {0x8803d000u, 0x43000},     // DDR data/BSS 0x8803d000..0x88080000 (driver globals)
    {0x88a0d000u, 0x29000},     // DDR data/BSS 0x88a0d000..0x88a36000
    {0x88a36000u, 0x2dbf8},     // DDR BSS ..0x88a63bf8 (end of loaded image)
};
uint32_t crc_update(uint32_t crc, const uint8_t* p, uint32_t n) {
    for (uint32_t i = 0; i != n; ++i) {
        crc ^= p[i];
        for (uint32_t b = 0; b != 8; ++b) crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return crc;
}
void put(uint8_t* p, uint32_t v) { p[0] = uint8_t(v); p[1] = uint8_t(v >> 8); p[2] = uint8_t(v >> 16); p[3] = uint8_t(v >> 24); }
uint32_t copy_words(uint8_t* out, uint32_t address, uint32_t bytes) {
    const volatile uint32_t* in = reinterpret_cast<const volatile uint32_t*>(address);
    for (uint32_t i = 0; i != bytes / 4; ++i) put(out + i * 4, in[i]);
    return bytes;
}
}
uint32_t fill(uint8_t* out, uint32_t capacity, uint32_t role, uint32_t gpio) {
    const uint32_t count = sizeof(fixed) / sizeof(fixed[0]);
    const uint32_t table = header_bytes, data = table + 12 * max_regions;
    if (capacity < data) return 0;
    uint32_t at = data, n = 0;
    for (uint32_t i = 0; i != count; ++i) {
        if (fixed[i].bytes > capacity - at) return 0;
        put(out + table + n * 12, fixed[i].address); put(out + table + n * 12 + 4, fixed[i].bytes);
        put(out + table + n * 12 + 8, at);
        at += copy_words(out + at, fixed[i].address, fixed[i].bytes); ++n;
    }
    {   // Test018: the key UI result block (module data), as its own region.
        const uint32_t address = reinterpret_cast<uint32_t>(&key_ui::result);
        const uint32_t bytes = sizeof(key_ui::Result);
        if (bytes > capacity - at) return 0;
        put(out + table + n * 12, address); put(out + table + n * 12 + 4, bytes);
        put(out + table + n * 12 + 8, at);
        at += copy_words(out + at, address, bytes); ++n;
    }
#if defined(XYF_MENU_LINK)
    {   // Test024: the held processor link result block (module data).
        const uint32_t address = reinterpret_cast<uint32_t>(&menu_link::result);
        const uint32_t bytes = sizeof(menu_link::Result);
        if (bytes > capacity - at) return 0;
        put(out + table + n * 12, address); put(out + table + n * 12 + 4, bytes);
        put(out + table + n * 12 + 8, at);
        at += copy_words(out + at, address, bytes); ++n;
    }
#endif
#if defined(XYF_REMOTE_READ)
    {   // Test027: the remote read result block (module data).
        const uint32_t address = reinterpret_cast<uint32_t>(&storage_server::result);
        const uint32_t bytes = sizeof(storage_server::Result);
        if (bytes > capacity - at) return 0;
        put(out + table + n * 12, address); put(out + table + n * 12 + 4, bytes);
        put(out + table + n * 12 + 8, at);
        at += copy_words(out + at, address, bytes); ++n;
    }
#endif
#if defined(XYF_USB_SERVICE)
    {   // XY Forever USB service result block (module data, both roles).
        const uint32_t address = reinterpret_cast<uint32_t>(&usb_service::result);
        const uint32_t bytes = sizeof(usb_service::Result);
        if (bytes > capacity - at) return 0;
        put(out + table + n * 12, address); put(out + table + n * 12 + 4, bytes);
        put(out + table + n * 12 + 8, at);
        at += copy_words(out + at, address, bytes); ++n;
    }
#endif
#if defined(XYF_FILE_BOOT)
    {   // Test025: the file boot result block (module data).
        const uint32_t address = reinterpret_cast<uint32_t>(&file_boot::result);
        const uint32_t bytes = sizeof(file_boot::Result);
        if (bytes > capacity - at) return 0;
        put(out + table + n * 12, address); put(out + table + n * 12 + 4, bytes);
        put(out + table + n * 12 + 8, at);
        at += copy_words(out + at, address, bytes); ++n;
    }
#endif
    // Live heap below its validated break, as much as fits.
    cold_heap::Snapshot heap; cold_heap::Result heap_result;
    uint32_t brk = 0;
    if (cold_heap::guard(heap, heap_result)) {
        brk = heap.brk;
        uint32_t bytes = (heap.brk - cold_heap::arena_context) & ~3u;
        if (bytes > capacity - at) bytes = (capacity - at) & ~3u;
        put(out + table + n * 12, cold_heap::arena_context); put(out + table + n * 12 + 4, bytes);
        put(out + table + n * 12 + 8, at);
        at += copy_words(out + at, cold_heap::arena_context, bytes); ++n;
    }
    uint32_t low, high;
    __asm__ volatile("%0 = CYCLES; %1 = CYCLES2;" : "=d"(low), "=d"(high));
    for (uint32_t i = 0; i != header_bytes; ++i) out[i] = 0;
    put(out, magic); put(out + 4, 1); put(out + 8, at); put(out + 12, n);
    put(out + 16, role); put(out + 20, gpio); put(out + 24, low); put(out + 28, high);
    put(out + 32, brk); put(out + 36, ~crc_update(0xffffffffu, out + header_bytes, at - header_bytes));
    put(out + 40, uint32_t(heap_result));
    return at;
}
} }

#include "cold_layout.hpp"
#include "cold.hpp"
#include "xyf_crc32.hpp"
#include "../native/ldr.hpp"
#include "warm_snapshot.hpp"
#if defined(XYF_FILE_BOOT)
#include "file_boot.hpp"
// Set by file boot: 1 when an admitted storage image (bytes/CRC below) sits
// in the boot buffer. cold.cpp re-admits it before use.
extern "C" volatile uint32_t xy_forever_storage_image_loaded;
extern "C" volatile uint32_t xy_forever_storage_image_bytes;
extern "C" volatile uint32_t xy_forever_storage_image_crc;
// 1 = storage image booted, 2 = embedded (not admitted), 3 = embedded after the
// storage image's report patch was refused, 4 = plain storage image (Test 051,
// no report patch). Written for diagnostics; nothing reads it today.
extern "C" { volatile uint32_t xy_forever_boot_source = 0xffffffffu; }
#if defined(XYF_PLAIN_IMAGES)
extern "C" volatile uint32_t xy_forever_storage_image_plain;   // file_boot admit (Test 051)
#endif
#if defined(XYF_NO_EMBEDDED)
// No library image to boot (file_boot.cpp): B shows why; both then halt.
extern "C" void xy_forever_no_image();
#endif
// The cold LDR policy, published for file boot's admission check.
extern "C" { const xyf_core::Policy* volatile xy_forever_cold_policy __attribute__((section(".data"))) = 0; }
#endif

namespace {
using xyf_core::U32;
using xyf_core::Byte;
using xyf_core::Range;

static_assert(sizeof(void*) == 4, "Blackfin-only cold adapter");

U32 crc32(const volatile Byte* bytes, U32 count) {
    return xyf::crc::update_volatile(0, bytes, count);
}

__attribute__((noreturn)) void stop() {
    // Stay on the reversible OS layer. A power-cycle with COM remains the
    // operator recovery entry. No SPI, NAND, USB or companion call occurs here.
    for (;;) __asm__ volatile("IDLE;");
}

const Range allowed[] = {
    {0x08000000u, 0x08040000u},
    {0x11800000u, 0x11c00000u},
    {0x80000000u, 0x90000000u}
};
const Range protected_ranges[] = {
    {0x0803e0c0u, 0x0803e142u}, // stock L1-copy routine, including tail
    {0x11803000u, 0x11804000u}, // incoming call stack
    {0x8803d000u, 0x8803d02au}, // stock FLUSH/FLUSHINV routine
    {0x88a346a6u, 0x88a3481cu}, // stock RAM source callback + ROM wrapper
    {0x88a34aecu, 0x88a34d6cu}, // stock ROM context/configuration/auxiliary
    {0x8a000000u, 0x8a800000u}, // ROM stream source
    {0x8ab00000u, XYF_STACK_END}, // module + stack ABOVE retained heap0 end
    {0x8b000000u, 0x8b800000u}  // embedded source before relocation
};
}

void xy_forever_cold_run() {
    using namespace xy_forever::cold;
    const Descriptor& descriptor = xy_forever_cold_descriptor;
#if defined(XYF_NO_EMBEDDED)
    // No embedded OS: the descriptor carries only the stock helper CRCs.
    if (descriptor.magic != descriptor_magic || descriptor.version != 1 || descriptor.payload_bytes != 0)
        stop();
#else
    if (descriptor.magic != descriptor_magic || descriptor.version != 1 ||
        descriptor.payload_bytes < 32 || descriptor.payload_bytes > source_capacity)
        stop();
#endif
    if (*reinterpret_cast<volatile U32*>(0x20000020u) != 0x11a00000u) stop();
    if (crc32(reinterpret_cast<const volatile Byte*>(0x88a346a6u), 0x176) !=
            descriptor.loader_crc32 ||
        crc32(reinterpret_cast<const volatile Byte*>(0x8803d000u), 0x2a) !=
            descriptor.cache_crc32 ||
        crc32(reinterpret_cast<const volatile Byte*>(0x0803e0c0u), 0x82) !=
            descriptor.l1_copy_crc32) stop();
    const xyf_core::Policy policy = {
        allowed, sizeof(allowed) / sizeof(allowed[0]),
        protected_ranges, sizeof(protected_ranges) / sizeof(protected_ranges[0])
    };
#if !defined(XYF_NO_EMBEDDED)
    const Byte* payload = reinterpret_cast<const Byte*>(source_base);
    if (crc32(payload, descriptor.payload_bytes) != descriptor.payload_crc32) stop();
    xyf_core::Summary summary;
    if (xyf_core::inspect(payload, descriptor.payload_bytes, policy, summary) !=
            xyf_core::Error::ok || summary.entry != 0x11a00000u) stop();
#endif
#if defined(XYF_FILE_BOOT)
    xy_forever_cold_policy = &policy;
#endif
#if defined(XYF_FILE_BOOT)
    // Test025 (XY Forever): both processors, every boot; a reads the firmware
    // file and serves it to b; each boots it only after a commit, else both
    // boot the embedded payload. Before the payload copy; always returns.
    xy_forever_file_boot();
#endif

    // The outer stream initially occupied 0x8a000000. It is now fully consumed.
    // Keep the embedded source at 0x8b000000 until this nonoverlapping copy.
#if !defined(XYF_NO_EMBEDDED)
    typedef void Copy(void*, const void*, U32);
#endif
    U32 boot_bytes = descriptor.payload_bytes;
#if defined(XYF_FILE_BOOT)
    // Test016: re-admit file boot's storage image by structure (same policy
    // and entry as the embedded payload) and its recorded CRC. Otherwise the
    // embedded copy below overwrites the buffer. Never stops on refusal.
    bool from_storage = false;
    if (xy_forever_storage_image_loaded == 1u) {
        const U32 n = xy_forever_storage_image_bytes;
        xyf_core::Summary loaded;
        from_storage = n >= 32 && n <= source_capacity &&
#if !defined(XYF_NO_EMBEDDED)
            // (Without an embedded OS, hand_off has just CRC-checked this exact copy.)
            crc32(reinterpret_cast<const volatile Byte*>(0x8a000000u), n) ==
                xy_forever_storage_image_crc &&
#endif
            xyf_core::inspect(reinterpret_cast<const Byte*>(0x8a000000u), n, policy, loaded) ==
                xyf_core::Error::ok && loaded.entry == 0x11a00000u;
        if (from_storage) boot_bytes = n;
    }
    xy_forever_boot_source = from_storage ? 1u : 2u;
#if defined(XYF_NO_EMBEDDED)
    // Nothing to fall back to: B says so on screen; both wait for a power cycle.
    if (!from_storage) { xy_forever_no_image(); stop(); }
#else
    if (!from_storage) {
#endif
#endif
#if !defined(XYF_NO_EMBEDDED)
    reinterpret_cast<Copy*>(0x040201c4u)(reinterpret_cast<void*>(0x8a000000u),
                                       payload, descriptor.payload_bytes);
    if (crc32(reinterpret_cast<const volatile Byte*>(0x8a000000u),
              descriptor.payload_bytes) != descriptor.payload_crc32) stop();
#if defined(XYF_FILE_BOOT)
    }
#endif
#endif
    bool patch = true;
#if defined(XYF_PLAIN_IMAGES)
    // Test 051: a plain official image (re-admitted above by the same policy,
    // entry and CRC) has no reporter: boot it unpatched (boot source 4).
    if (from_storage && xy_forever_storage_image_plain == 1u) { patch = false; xy_forever_boot_source = 4u; }
#endif
    if (patch && !xy_forever_patch_warm_snapshot(reinterpret_cast<Byte*>(0x8a000000u),
                                                    boot_bytes, policy)) {
#if defined(XYF_NO_EMBEDDED)
        xy_forever_no_image(); stop();                 // admission checked the slot: not expected
#elif defined(XYF_FILE_BOOT)
        if (!from_storage) stop();
        // Storage image lacked an exact report slot: boot the embedded copy.
        xy_forever_boot_source = 3u;
        reinterpret_cast<Copy*>(0x040201c4u)(reinterpret_cast<void*>(0x8a000000u),
                                           payload, descriptor.payload_bytes);
        if (crc32(reinterpret_cast<const volatile Byte*>(0x8a000000u),
                  descriptor.payload_bytes) != descriptor.payload_crc32 ||
            !xy_forever_patch_warm_snapshot(reinterpret_cast<Byte*>(0x8a000000u),
                                                descriptor.payload_bytes, policy)) stop();
#endif
    }
    // Reuse only the retained cold boot environment; this is never called from
    // a running stock OS. Its valid branch uses only ROM and protected routines.
    // Physical address independence and processor handoff remain qualification
    // gates; building this experimental adapter does not establish either.
    typedef U32 RamBoot(U32);
    reinterpret_cast<RamBoot*>(0x88a3477au)(0x8a000000u);
    stop();
}

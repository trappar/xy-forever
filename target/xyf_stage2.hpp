#ifndef XYF_STAGE2_HPP
#define XYF_STAGE2_HPP
#include <stdint.h>
// Stage-2 ("main XY Forever") file format (docs/storage/packages.md).
// A 96-byte header followed by a raw module image that stage 1 copies to
// load_address and calls at entry (with a versioned ABI struct). Stage 1
// accepts it only if every check below passes; otherwise it keeps its own
// recovery menu / default boot. Little-endian.
namespace xyf { namespace stage2 {
static const uint32_t magic = 0x32535958u;                 // "XYS2"
// ABI 1: the "hello" test image (--xyf-stage2). ABI 2: the main XY Forever
// menu (--xyf-stage2-menu), ABI 1 as a prefix plus the catalog and request
// exits below. A stage 1 offers exactly one ABI and accepts only images whose
// header names it (validate: Check::abi otherwise), so an ABI-2 stage 1
// refuses the ABI-1 hello image cleanly and shows its own menu.
static const uint32_t abi_hello = 1, abi_menu = 2, abi_service = 3;
#if defined(XYF_STAGE2_MENU)
static const uint32_t abi = abi_menu;
static const uint32_t this_stage1 = 2;                     // stage-1 version of this build
#elif defined(XYF_STAGE2_SERVICE)
// ABI 3: the product's stage 2, called from B's USB service. The stage-1 version a header's
// min_stage1 is checked against is stage 1's release, MAJOR << 16 | MINOR << 8 | PATCH
// (XYF_VERSION_CODE, usb_service passes it): 0.1.0 = 0x100, above every earlier build number.
static const uint32_t abi = abi_service;
static const uint32_t this_stage1 = 0;                     // unused: the release is passed
#else
static const uint32_t abi = abi_hello;
static const uint32_t this_stage1 = 1;                     // stage-1 version of this build
#endif
// Load window (FINDINGS-stage2.md): in the 1 MiB-aligned region
// that holds the executing cold module (product: 0x8ab00000..0x8ab84000, module
// + stack), above the stack top with a 48 KiB gap. Never loaded by the outer LDR
// or the stock bootloader, outside the retained heap arena (..0x8aa65bf4), and
// clear of the RAM-boot source (0x8a000000..0x8a800000), the embedded OS source
// (0x8b000000..0x8b800000, the fallback in test builds), the session scratch
// and catalog buffers (0x8b800000..0x8b823000) and the transfer staging
// (0x8b820000..0x8c000000), so stage 1 can boot every fallback after stage 2
// returns.
static const uint32_t window_begin = 0x8ab90000u, window_end = 0x8ac00000u;
static const uint32_t header_bytes = 96, max_image = window_end - window_begin;
// Where stage 1 looks for it (XY Forever's own folder; read-only).
static const char path[] = "/yaffs2/xy-forever/stage2/main.xys2";
struct Header {
    uint32_t magic, abi, header_bytes, flags;               // flags: reserved, must be 0
    uint32_t load_address, entry, image_bytes, image_crc32;
    uint8_t image_sha256[32];
    uint32_t min_stage1, version;                           // stage-1 version needed; stage-2 version
    char build_id[16];                                      // NUL-terminated
    uint32_t reserved;                                      // must be 0
    uint32_t header_crc32;                                  // over the first 92 bytes
};
static_assert(sizeof(Header) == header_bytes, "Header layout");
enum class Check : uint32_t {
    ok,
    size,
    magic,
    abi,
    header_crc,
    flags,
    window,
    entry,
    image_crc,
    stage1,
    build_id,
    image_sha
};
// Validates a whole file (header + image) for this stage-1 version. SHA-256
// is checked separately by the caller (agreement with processor b).
Check validate(const uint8_t* file, uint32_t length, uint32_t stage1_version, Header& out);
// validate() plus SHA-256 of the image against the header (both processors).
Check accept(const uint8_t* file, uint32_t length, uint32_t stage1_version, Header& out);

// Processor b's decisions (host-tested; file_boot/key_ui only act on them).
// A STAGE2 blob from a is either the accepted file or a's 8-byte refusal
// note ("XYSN", a's status | check << 8). detail: the note word, or the Check.
static const uint32_t refusal_magic = 0x4e535958u;          // "XYSN"
enum class Blob : uint32_t { ready, refused, invalid };
Blob classify(const uint8_t* data, uint32_t size, uint32_t stage1_version, Header& out, uint32_t& detail);
// After stage 2 returned: open the stage-1 menu, boot the library default, or
// (link lost) let both processors boot the built-in copy.
enum class Next : uint32_t { menu, boot_default, boot_builtin };
Next after(uint32_t exit, bool link_lost);

// ---- ABI 1: how stage 1 calls stage 2 (processor b only) ---------------------
// Stage 1 copies the image to load_address, flushes it from the data cache and
// invalidates the instruction cache, then calls
//     uint32_t entry(const Abi* abi)
// on its own 16 KiB cold stack with the held processor link still open.
// Stage 2 must return (one of the Exit codes); it never boots, writes storage
// or calls stock code itself: everything goes through Services, which run in
// stage 1 (the only code audited against the stock bootloader). Unknown exit
// codes are treated as exit_continue.
enum Exit : uint32_t { exit_continue = 0, exit_menu = 1 };
// next_event(): key | pressed << 8 | event_key, or 0 after ~5 ms without an
// event, or event_link_lost once the processor link is gone (stage 2 must
// then return at once; stage 1 boots the built-in copy). The held boot PLAY
// press is filtered as in the stage-1 menu.
static const uint32_t event_key = 0x10000u, event_link_lost = 0xffffffffu;
struct Services {
    uint32_t count;                         // number of entries below (4 in ABI 1)
    uint16_t* (*begin_frame)();             // waits for the display; 0 if busy (skip this frame)
    void (*end_frame)();                    // cache flush + start the display transfer
    uint32_t (*next_event)();               // keeps the processor link alive; see above
    uint32_t (*now_ms)();                   // ms since stage 1 started
};
struct Abi {
    uint32_t abi, bytes, role, stage1_version;   // abi 1, sizeof(Abi), 1 (= b), this_stage1
    uint32_t key_bitmap[3];                 // boot key snapshot (bit i = key i, PLAY = 51)
    const Services* services;
};
static_assert(sizeof(void*) != 4 || (sizeof(Services) == 20 && sizeof(Abi) == 32), "ABI 1 layout (32-bit target)");

// ---- ABI 2: the main XY Forever menu (--xyf-stage2-menu) -------------------
// Abi2 starts with the ABI-1 struct (abi = 2, bytes = sizeof(Abi2)); the
// Services table is unchanged. Stage 1 hands over the catalog blob exactly as
// processor a sent it (XYC2, CRC-checked; XYC1 still readable) and a
// note to show (result of the last request, may be 0). Stage 2 returns a
// request: code | argument << 8. Stage 1 performs every request with its own
// audited code (decide() below); a request it cannot honour comes back as a
// note and stage 1 calls stage 2 again (the image and its .data stay loaded,
// so the menu keeps its place). Stage 2 never calls stock code, never writes
// and never boots anything itself.
enum Request : uint32_t {
    request_continue = 0,       // = exit_continue: boot the library default
    request_menu = 1,           // = exit_menu: the stage-1 menu (fallback)
    request_boot_once = 2,      // argument: catalog entry
    request_set_default = 3,    // argument: catalog entry (needs a write build)
    request_mtp = 4,            // MTP mode (MTP builds only)
    request_uninstall = 5       // not available yet
};
enum Feature : uint32_t {       // what this stage 1 can do (Abi2::features)
    feature_catalog = 1u,
    feature_set_default = 2u,
    feature_mtp = 4u,
    feature_uninstall = 8u
};
struct Abi2 {
    Abi v1;                                 // v1.abi = 2, v1.bytes = sizeof(Abi2)
    const uint8_t* catalog;                 // versioned XYC catalog from processor a, or 0
    uint32_t catalog_bytes;
    uint32_t features;                      // Feature bits
    const char* note;                       // one status line for the menu, or 0
};
static_assert(sizeof(void*) != 4 || sizeof(Abi2) == 48, "ABI 2 layout (32-bit target)");
static const uint32_t max_rounds = 32;      // stage-1 calls per boot (each one is a user request)
// Stage 1's reading of a request (host-tested). entry: the checked argument.
enum class Do : uint32_t {
    boot_default,
    boot_entry,
    menu,
    boot_builtin,
    set_default,
    mtp,
    note_unavailable,
    note_no_entry
};
Do decide(uint32_t exit, bool link_lost, uint32_t catalog_count, uint32_t features, uint32_t& entry);

// ---- ABI 3: stage 2 in the product's USB service (XYF_STAGE2_SERVICE) ----------
// B's USB service loads the file at the first HELLO of a boot (a plain boot never reads it),
// accept()s it against the stage-1 release, copies the image to load_address (FLUSH,
// IFLUSH) and then calls
//     uint32_t entry(const Call* call)
// through the one audited trampoline (xyf_stage2_enter) with op_init once, op_turn on every
// home_pump pass and op_command for a request type stage 1 does not answer. Stage 2 makes no
// stock call and touches no address it was not given: it calls only the Services3 entries below.
// Services are append-only: a later stage 1 adds entries at the end and raises version; a
// stage 2 needs version >= services_version (and bytes >= sizeof(Services3)) and refuses init
// otherwise; its header's min_stage1 names the first stage-1 release with what it needs, and
// stage 1 refuses a min_stage1 above its own release (Check::stage1). Stage 1 calls op_turn and
// op_command only after init answered init_ok, and stops calling stage 2 for the rest of the
// boot as soon as an answer is not one it defines.
static const uint32_t services_version = 1;
struct Services3 {                                  // the ABI-3 services table
    uint32_t version, bytes;                        // services_version, sizeof(Services3)
    uint32_t stage1_version;                        // this stage 1's release (XYF_VERSION_CODE)
    uint32_t (*now_ms)();                           // ms since the USB service started
    void (*trace)(uint32_t code, uint32_t value);   // last trace point (USB1 block, STATUS)
    // A reply frame through the USB service: type | 0x80, seq, payload (u32 status first). 1 = sent.
    uint32_t (*reply)(uint32_t type, uint32_t seq, const uint8_t* payload, uint32_t length);
};
enum Op : uint32_t { op_init = 1, op_turn = 2, op_command = 3 };
static const uint32_t init_ok = 0x4b4f3253u;        // "S2OK": op_init's only accepted answer
static const uint32_t turn_ok = 0;                   // op_turn's only accepted answer
static const uint32_t not_handled = 0, handled = 1;  // op_command: stage 1 answers unsupported / stage 2 replied
static const uint32_t trace_init = 1;                // stage 2's trace at init (value: its version)
struct Call {
    uint32_t abi, bytes, op;                        // abi_service, sizeof(Call), Op
    const Services3* services;
    const Header* header;                           // the accepted header (every op)
    uint32_t type, seq, length;                     // op_command: the request
    const uint8_t* payload;
};
}}
#endif

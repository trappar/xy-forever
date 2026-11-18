#ifndef XYF_MOD_PATCH_HPP
#define XYF_MOD_PATCH_HPP
#include "hash.hpp"
#include "mod_link.hpp"
namespace xyf_core { namespace mod_patch {
// The patch step of mod assembly (docs/storage/mods-assembly.md): a freestanding port of
// open-xy builder/core.mjs patchFirmware, in place on the caller's image buffer.
//   image[0, stock_bytes)            stock os.ldr, loaded by the caller (SHA-256 pinned: proven after
//                                    assembly, by the restore check below; a difference is Status::stock)
//   image[stock_bytes, + module)     the module, written there by the linker
// Result, in the same buffer: stock with every edit applied up to FINAL, the module's loader record
// (module_base) in FINAL's old place, the module, FINAL after it, the first record's length
// rewritten: core.mjs's candidate byte for byte. Capacity: stock_bytes + 16 + module limit
// (4,454,120 B for 1.2.1; 6,551,272 B with the 4 MiB layout). The base must be a known one or confirmed (below). Every edit's preimage and
// mapping (inside one stored record's payload) is checked against stock, active or
// not; hooks must land inside the module; no two edits overlap; undoing the edits and removing the
// record must give stock's SHA-256; ldr::inspect with the caller's policy (the cold LDR policy)
// must accept the image. The work runs in steps (step(): Status::more) of one slice of hashing or
// one pass over the edits. No heap, no BSS, no library calls, every loop bounded.
enum class Status {
    ok, more, wait, argument, capacity, edits, stock, layout, mapping, preimage, service, destination, branch,
    overlap, module, verify, policy, base, unverified
};
// The profile facts the patch is checked against (profile.json: stock_sha256, stock_bytes,
// stock_records, module_base, reserved_bytes, reserved - table_bytes, heap_fill_base/_bytes) and the
// official catalog's SHA-256 (the content step's pin: pass it as mod_content::Catalog::sha256).
// expanded_base: the module base of the profile's larger layout (info.txt expanded_module_base, 0: none;
// core.mjs offersExpandedMemory). `expanded` is the caller's choice, the website's experimental 4 MiB option
// (core.mjs expandedProfile): the module starts at expanded_base and ends where it did (the relocated
// tables at its top, every hook and other edit unchanged), and the two heap edits load the smaller heap
// (expanded_base - heap_fill_base). The facts stay the standard ones; the patch derives the layout.
struct Profile {   // field order fixed: the known-base table (mod_patch.cpp) matches its first 96 bytes (92 + expanded_base)
    hash::Digest stock_sha256, catalog_sha256;
    U32 stock_bytes, stock_records, module_base, reserved_bytes, module_bytes, heap_fill_base, heap_fill_bytes;
    U32 expanded_base;
    bool expanded;
};
// The layout the patch and link use: `profile` itself, or (expanded) its larger layout. False when expanded is
// asked for and the base offers no valid larger layout (no expanded_base, or the sizes do not fit together).
bool layout(const Profile& profile, Profile& out);
struct Limits {
    U32 edits, records, slice_bytes;   // edits.bin edits, stock loader records, bytes hashed per step
    Limits() : edits(512), records(2048), slice_bytes(131072) {}
};
struct Edit {
    U32 address, offset, before, after;   // before/after: offsets into edits.bin (after 0: none)
    unsigned short length, symbol;        // symbol: string offset, 0xffff none
    Byte kind, flags, service, active;
    Byte redirect[12];                    // a hook's or the startup label's computed bytes
};
struct Record { U32 offset, target, count, code; };
// Workspace for the tables: 4-byte aligned, scratch_bytes(limits) bytes (0: limits out of range).
U32 scratch_bytes(const Limits& limits);
static const U32 max_edits = 4096, max_records = 16384, max_services = 32, max_edit_bytes = 128,
                 max_edits_file = 65536, detail_bytes = 32;
// Caller-owned state of one patch (or verify) run; not shared between concurrent runs. The profile,
// policy, edits.bin and the linker's workspace and result must stay alive until the run ends.
struct Patcher {
    const Profile* profile; const Policy* policy;                           // the caller's, kept alive
    const mod_link::Workspace* link; const mod_link::Result* linked;          // the linker's (mod_link.hpp)
    U32 slice_bytes, limit_edits, limit_records;
    const Byte* edits; Byte* image; U32 edits_bytes, capacity;
    Edit* table; unsigned short* order; Record* records;
    U32 edit_count, service_count, record_count, strings, string_bytes;
    Profile layout;                       // the layout in use (layout() of *profile)
    U32 phase, cursor, module_bytes, total, applied;
    bool profiling;
    bool known;                           // the base is in the known-base table (else confirmed unverified)
    Status failure;                       // after a refusal: what step() keeps returning
    unsigned short service_names[max_services];
    Byte final[16];
    hash::Sha256 whole, restored;
    hash::Digest sha256;                  // of the assembled (or verified) image, after ok
    Summary ldr;                          // ldr::inspect's summary (after ok)
    char detail[detail_bytes];            // which edit, record or check refused
};
// Known bases: official open-xy bases compiled in (mod_known_bases.inc, written by `modkit
// known-base`). Before any byte of edits.bin is used, start() and start_verify() look the base up by
// every profile fact, its catalog SHA-256 and `edits_sha256`: a match is known (p.known). Anything else
// is refused with Status::unverified ("base") unless `unverified_confirmed`: the user confirmed this
// unverified base on the device; it then runs with p.known false, which the caller shows.
// Starts a patch. `edits_sha256`: the base's files.sha256 entry for edits.bin. `profiling`: false
// is the website's mode (per-voice measuring off: the profiling hooks applied). Then step() until
// Status::wait, set_module(), step() until Status::ok.
Status start(Patcher& p, const Profile& profile, const Byte* edits, U32 edits_bytes, const hash::Digest& edits_sha256,
             Byte* image, U32 capacity, bool profiling, bool unverified_confirmed, const Policy& policy,
             const Limits& limits, Byte* scratch, U32 scratch_capacity);
// The linker's finished link (status ok, begun with module = image + stock_bytes): the module's size
// (sizes.initialized), its active services (service_at) and globals (global) are read from it.
Status set_module(Patcher& p, const mod_link::Workspace& link, const mod_link::Result& linked);
Status step(Patcher& p);
// Admission self-check of an assembled image (e.g. read back): the base checked as by start(), its
// loader layout is stock's plus the one module record before FINAL, every edit maps into a stock
// record's payload (as in a patch), undoing the edits and removing the record gives stock's SHA-256,
// and the policy accepts it; then step() until Status::ok. The image is only read. Edit windows hold
// computed bytes it cannot check: after ok the CALLER MUST compare p.sha256 with the SHA-256 recorded
// when the image was assembled, and treat a difference as a refusal. `hashed`: the caller has already
// proven the image's SHA-256 equal to that of an image this code assembled (start() to ok, which ran the
// restore check): the layout checks and the policy only, no hashing; p.sha256 is left as that patch set it
// (the same Patcher), for the caller's comparison (build 190).
Status start_verify(Patcher& p, const Profile& profile, const Byte* edits, U32 edits_bytes,
                    const hash::Digest& edits_sha256, const Byte* image, U32 size, bool unverified_confirmed,
                    const Policy& policy, const Limits& limits, Byte* scratch, U32 scratch_capacity,
                    bool hashed = false);
// The profile facts from the base's whole info.txt: needs_os, catalog_sha256 (64 hex digits), stock_bytes,
// stock_records, module_base, reserved_bytes, module_bytes, heap_fill_base, heap_fill_bytes (decimal
// or 0x + 1-8 hex digits), each once, and expanded_module_base at most once (absent: 0); `expanded` is set
// false; other keys, comments and blank lines are skipped. ok, or
// Status::base with `detail` = the key missing, repeated or malformed, or "profile" when the values
// do not fit together.
Status read_profile(const Byte* info, U32 bytes, Profile& profile, char (&detail)[detail_bytes]);
const char* status_name(Status status);
} }
#endif

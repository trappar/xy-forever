#ifndef XYF_STATE_WRITE_HPP
#define XYF_STATE_WRITE_HPP
#include <stdint.h>
#include "xyf_library.hpp"
// XY Forever "set default" (the only storage write; never on a normal boot).
// Pure planning and command decoding (host-tested); processor a performs the
// write with cold_storage::Stock121ReadOnlySession::write_state_slot.
//
// Commands from b (tagged op-10 arguments 'CM' << 16 | item << 8 | seq):
//   item_set_default + i        menu: b has the catalog, i = library index
//   item_id_nibble + k, seq = nibble k of CRC-32(id) << 3, k = 0..7, then
//   item_set_default_by_id      USB: the host names the entry by id
// Nibbles, not bytes: the low three bits of every command value stay 0 (docs/HARDWARE.md: a
// command that reaches A's bootloader selects partition value & 7; 0 is the user area).
namespace xyf { namespace state_write {
static const uint32_t max_bytes = 4096;
static const uint32_t item_set_default = 0x40;              // .. 0x4f
static const uint32_t item_id_nibble = 0x68;                // .. 0x6f
static const uint32_t item_set_default_by_id = 0x64;
// Processor a work area for the USB path (a's SDRAM; same layout as xyf_boot).
static const uint32_t a_library = 0x8b812000u, a_scan = 0x8b814000u, a_scan_bytes = 0xa000u;
static const uint32_t a_plan = 0x8b820000u, a_readback = 0x8b822000u;
static const char slot_a_path[] = "/yaffs2/xy-forever/state-a.txt";
static const char slot_b_path[] = "/yaffs2/xy-forever/state-b.txt";
enum Status : uint32_t {
    status_none = 0, status_ok = 1, status_bad_entry = 2, status_unusable = 3, status_plan = 4,
    status_not_mounted = 5, status_storage = 6, status_hash = 7     // storage: low byte cold_storage::Result
};
struct Plan {
    const char* path;          // slot_a_path or slot_b_path
    uint32_t seq, length;
    char text[max_bytes + 1];
};
// Next slot for "default = entry index": keeps the other state fields, writes
// the slot not currently in use (the older or invalid one) with seq + 1.
bool plan_default(const library::Library& library, uint32_t index, Plan& out);
// Same from the current state choice and a checked id (USB path: no library scan).
bool plan_default_id(const state::Choice& current, const char* id, Plan& out);
uint32_t id_hash(const char* id);                            // zlib CRC-32 of the id bytes
// Unique usable entry with this id hash, -1 if none, -2 if ambiguous.
int32_t find_by_hash(const library::Library& library, uint32_t hash);
// USB: collects the eight hash nibbles; returns true on a commit with all eight.
struct Collector { uint32_t mask, hash; };
bool collect(Collector& collector, uint32_t command);
// b side: the nine command values for an id (seq values for the commit/index
// commands are supplied by the caller's sequence counter).
uint32_t id_nibble_command(uint32_t hash, uint32_t k);   // k = 0..7
} }
#endif

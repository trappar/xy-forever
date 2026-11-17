#ifndef XYF_STORE_HPP
#define XYF_STORE_HPP
#include <stdint.h>
// XY Forever storage formats (docs/STORAGE.md): key=value text for
// state.txt / info.txt and a bounded reader for open-xy layout.json.
// Freestanding: no libc, no allocation, every loop bounded by its input.
namespace xyf { namespace store {
static const uint32_t max_text = 4096, max_layout = 8192, max_key = 31, max_value = 127;
static const uint32_t max_id = 31, max_machines = 64, max_machine_id = 15, max_depth = 8;

// Library entry / machine identifiers: [a-z0-9_-]{1,31}.
bool valid_id(const char* id);

typedef void (*Visit)(void* context, const char* key, const char* value);
// Calls visit(key, value) for each well-formed line; skips comments, blank and
// malformed lines (bad key, missing '=', key/value too long). Returns the
// number of pairs visited, or -1 if length > max_text.
int parse_key_values(const char* text, uint32_t length, Visit visit, void* context);

struct State {
    char default_id[max_id + 1], once[max_id + 1];
    char stage2[64], stage2_previous[64];
    uint32_t stage2_tries;
};
// Unknown keys ignored; ids that fail valid_id() are dropped (left empty).
bool parse_state(const char* text, uint32_t length, State& state);
// Canonical form (fixed key order, LF, only non-empty fields). Returns bytes
// written (without NUL), or 0 if capacity is too small.
uint32_t write_state(const State& state, char* out, uint32_t capacity);

enum Kind : uint32_t { kind_unknown = 0, kind_official = 1, kind_open_xy = 2, kind_custom = 3 };
struct Info {
    char name[48], version[32];
    Kind kind;
    uint32_t bytes, crc32;
    bool has_bytes, has_crc32;
    bool has_generation;       // a generation key (an assembled mod set: xyf_library content_installed)
};
bool parse_info(const char* text, uint32_t length, Info& info);

enum MachineKind : uint8_t { machine_engine = 1, machine_effect = 2 };
struct Machine { char id[max_machine_id + 1]; MachineKind kind; int16_t number, page; };
struct Layout { char profile[32]; uint32_t count; Machine machines[max_machines]; };
// Accepts only {"format":"opxy-layout/1", ...}; machines with an invalid id,
// missing number/page, or both engine and effect are rejected (whole file).
bool parse_layout(const char* json, uint32_t length, Layout& layout);
} }
#endif

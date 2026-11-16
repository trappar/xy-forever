#ifndef XYF_STATE_HPP
#define XYF_STATE_HPP
#include <stdint.h>
#include "xyf_store.hpp"
// Power-safe state.txt without rename (docs/STORAGE.md, State slots):
// two alternating slots state-a.txt / state-b.txt, each "key=value" lines plus
// "seq=<n>" and a final "crc32=<8 hex>" line covering every byte before it.
// Read: the valid slot with the higher seq wins; legacy state.txt (no CRC)
// is used only when neither slot is valid. Write: rewrite the other slot.
namespace xyf { namespace state {
enum Source : uint32_t { source_none = 0, source_a = 1, source_b = 2, source_legacy = 3 };
struct Slot { bool valid; uint32_t seq; store::State state; };
// Parses one slot; valid only with a correct trailing crc32 line and a seq.
bool parse_slot(const char* text, uint32_t length, Slot& out);
// Canonical slot text (state fields, seq, crc32 line). 0 if capacity is too small.
uint32_t write_slot(const store::State& state, uint32_t seq, char* out, uint32_t capacity);
// Folds one more candidate into the running choice (call for a, b, legacy).
struct Choice { Source source; uint32_t seq; store::State state; };
void consider(Choice& choice, Source source, const char* text, uint32_t length);
// Which slot the next write must replace, and its seq.
void next_write(const Choice& choice, Source& target, uint32_t& seq);
} }
#endif

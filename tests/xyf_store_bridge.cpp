// Host bridge for xyf_store tests (not linked into the device).
#include "../target/xyf_store.hpp"
using namespace xyf::store;
extern "C" {
unsigned store_valid_id(const char* id) { return valid_id(id); }
int store_state(const char* text, unsigned length, State* out) { return parse_state(text, length, *out) ? 1 : 0; }
unsigned store_write_state(const State* s, char* out, unsigned capacity) { return write_state(*s, out, capacity); }
int store_info(const char* text, unsigned length, Info* out) { return parse_info(text, length, *out) ? 1 : 0; }
int store_layout(const char* json, unsigned length, Layout* out) { return parse_layout(json, length, *out) ? 1 : 0; }
unsigned store_sizes(unsigned which) { return which == 0 ? sizeof(State) : which == 1 ? sizeof(Info) : sizeof(Layout); }
}

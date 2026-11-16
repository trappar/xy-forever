// Host bridge: device xyf::state::write_slot for the host-tool parser test.
#include "../target/xyf_state.hpp"
#include <string.h>
extern "C" unsigned slot_default(const char* id, unsigned seq, char* out, unsigned capacity) {
    xyf::store::State s; memset(&s, 0, sizeof(s));
    strncpy(s.default_id, id, sizeof(s.default_id) - 1);
    return xyf::state::write_slot(s, seq, out, capacity);
}

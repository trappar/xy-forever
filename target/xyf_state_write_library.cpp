#include "xyf_state_write.hpp"
// Library-based helpers (declared in xyf_state_write.hpp), in their own object:
// the menu boot links them; the USB service (module budget) does not.
namespace xyf { namespace state_write {
namespace {
void copy_state(store::State& to, const store::State& from) {
    const char* f = reinterpret_cast<const char*>(&from); char* t = reinterpret_cast<char*>(&to);
    for (uint32_t i = 0; i != sizeof(store::State); ++i) t[i] = f[i];
}
}

bool plan_default(const library::Library& lib, uint32_t index, Plan& out) {
    out.path = 0; out.seq = 0; out.length = 0; out.text[0] = 0;
    if (index >= lib.count || !lib.entries[index].usable) return false;
    state::Choice current;
    copy_state(current.state, lib.state);
    current.source = state::Source(lib.has_state ? lib.state_source : state::source_none);
    current.seq = lib.state_seq;
    return plan_default_id(current, lib.entries[index].id, out);
}

int32_t find_by_hash(const library::Library& lib, uint32_t hash) {
    int32_t found = -1;
    for (uint32_t i = 0; i != lib.count; ++i) {
        if (!lib.entries[i].usable || id_hash(lib.entries[i].id) != hash) continue;
        if (found >= 0) return -2;
        found = int32_t(i);
    }
    return found;
}
} }

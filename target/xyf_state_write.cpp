#include "xyf_state_write.hpp"
#include "xyf_crc32.hpp"
namespace xyf { namespace state_write {
namespace {
const uint32_t command_tag = 0x434du;                        // "CM" (peer_services command_tag)
void copy_state(store::State& to, const store::State& from) {
    const char* f = reinterpret_cast<const char*>(&from); char* t = reinterpret_cast<char*>(&to);
    for (uint32_t i = 0; i != sizeof(store::State); ++i) t[i] = f[i];
}
}

bool plan_default_id(const state::Choice& current, const char* id, Plan& out) {
    out.path = 0; out.seq = 0; out.length = 0; out.text[0] = 0;
    if (!id || !store::valid_id(id)) return false;
    state::Source target; uint32_t seq;
    state::next_write(current, target, seq);
    if (target != state::source_a && target != state::source_b) return false;
    store::State next;
    if (current.source == state::source_none) for (uint32_t i = 0; i != sizeof(next); ++i) reinterpret_cast<char*>(&next)[i] = 0;
    else copy_state(next, current.state);
    uint32_t n = 0;
    for (; id[n] && n != store::max_id; ++n) next.default_id[n] = id[n];
    next.default_id[n] = 0;
    next.once[0] = 0;                                        // a new default clears a pending "once"
    const uint32_t length = state::write_slot(next, seq, out.text, sizeof(out.text));
    if (!length || length > max_bytes) return false;
    out.path = target == state::source_a ? slot_a_path : slot_b_path;
    out.seq = seq;
    out.length = length;
    return true;
}


uint32_t id_hash(const char* id) {
    uint32_t n = 0;
    while (id[n] && n != 64) ++n;
    return crc::update(0, reinterpret_cast<const uint8_t*>(id), n);
}


bool collect(Collector& c, uint32_t command) {
    if ((command >> 16) != command_tag) return false;
    const uint32_t item = (command >> 8) & 0xffu, value = command & 0xffu;
    if (item >= item_id_nibble && item < item_id_nibble + 8) {
        if (value & 0x87u) return false;                    // not a nibble << 3
        const uint32_t k = item - item_id_nibble;
        c.hash = (c.hash & ~(0xfu << (4 * k))) | (value >> 3) << (4 * k);
        c.mask |= 1u << k;
        return false;
    }
    if (item != item_set_default_by_id) return false;
    const bool complete = c.mask == 0xffu;
    c.mask = 0;
    return complete;
}

uint32_t id_nibble_command(uint32_t hash, uint32_t k) {
    return command_tag << 16 | (item_id_nibble + (k & 7u)) << 8 | ((hash >> (4 * (k & 7u))) & 0xfu) << 3;
}
} }

// Machine numbering: a port of open-xy builder/core.mjs numberLayout, validIdentity,
// validRequest and fits (MIT, commit 52c8de9). Keys ("engine 34", "page 82") are held in a table
// per slot for numbers below max_rule_number and in a short list for any other a layout names.
#include "mod_internal.hpp"

namespace xyf_core { namespace mod_link { namespace numbers {
namespace {
const U32 slot_page = 3, slots = 4, table_size = max_rule_number;
const char* const slot_names[slots] = {"engine", "effect", "player", "page"};

struct Over { U32 slot; int number; U32 holder; };
struct Fresh { U32 index, kind, slots; Identity identity; };   // slots: bit per slot code
struct Work {
    U16* table;                  // [slots][table_size]: holder + 1, 0 = free
    Over* over; U32 over_count, over_capacity;
    U16* pool[slots]; U32 pool_length[slots];
    Fresh* fresh; U32 fresh_count;
    U16* order;
};

bool listed(const U32* list, U32 count, int value) {
    for (U32 i = 0; i != count; ++i) if (int(list[i]) == value) return true;
    return false;
}
U32 remainder(U32 value, U32 divisor) {           // value % divisor, without a library call
    U32 rest = 0;
    for (U32 bit = 32; bit; --bit) {
        rest = (rest << 1) | ((value >> (bit - 1)) & 1u);
        if (rest >= divisor) rest -= divisor;
    }
    return rest;
}
bool same_id(const char* a, const char* b) {
    for (U32 i = 0; i != id_bytes; ++i) {
        if (a[i] != b[i]) return false;
        if (!a[i]) return true;
    }
    return true;
}
bool before(const char* a, const char* b) {       // a < b, bytewise (ids are ASCII)
    for (U32 i = 0; i != id_bytes; ++i) {
        if (Byte(a[i]) != Byte(b[i])) return Byte(a[i]) < Byte(b[i]);
        if (!a[i]) return false;
    }
    return false;
}
void copy_id(char* out, const char* id) {
    U32 i = 0;
    for (; i + 1 < id_bytes && id[i]; ++i) out[i] = id[i];
    for (; i != id_bytes; ++i) out[i] = 0;
}
bool owns_page(const Rules& r, U32 kind, const Identity& id) {
    if (kind == effect) return !id.has_page || id.page != int(r.effect_page);
    return kind == sample && (!id.has_page || !listed(r.sample_pages, r.sample_page_count, id.page));
}
bool same_numbers(const Identity& a, const Identity& b) {
    return a.slot == b.slot && a.has_number == b.has_number && a.has_page == b.has_page &&
        (!a.has_number || a.number == b.number) && (!a.has_page || a.page == b.page);
}
U32 get(const Work& w, U32 slot, int number) {
    if (number >= 0 && U32(number) < table_size) return w.table[slot * table_size + U32(number)];
    for (U32 i = 0; i != w.over_count; ++i)
        if (w.over[i].slot == slot && w.over[i].number == number) return w.over[i].holder;
    return 0;
}
bool set(Work& w, U32 slot, int number, U32 holder) {
    if (number >= 0 && U32(number) < table_size) { w.table[slot * table_size + U32(number)] = U16(holder); return true; }
    for (U32 i = 0; i != w.over_count; ++i)
        if (w.over[i].slot == slot && w.over[i].number == number) { w.over[i].holder = holder; return true; }
    if (w.over_count == w.over_capacity) return false;
    w.over[w.over_count].slot = slot; w.over[w.over_count].number = number; w.over[w.over_count].holder = holder;
    ++w.over_count;
    return true;
}
void drop_pages(Work& w, U32 holder) {
    for (U32 n = 0; n != table_size; ++n)
        if (w.table[slot_page * table_size + n] == holder) w.table[slot_page * table_size + n] = 0;
    for (U32 i = 0; i != w.over_count; ++i)
        if (w.over[i].slot == slot_page && w.over[i].holder == holder) w.over[i].holder = 0;
}
// occupied(rules, identity) as (slot, number) pairs; returns the count (at most 3).
U32 occupied(const Rules& r, const Identity& id, U32* key_slot, int* key_number) {
    U32 n = 0;
    key_slot[n] = id.slot; key_number[n++] = id.number;
    if (id.slot == slot_effect) { key_slot[n] = slot_engine; key_number[n++] = id.number; }
    if (!listed(r.sample_pages, r.sample_page_count, id.page) && id.page != int(r.effect_page)) {
        key_slot[n] = slot_page; key_number[n++] = id.page;
    }
    return n;
}
void key_text(Text& t, U32 slot, int number) { t.add(slot_names[slot]); t.add(" "); t.add_number(number); }

struct Layout {
    const Rules& rules; Work& w; Number* out; U32 capacity; U32& count; Text& message;
    int find(const char* id) const {
        for (U32 i = 0; i != count; ++i) if (same_id(out[i].id, id)) return int(i);
        return -1;
    }
    Status insert(const char* id, const Identity& identity, U32& index) {
        if (count == capacity) { message.add("numbers"); return Status::limit; }
        copy_id(out[count].id, id); out[count].identity = identity; index = count++;
        return Status::ok;
    }
    // hold(name, identity): its keys are free or already its own.
    Status hold(U32 index, const Identity& identity) {
        U32 key_slot[3]; int key_number[3];
        U32 keys = occupied(rules, identity, key_slot, key_number);
        for (U32 k = 0; k != keys; ++k) {
            U32 holder = get(w, key_slot[k], key_number[k]);
            if (!holder) {
                if (!set(w, key_slot[k], key_number[k], index + 1)) { message.add("held numbers"); return Status::limit; }
                holder = index + 1;
            }
            if (holder != index + 1) {
                message.add(out[index].id); message.add(" and "); message.add(out[holder - 1].id);
                message.add(" both hold "); key_text(message, key_slot[k], key_number[k]);
                return Status::both_hold;
            }
        }
        out[index].identity = identity;
        return Status::ok;
    }
    bool held(U32 slot, int number) const {
        if (get(w, slot, number)) return true;
        return slot == slot_effect && get(w, slot_engine, number);
    }
    bool free(U32 slot, int number) const {
        return !(slot == slot_page && number == int(rules.pages) - 1) && !held(slot, number);
    }
    bool take(U32 index, Identity& identity, U32 slot, int number) {
        if (slot == slot_page) { identity.page = number; identity.has_page = true; }
        else { identity.number = number; identity.has_number = true; }
        if (!set(w, slot, number, index + 1)) return false;
        return slot != slot_effect || set(w, slot_engine, number, index + 1);
    }
};
}

U32 fnv1a(const Byte* text, U32 length) {
    U32 value = 0x811c9dc5u;
    XYF_WORK(length);
    for (U32 i = 0; i != length; ++i) value = (value ^ text[i]) * 0x01000193u;
    return value;
}
U32 slot_of(U32 kind) { return kind == player ? slot_player : kind == effect ? slot_effect : slot_engine; }

bool valid_rules(const Rules& r) {
    if (r.engines > table_size || r.pages > table_size || r.engine_none >= table_size ||
        r.page_none >= table_size || r.effect_page >= table_size ||
        r.stock_engine_count > max_rule_list || r.sample_page_count > max_rule_list ||
        r.effect_count > max_rule_list || r.player_count > max_rule_list) return false;
    for (U32 i = 0; i != r.stock_engine_count; ++i) if (r.stock_engines[i] >= table_size) return false;
    for (U32 i = 0; i != r.sample_page_count; ++i) if (r.sample_pages[i] >= table_size) return false;
    for (U32 i = 0; i != r.effect_count; ++i) if (r.effects[i] >= table_size) return false;
    for (U32 i = 0; i != r.player_count; ++i) if (r.players[i] >= table_size) return false;
    return true;
}

static bool valid_identity(const Rules& r, U32 kind, const Identity& id) {
    if (id.slot != slot_of(kind) || !id.has_number || !id.has_page) return false;
    int n = id.number, page = id.page;
    bool own_page = page > int(r.page_none) && page < int(r.pages);
    if (kind == player) return listed(r.players, r.player_count, n) && own_page;
    if (kind == effect) return listed(r.effects, r.effect_count, n) && (page == int(r.effect_page) || own_page);
    if (!(n > 0 && n < int(r.engines) && n != int(r.engine_none) && !listed(r.stock_engines, r.stock_engine_count, n)))
        return false;
    if (kind == sample) return n > int(r.engine_none) && (listed(r.sample_pages, r.sample_page_count, page) || own_page);
    return (kind == synth || kind == drum) && own_page;
}

bool valid_request(const Rules& r, U32 kind, const Identity& id) {
    if (id.slot != slot_of(kind)) return false;
    if (id.has_number) return valid_identity(r, kind, id);
    if (kind == effect) return !id.has_page || id.page == int(r.effect_page);
    if (kind == sample) return !id.has_page || listed(r.sample_pages, r.sample_page_count, id.page);
    return (kind == synth || kind == drum || kind == player) && !id.has_page;
}

bool fits(const Identity& requested, const Identity& numbers) {
    if (requested.has_number && !(numbers.slot == requested.slot && numbers.has_number &&
                                  numbers.number == requested.number)) return false;
    return !requested.has_page || (numbers.has_page && numbers.page == requested.page);
}

U32 work_bytes(U32 capacity) {
    if (capacity > 4096) return 0;
    return slots * table_size * 2 + 4 * capacity * sizeof(Over) + slots * table_size * 2 +
        capacity * sizeof(Fresh) + ((capacity * 2 + 3) & ~3u) + 16;
}

Status layout(const Rules& rules, const Number* fixed, U32 fixed_count, const Number* saved,
              U32 saved_count, const Machine* machines, U32 machine_count, Byte* area,
              Number* out, U32 capacity, U32& count, Text& message) {
    count = 0;
    if (!valid_rules(rules) || capacity > 4096 || machine_count > capacity) return Status::argument;
    Work w;
    w.table = reinterpret_cast<U16*>(area);
    Byte* at = area + slots * table_size * 2;
    w.over = reinterpret_cast<Over*>(at); w.over_count = 0; w.over_capacity = 4 * capacity;
    at += 4 * capacity * sizeof(Over);
    for (U32 s = 0; s != slots; ++s) w.pool[s] = reinterpret_cast<U16*>(at + s * table_size * 2);
    at += slots * table_size * 2;
    w.fresh = reinterpret_cast<Fresh*>(at); w.fresh_count = 0;
    at += capacity * sizeof(Fresh);
    w.order = reinterpret_cast<U16*>(at);
    for (U32 i = 0; i != slots * table_size; ++i) w.table[i] = 0;
    // numberPools(rules)
    w.pool_length[slot_engine] = 0;
    for (U32 n = rules.engine_none + 1; n < rules.engines; ++n)
        if (!listed(rules.stock_engines, rules.stock_engine_count, int(n))) w.pool[slot_engine][w.pool_length[slot_engine]++] = U16(n);
    w.pool_length[slot_page] = 0;
    for (U32 n = rules.page_none + 1; n < rules.pages; ++n) w.pool[slot_page][w.pool_length[slot_page]++] = U16(n);
    w.pool_length[slot_effect] = rules.effect_count;
    for (U32 i = 0; i != rules.effect_count; ++i) w.pool[slot_effect][i] = U16(rules.effects[i]);
    w.pool_length[slot_player] = rules.player_count;
    for (U32 i = 0; i != rules.player_count; ++i) w.pool[slot_player][i] = U16(rules.players[i]);

    Layout l = {rules, w, out, capacity, count, message};
    Status status;
    for (U32 i = 0; i != fixed_count; ++i) {
        if (!fixed[i].identity.has_number || !fixed[i].identity.has_page) return Status::argument;
        U32 index;
        int found = l.find(fixed[i].id);
        if (found >= 0) index = U32(found);
        else if ((status = l.insert(fixed[i].id, fixed[i].identity, index)) != Status::ok) return status;
        if ((status = l.hold(index, fixed[i].identity)) != Status::ok) return status;
    }
    for (U32 i = 0; i != saved_count; ++i) {
        const Identity& id = saved[i].identity;
        if (!id.has_number || !id.has_page || id.slot > slot_player) return Status::argument;
        int found = l.find(saved[i].id);
        if (found >= 0) {
            if (!same_numbers(out[found].identity, id)) {
                message.add(saved[i].id);
                return Status::layout_fixed;
            }
            continue;
        }
        // An effect number later found unusable on stock is let go and numbered again.
        if (id.slot == slot_effect && !listed(rules.effects, rules.effect_count, id.number)) continue;
        U32 index;
        if ((status = l.insert(saved[i].id, id, index)) != Status::ok) return status;
        if ((status = l.hold(index, id)) != Status::ok) return status;
    }
    // Machines by id (insertion sort: at most `capacity`).
    for (U32 i = 0; i != machine_count; ++i) {
        U32 j = i;
        while (j && before(machines[i].id, machines[w.order[j - 1]].id)) { w.order[j] = w.order[j - 1]; --j; }
        w.order[j] = U16(i);
    }
    for (U32 o = 0; o != machine_count; ++o) {
        const Machine& m = machines[w.order[o]];
        U32 slot = slot_of(m.kind);
        bool own_page = owns_page(rules, m.kind, m.requested);
        int found = l.find(m.id);
        if (found >= 0 && own_page != owns_page(rules, m.kind, out[found].identity)) {
            const Identity& had = out[found].identity;
            if (m.kind == sample) {
                const Identity& page = own_page ? had : m.requested;
                if (!page.has_page || page.page != 2) {
                    message.add(m.id);
                    return Status::forks;
                }
            }
            drop_pages(w, U32(found) + 1);
            if (own_page) {
                Fresh& f = w.fresh[w.fresh_count++];
                f.index = U32(found); f.kind = m.kind; f.slots = 1u << slot_page;
                f.identity = had; f.identity.has_page = false; f.identity.page = 0;
            } else {
                out[found].identity.has_page = m.requested.has_page;
                out[found].identity.page = m.requested.page;
            }
        } else if (found >= 0) {
            if (!valid_identity(rules, m.kind, out[found].identity)) {
                message.add(m.id);
                return Status::layout_invalid;
            }
            if (m.kind == sample && !own_page &&
                !(out[found].identity.has_page == m.requested.has_page && out[found].identity.page == m.requested.page)) {
                message.add(m.id);
                return Status::forks;
            }
        } else {
            U32 index;
            Identity placeholder = m.requested;
            if ((status = l.insert(m.id, placeholder, index)) != Status::ok) return status;
            Fresh& f = w.fresh[w.fresh_count++];
            f.index = index; f.kind = m.kind; f.identity = m.requested;
            f.slots = (1u << slot) | ((m.kind == synth || m.kind == drum || m.kind == player || own_page) ? 1u << slot_page : 0);
        }
    }
    // Requested numbers first, so no machine's preferred place takes one another asks for.
    for (U32 i = 0; i != w.fresh_count; ++i) {
        Fresh& f = w.fresh[i];
        for (U32 s = 0; s != slots; ++s) {
            if (!(f.slots & (1u << s))) continue;
            bool has = s == slot_page ? f.identity.has_page : f.identity.has_number;
            int n = s == slot_page ? f.identity.page : f.identity.number;
            if (s == slot_page) f.identity.has_page = false; else f.identity.has_number = false;
            bool pooled = false;
            for (U32 k = 0; has && k != w.pool_length[s] && !pooled; ++k) pooled = int(w.pool[s][k]) == n;
            if (pooled && l.free(s, n) && !l.take(f.index, f.identity, s, n)) {
                message.add("held numbers"); return Status::limit;
            }
        }
    }
    // The slot order of core.mjs's slot lists: the machine's slot, then the page.
    for (U32 i = 0; i != w.fresh_count; ++i) {
        Fresh& f = w.fresh[i];
        const char* id = out[f.index].id;
        U32 order[2] = {slot_of(f.kind), slot_page};
        for (U32 o = 0; o != 2; ++o) {
            U32 s = order[o];
            if (!(f.slots & (1u << s))) continue;
            if (s == slot_page ? f.identity.has_page : f.identity.has_number) continue;
            U32 length = w.pool_length[s], found_at = length;
            if (length) {
                U32 id_length = 0;
                while (id_length < id_bytes && id[id_length]) ++id_length;
                U32 start = remainder(fnv1a(reinterpret_cast<const Byte*>(id), id_length), length);
                for (U32 k = 0; k != length && found_at == length; ++k) {
                    U32 at_k = start + k >= length ? start + k - length : start + k;
                    if (l.free(s, int(w.pool[s][at_k]))) found_at = at_k;
                }
            }
            if (found_at == length) {
                message.add(slot_names[s]); message.add(" number for "); message.add(id);
                return Status::no_free;
            }
            if (!l.take(f.index, f.identity, s, int(w.pool[s][found_at]))) { message.add("held numbers"); return Status::limit; }
        }
        if (!valid_identity(rules, f.kind, f.identity)) {
            message.add(id);
            return Status::no_valid;
        }
        if ((status = l.hold(f.index, f.identity)) != Status::ok) return status;
    }
    return Status::ok;
}
} } }

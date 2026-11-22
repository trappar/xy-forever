// Device mod linker: a port of open-xy builder/core.mjs linkKit, selectRuntime, activeServices,
// numberSymbols and openKit's object and number checks (MIT, commit 52c8de9), over the package
// files of docs/storage/mods.md ("Mod packages"), in steps of one whole file (mod_link.hpp).
#include "mod_internal.hpp"

namespace xyf_core { namespace mod_link {
namespace {
const U32 none = 0xffffffffu, max_claims = 16, max_extensions = 16, max_services = 32, max_words = 32, max_fixed = 64,
          max_tokens = 48, max_path = 200, max_package = 40, base_files = 10, max_chain = 64;
const U16 no_place = 0xffff, abs_place = 0xfffe;
enum Phase { ph_base = 0, ph_package = 1, ph_pass1 = 2, ph_zero = 3, ph_pass2 = 4, ph_done = 5 };
enum Role { role_core = 0, role_service = 1, role_runtime = 2, role_machine = 3, role_extension = 4 };
enum ObjectState { unused = 0, selected = 1, candidate = 2, chosen = 3, adding = 4 };
enum Flag { f_defined = 1, f_imported = 2, f_global = 4, f_alias = 8, f_definition = 16 };
const char* const kind_names[] = {"synth", "drum", "sample", "effect", "player"};
const char* const base_paths[base_files] = {"info.txt",    "services.txt",  "machines.txt",  "extensions.txt",
                                            "fixed.txt",   "absent.txt",    "fallbacks.txt", "definitions.txt",
                                            "objects.txt", "identities.txt"};
const char* const rule_names[9] = {"engines",       "engine_none",  "pages",   "page_none", "effect_page",
                                   "stock_engines", "sample_pages", "effects", "players"};
const char* const accessor_names[4] = {"hooks", "processor", "panel", "filter"};

struct Name {
    U32 text, value, alias, hash;
    U16 next, length;
    Byte flags, pad;
    U16 owner;
};   // owner: package (0 = base)
struct Object {
    U32 path, order, bytes, hash, first, count, first_record;
    U16 path_length, owner, extension, records;   // owner: machine + 1 (0 = sdk); extension + 1
    Byte role, service, state, weak;
};
struct Placed {
    U32 address, size, alignment;
    U16 index;
    Byte group, pad;
};
struct Record {
    U32 value;
    U16 name, place;
    Byte import, inside, pad[2];
};   // place: placed index
struct Machine {
    char id[id_bytes];
    U32 prefix, users, kind;
    U16 prefix_length;
    Byte selected, pad;
    Identity requested;
};
struct Extension {
    char id[33];
    Byte processor, chosen, accessors, exports, objects, pad[2];
    U32 claims, claims_length, labels, labels_length;
};
struct Service {
    U32 name, users, length;
};
struct Alias {
    U32 owner, symbol, fallback;
};   // absent: machine; fallbacks: service name

struct State {
    Limits limits;
    Rules rules;
    const Selection* selection;
    Result* result;
    Byte* module;
    U32 module_capacity;
    Byte* elf_work;
    U32 elf_work_bytes;
    Name* names;
    U32 name_count;
    U16* heads;
    U32 head_mask;
    char* text;
    U32 text_used;
    Object* objects;
    U32 object_count;
    Placed* placed;
    U32 placed_count;
    Record* records;
    U32 record_count;
    U32* linked;
    U32 linked_count;
    U32* sorted;
    U32 candidates;
    U16* section_map;
    Machine* machines;
    U32 machine_count;
    Alias* absent;
    U32 absent_count;
    Alias* fallbacks;
    U32 fallback_count;
    Number* numbers;
    U32 number_count;
    Byte* numbers_work;
    numbers::Machine* numbering;
    Text detail;
    U32 phase, index, steps, active, zeroed;
    Extension extensions[max_extensions];
    U32 extension_count;
    Service services[max_services];
    U32 service_count;
    U32 word_text[max_words], word_length[max_words], word_count;
    Number fixed[max_fixed];
    U32 fixed_count;
    U32 module_base, module_bytes, packages;
    char generation[64];
    char package[max_package + 1], path[max_path + 1];
    // the current table line
    const Byte* line;
    U32 token_at[max_tokens], token_length[max_tokens], tokens, line_number;
    struct {
        char generation[64], id[33], machine[16], prefix[48], type[16];
        U32 module_base, module_bytes, expanded_base;
        bool base_ok, bytes_ok, expanded_ok;
    } info;
};

U32 align4(U32 n) { return (n + 3u) & ~3u; }
U32 length_of(const char* s, U32 cap) {
    U32 n = 0;
    while (n < cap && s[n]) ++n;
    return n;
}
const Byte* bytes(const char* s) { return reinterpret_cast<const Byte*>(s); }
bool equal_bytes(const Byte* a, U32 n, const Byte* b, U32 m) {
    if (n != m) return false;
    XYF_WORK(n);
    for (U32 i = 0; i != n; ++i)
        if (a[i] != b[i]) return false;
    return true;
}
bool equal(const Byte* a, U32 n, const char* b) { return equal_bytes(a, n, bytes(b), length_of(b, 0xffff)); }
bool same_cstr(const char* a, const char* b, U32 cap) { return equal(bytes(a), length_of(a, cap), b); }
void copy_text(char* out, U32 cap, const Byte* in, U32 n) {
    U32 i = 0;
    for (; i < n && i + 1 < cap; ++i) out[i] = char(in[i]);
    out[i] = 0;
}
bool valid_id(const Byte* s, U32 n, U32 limit) {     // [a-z][a-z0-9_]{0,limit-1}
    if (!n || n > limit || s[0] < 'a' || s[0] > 'z') return false;
    for (U32 i = 1; i != n; ++i)
        if (!((s[i] >= 'a' && s[i] <= 'z') || (s[i] >= '0' && s[i] <= '9') || s[i] == '_')) return false;
    return true;
}
Status refuse(State& st, Status status, const Byte* detail, U32 length) {
    st.detail.add(detail, length);
    return status;
}
Status refuse(State& st, Status status, const char* detail) {
    return refuse(st, status, bytes(detail), length_of(detail, 0xffff));
}

// ------------------------------------------------------------------ names
const Byte* name_text(const State& st, U32 index) { return bytes(st.text + st.names[index].text); }
// A chain's cost: 1 per name, 16 per name with the same full hash (its bytes are compared).
// intern() refuses a new name whose chain already costs max_chain, which bounds every lookup.
U32 lookup(const State& st, const Byte* text, U32 length, U32& hash, U32& cost) {
    hash = numbers::fnv1a(text, length);
    cost = 0;
    for (U32 at = st.heads[hash & st.head_mask]; at != no_place; at = st.names[at].next) {
        XYF_WORK(1);
        if (st.names[at].hash != hash) {
            ++cost;
            continue;
        }
        if (equal_bytes(name_text(st, at), st.names[at].length, text, length)) return at;
        cost += 16;
    }
    return none;
}
U32 lookup(const State& st, const Byte* text, U32 length) {
    U32 hash, cost;
    return lookup(st, text, length, hash, cost);
}
Status store_text(State& st, const Byte* text, U32 length, U32& at) {
    if (length > st.limits.text_bytes - st.text_used) return refuse(st, Status::limit, "text");
    at = st.text_used;
    for (U32 i = 0; i != length; ++i) st.text[at + i] = char(text[i]);
    st.text_used += length;
    return Status::ok;
}
Status intern(State& st, const Byte* text, U32 length, U32& index) {
    if (length > elf::max_name) return refuse(st, Status::name_length, text, length);
    U32 hash, cost;
    index = lookup(st, text, length, hash, cost);
    if (index != none) return Status::ok;
    if (cost >= max_chain) return refuse(st, Status::name_chain, text, length);
    if (st.name_count == st.limits.names) return refuse(st, Status::limit, "names");
    U32 at;
    Status status = store_text(st, text, length, at);
    if (status != Status::ok) return status;
    Name& n = st.names[st.name_count];
    n.text = at;
    n.length = U16(length);
    n.flags = 0;
    n.owner = 0;
    n.value = n.alias = 0;
    n.hash = hash;
    n.next = st.heads[hash & st.head_mask];
    st.heads[hash & st.head_mask] = U16(st.name_count);
    index = st.name_count++;
    return Status::ok;
}

// ------------------------------------------------------------------ tables (one whole file each)
struct Cursor {
    const Byte* data;
    U32 size, at;
    bool next(State& st, U32& length) {     // the next line into st.line, tokens split at spaces
        if (at >= size) return false;
        st.line = data + at;
        length = 0;
        while (at < size && data[at] != '\n') {
            ++at;
            ++length;
        }
        ++at;
        ++st.line_number;
        st.tokens = 0;
        U32 start = 0;
        for (U32 i = 0; i <= length && st.tokens < max_tokens; ++i)
            if (i == length || st.line[i] == ' ') {
                st.token_at[st.tokens] = start;
                st.token_length[st.tokens++] = i - start;
                start = i + 1;
            }
        return true;
    }
};
const Byte* token(const State& st, U32 i) { return st.line + st.token_at[i]; }
bool comment(const State& st) { return !st.tokens || !st.token_length[0] || st.line[0] == '#'; }
// The value of "key=value" among the tokens from `from`, or null.
const Byte* field(const State& st, U32 from, const char* key, U32& length) {
    U32 k = length_of(key, 32);
    for (U32 i = from; i < st.tokens; ++i) {
        const Byte* t = token(st, i);
        if (st.token_length[i] > k && t[k] == '=' && equal_bytes(t, k, bytes(key), k)) {
            length = st.token_length[i] - k - 1;
            return t + k + 1;
        }
    }
    length = 0;
    return 0;
}
bool parse_unsigned(const Byte* s, U32 n, U32& value) {
    value = 0;
    if (!n || n > 9) return false;
    for (U32 i = 0; i != n; ++i) {
        if (s[i] < '0' || s[i] > '9') return false;
        value = value * 10u + (s[i] - '0');
    }
    return true;
}
bool parse_hex(const Byte* s, U32 n, U32& value) {
    value = 0;
    if (n >= 2 && s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) {
        s += 2;
        n -= 2;
    }
    if (!n || n > 8) return false;
    for (U32 i = 0; i != n; ++i) {
        U32 c = s[i] | 0x20u;
        U32 digit = c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : 16;
        if (digit == 16) return false;
        value = (value << 4) | digit;
    }
    return true;
}
bool parse_optional(const Byte* s, U32 n, bool& has, int& value) {   // "-" = null, else an integer
    has = false;
    value = 0;
    if (n == 1 && s[0] == '-') return true;
    bool negative = n && s[0] == '-';
    U32 magnitude;
    if (!parse_unsigned(s + negative, n - negative, magnitude)) return false;
    has = true;
    value = negative ? -int(magnitude) : int(magnitude);
    return true;
}
U32 hex_digit(Byte c) {
    c = Byte(c | 0x20u);
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10u : 16u;
}
U32 list_items(const Byte* s, U32 n) {       // items of a comma-separated list
    U32 count = 1;
    for (U32 i = 0; i != n; ++i) count += s[i] == ',';
    return count;
}
bool valid_labels(const Byte* s, U32 n) {    // %XX escapes only (modkit claim_labels_field)
    for (U32 i = 0; i != n; ++i)
        if (s[i] == '%' && (n - i < 3 || hex_digit(s[i + 1]) == 16 || hex_digit(s[i + 2]) == 16)) return false;
    return true;
}
Status malformed(State& st) {
    st.detail.add(st.path);
    st.detail.add(":");
    st.detail.add_unsigned(st.line_number);
    return Status::table;
}
U32 word_bit(const State& st, const Byte* s, U32 n) {
    for (U32 i = 0; i != st.word_count; ++i)
        if (equal_bytes(bytes(st.text + st.word_text[i]), st.word_length[i], s, n)) return 1u << i;
    return 0;
}
// Bits of a comma-separated list ("-" = none); unknown words are added when `add`.
Status words(State& st, const Byte* s, U32 n, bool add, U32& bits) {
    bits = 0;
    if (n == 1 && s[0] == '-') return Status::ok;
    U32 start = 0;
    for (U32 i = 0; i <= n; ++i) {
        if (i != n && s[i] != ',') continue;
        U32 bit = word_bit(st, s + start, i - start);
        if (!bit && add) {
            if (st.word_count == max_words) return refuse(st, Status::limit, "service users");
            Status status = store_text(st, s + start, i - start, st.word_text[st.word_count]);
            if (status != Status::ok) return status;
            st.word_length[st.word_count] = i - start;
            bit = 1u << st.word_count++;
        }
        bits |= bit;
        start = i + 1;
    }
    return Status::ok;
}
// Identity from "<engine|effect|player>=N|- page=N|-" among the tokens from `from`.
bool identity(State& st, U32 from, Identity& id) {
    const char* slots[] = {"engine", "effect", "player"};
    U32 sn = 0, pn = 0;
    const Byte* number = 0;
    for (U32 k = 0; k != 3 && !number; ++k) {
        number = field(st, from, slots[k], sn);
        id.slot = k;
    }
    const Byte* page = field(st, from, "page", pn);
    return number && page && parse_optional(number, sn, id.has_number, id.number) &&
           parse_optional(page, pn, id.has_page, id.page);
}

// identities.txt: "<rule> <value>" per line, each of rule_names once (a list comma-separated, "-" = none).
Status parse_rules(State& st, Cursor& c) {
    Rules& r = st.rules;
    U32* scalars[5] = {&r.engines, &r.engine_none, &r.pages, &r.page_none, &r.effect_page};
    U32* lists[4] = {r.stock_engines, r.sample_pages, r.effects, r.players};
    U32* counts[4] = {&r.stock_engine_count, &r.sample_page_count, &r.effect_count, &r.player_count};
    U32 seen = 0, length;
    while (c.next(st, length)) {
        if (comment(st)) continue;
        U32 k = 0;
        while (k != 9 && !equal(token(st, 0), st.token_length[0], rule_names[k])) ++k;
        if (st.tokens != 2 || k == 9 || ((seen >> k) & 1u)) return malformed(st);
        seen |= 1u << k;
        const Byte* v = token(st, 1);
        U32 n = st.token_length[1];
        if (k < 5) {
            if (!parse_unsigned(v, n, *scalars[k])) return malformed(st);
            continue;
        }
        if (n == 1 && v[0] == '-') continue;
        U32& count = *counts[k - 5];
        for (U32 i = 0, start = 0; i <= n; ++i) {
            if (i != n && v[i] != ',') continue;
            if (count == max_rule_list || !parse_unsigned(v + start, i - start, lists[k - 5][count]))
                return malformed(st);
            ++count;
            start = i + 1;
        }
    }
    if (seen != 0x1ffu || !numbers::valid_rules(r)) return refuse(st, Status::table, "base/identities.txt");
    return Status::ok;
}

Status parse_info(State& st, Cursor& c) {
    st.info.generation[0] = st.info.id[0] = st.info.machine[0] = st.info.prefix[0] = st.info.type[0] = 0;
    st.info.base_ok = st.info.bytes_ok = st.info.expanded_ok = false;
    const char* keys[] = {"generation", "id", "machine", "prefix", "type"};
    char* outs[] = {st.info.generation, st.info.id, st.info.machine, st.info.prefix, st.info.type};
    const U32 caps[] = {64, 33, 16, 48, 16};
    U32 length;
    while (c.next(st, length)) {
        U32 eq = 0;
        while (eq < length && st.line[eq] != '=') ++eq;
        if (eq == length || st.line[0] == '#') continue;
        const Byte* value = st.line + eq + 1;
        U32 n = length - eq - 1;
        for (U32 k = 0; k != 5; ++k)
            if (equal(st.line, eq, keys[k]) && !outs[k][0]) {
                if (n >= caps[k]) return refuse(st, Status::text_length, keys[k]);   // device rule: not truncated
                copy_text(outs[k], caps[k], value, n);
            }
        if (equal(st.line, eq, "module_base")) st.info.base_ok = parse_hex(value, n, st.info.module_base);
        if (equal(st.line, eq, "module_bytes")) st.info.bytes_ok = parse_unsigned(value, n, st.info.module_bytes);
        if (equal(st.line, eq, "expanded_module_base"))
            st.info.expanded_ok = parse_hex(value, n, st.info.expanded_base);
    }
    return Status::ok;
}

bool valid_path(const Byte* s, U32 n) {      // objects/<[A-Za-z0-9_.-]+ components>.o, no "." or ".."
    if (n < 11 || n > max_path || !equal_bytes(s, 8, bytes("objects/"), 8) || s[n - 2] != '.' || s[n - 1] != 'o')
        return false;
    U32 start = 8;
    for (U32 i = 8; i <= n; ++i) {
        if (i == n || s[i] == '/') {
            U32 len = i - start;
            if (!len || (len == 1 && s[start] == '.') || (len == 2 && s[start] == '.' && s[start + 1] == '.'))
                return false;
            start = i + 1;
            continue;
        }
        U32 c = s[i] | 0x20u;
        if (!((c >= 'a' && c <= 'z') || (s[i] >= '0' && s[i] <= '9') || s[i] == '_' || s[i] == '.' || s[i] == '-'))
            return false;
    }
    return true;
}
bool runtime_name(const Byte* s, U32 n) {    // /^sdk\/runtime\/_[A-Za-z0-9_]+\.o$/
    if (n < 16 || !equal_bytes(s, 13, bytes("sdk/runtime/_"), 13) || s[n - 2] != '.' || s[n - 1] != 'o') return false;
    for (U32 i = 13; i != n - 2; ++i) {
        U32 c = s[i] | 0x20u;
        if (!((c >= 'a' && c <= 'z') || (s[i] >= '0' && s[i] <= '9') || s[i] == '_')) return false;
    }
    return true;
}
Status parse_objects(State& st, Cursor& c, U32 package, U32 owner, U32 extension) {
    U32 length;
    Status status;
    while (c.next(st, length)) {
        if (comment(st)) continue;
        U32 order;
        if (st.tokens != 3 || !parse_unsigned(token(st, 0), st.token_length[0], order) ||
            !valid_path(token(st, 2), st.token_length[2]))
            return malformed(st);
        if (st.object_count == st.limits.objects) return refuse(st, Status::limit, "objects");
        Object& o = st.objects[st.object_count];
        const Byte* role = token(st, 1);
        U32 rn = st.token_length[1];
        o.service = 0xff;
        o.owner = U16(owner);
        o.extension = U16(extension);
        if (!package && equal(role, rn, "core")) o.role = role_core;
        else if (!package && equal(role, rn, "runtime")) {
            o.role = role_runtime;
            if (!runtime_name(token(st, 2) + 8, st.token_length[2] - 8))
                return refuse(st, Status::table, token(st, 2) + 8, st.token_length[2] - 8);
        } else if (!package && rn > 8 && equal_bytes(role, 8, bytes("service="), 8)) {
            o.role = role_service;
            for (U32 i = 0; i != st.service_count; ++i)
                if (equal_bytes(role + 8, rn - 8, bytes(st.text + st.services[i].name), st.services[i].length))
                    o.service = Byte(i);
            if (o.service == 0xff) return refuse(st, Status::table, role + 8, rn - 8);   // "Undeclared service"
        } else if (owner && equal(role, rn, "machine")) o.role = role_machine;
        else if (extension && equal(role, rn, "extension")) o.role = role_extension;
        else return refuse(st, Status::package, role, rn);
        o.order = order;
        if ((status = store_text(st, token(st, 2), st.token_length[2], o.path)) != Status::ok) return status;
        o.path_length = U16(st.token_length[2]);
        o.state = unused;
        o.weak = 0;
        o.bytes = o.hash = o.first = o.count = o.first_record = 0;
        o.records = 0;
        ++st.object_count;
    }
    return Status::ok;
}

// One base table (base_paths[index]).
Status parse_base(State& st, U32 index, Cursor& c) {
    U32 length, n;
    Status status = Status::ok;
    if (index == 0) {
        if ((status = parse_info(st, c)) != Status::ok) return status;
        if (!same_cstr(st.info.type, "mod-base", 16) || !st.info.generation[0] || !st.info.base_ok ||
            !st.info.bytes_ok || !st.info.module_bytes || st.info.module_base > 0xffffffffu - st.info.module_bytes)
            return refuse(st, Status::table, "base/info.txt");
        copy_text(st.generation, 64, bytes(st.info.generation), length_of(st.info.generation, 64));
        st.module_base = st.info.module_base;
        st.module_bytes = st.info.module_bytes;
        if (st.selection->expanded) {     // the larger layout keeps the module's end
            const U32 x = st.info.expanded_base;
            if (!st.info.expanded_ok || !x || x >= st.module_base)
                return refuse(st, Status::table, "expanded_module_base");
            st.module_bytes += st.module_base - x;
            st.module_base = x;
        }
        return Status::ok;
    }
    if (index == 8) return parse_objects(st, c, 0, 0, 0);
    if (index == 9) return parse_rules(st, c);
    while (c.next(st, length)) {
        if (comment(st)) continue;
        if (index == 1) {            // services.txt: name users=a,b ...
            const Byte* users = field(st, 1, "users", n);
            if (!users || st.service_count == max_services) return malformed(st);
            Service& s = st.services[st.service_count];
            if ((status = store_text(st, token(st, 0), st.token_length[0], s.name)) != Status::ok ||
                (status = words(st, users, n, true, s.users)) != Status::ok)
                return status;
            s.length = st.token_length[0];
            ++st.service_count;
        } else if (index == 2) {     // machines.txt: order id kind= prefix= <slot>= page= features= ...
            U32 order, kn, pn, fn;
            if (st.tokens < 2 || !parse_unsigned(token(st, 0), st.token_length[0], order) ||
                order != st.machine_count || !valid_id(token(st, 1), st.token_length[1], id_bytes - 1) ||
                st.machine_count == st.limits.machines)
                return malformed(st);
            Machine& m = st.machines[st.machine_count];
            copy_text(m.id, id_bytes, token(st, 1), st.token_length[1]);
            const Byte* kind = field(st, 2, "kind", kn);
            const Byte* prefix = field(st, 2, "prefix", pn);
            const Byte* features = field(st, 2, "features", fn);
            if (!kind || !prefix || !pn || pn > 47 || !identity(st, 2, m.requested)) return malformed(st);
            m.kind = numbers::other;
            for (U32 k = 0; k != 5; ++k)
                if (equal(kind, kn, kind_names[k])) m.kind = k;
            if ((status = store_text(st, prefix, pn, m.prefix)) != Status::ok) return status;
            m.prefix_length = U16(pn);
            U32 bits = 0;
            if (features && (status = words(st, features, fn, false, bits)) != Status::ok) return status;
            m.users =
                bits | (m.kind < 5 ? word_bit(st, bytes(kind_names[m.kind]), length_of(kind_names[m.kind], 8)) : 0);
            m.selected = 0;
            ++st.machine_count;
        } else if (index == 3) { // extensions.txt: order id link= services= claims= labels= accessors= processor= ...
            U32 order, cn, ln, an, pn;
            if (st.tokens < 2 || !parse_unsigned(token(st, 0), st.token_length[0], order) ||
                order != st.extension_count || !valid_id(token(st, 1), st.token_length[1], 32))
                return malformed(st);
            if (st.extension_count == max_extensions) return Status::collection; // 'Invalid extension collection'
            Extension& e = st.extensions[st.extension_count];
            copy_text(e.id, 33, token(st, 1), st.token_length[1]);
            const Byte* claims = field(st, 2, "claims", cn);
            const Byte* labels = field(st, 2, "labels", ln);
            const Byte* accessors = field(st, 2, "accessors", an);
            const Byte* processor = field(st, 2, "processor", pn);
            if (!claims || !labels || !accessors || !processor) return malformed(st);
            e.processor = !(pn == 1 && processor[0] == '-');
            e.accessors = 0;
            for (U32 i = 0, start = 0; i <= an; ++i) {
                if (i != an && accessors[i] != ',') continue;
                for (U32 k = 0; k != 4; ++k)
                    if (equal(accessors + start, i - start, accessor_names[k])) e.accessors |= Byte(1u << k);
                start = i + 1;
            }
            e.claims_length = cn == 1 && claims[0] == '-' ? 0 : cn;
            if (e.claims_length && list_items(claims, cn) > max_claims)
                return refuse(st, Status::claim_count, token(st, 1), st.token_length[1]);
            e.labels_length = e.claims_length ? ln : 0; // one label per claim, "-" with none
            if (e.claims_length ? list_items(labels, ln) != list_items(claims, cn) || !valid_labels(labels, ln) :
                                  !(ln == 1 && labels[0] == '-'))
                return malformed(st);
            if ((status = store_text(st, claims, e.claims_length, e.claims)) != Status::ok ||
                (status = store_text(st, labels, e.labels_length, e.labels)) != Status::ok)
                return status;
            e.chosen = e.objects = e.exports = 0;
            ++st.extension_count;
        } else if (index == 4) { // fixed.txt: machine <slot>=N page=N
            if (st.tokens != 3 || st.fixed_count == max_fixed ||
                !valid_id(token(st, 0), st.token_length[0], id_bytes - 1))
                return malformed(st);
            Number& f = st.fixed[st.fixed_count++];
            copy_text(f.id, id_bytes, token(st, 0), st.token_length[0]);
            if (!identity(st, 1, f.identity) || !f.identity.has_number || !f.identity.has_page) return malformed(st);
        } else { // absent.txt, fallbacks.txt (owner symbol fallback); definitions.txt
            if (st.tokens != (index == 7 ? 2u : 3u) || !st.token_length[1] || (index != 7 && !st.token_length[2]))
                return malformed(st);
            U32 symbol;
            if (index == 7) {
                U32 value;
                if (!parse_hex(token(st, 1), st.token_length[1], value)) return malformed(st);
                if ((status = intern(st, token(st, 0), st.token_length[0], symbol)) != Status::ok) return status;
                st.names[symbol].flags |= Byte(f_global | f_definition | f_defined);
                st.names[symbol].value = value;
                continue;
            }
            U32 owner = none;
            if (index == 5) { // rows of machines outside the collection are never used
                for (U32 i = 0; i != st.machine_count && owner == none; ++i)
                    if (equal(token(st, 0), st.token_length[0], st.machines[i].id)) owner = i;
                if (owner == none) continue;
            }
            U32& count = index == 5 ? st.absent_count : st.fallback_count;
            if (count == (index == 5 ? st.limits.absent : st.limits.fallbacks))
                return refuse(st, Status::limit, base_paths[index]);
            Alias& a = (index == 5 ? st.absent : st.fallbacks)[count];
            a.owner = owner;
            if ((index == 6 && (status = intern(st, token(st, 0), st.token_length[0], a.owner)) != Status::ok) ||
                (status = intern(st, token(st, 1), st.token_length[1], a.symbol)) != Status::ok ||
                (status = intern(st, token(st, 2), st.token_length[2], a.fallback)) != Status::ok)
                return status;
            ++count;
        }
    }
    if (index == 3) {
        // modkit rebuild_kit: every id is in this generation (the first unknown is the detail).
        const Selection& sel = *st.selection;
        for (U32 i = 0; i != sel.machine_count + sel.extension_count; ++i) {
            bool machine = i < sel.machine_count;
            const char* id = machine ? sel.machines[i] : sel.extensions[i - sel.machine_count];
            bool found = false;
            for (U32 k = 0; k != (machine ? st.machine_count : st.extension_count) && !found; ++k)
                if (machine ? same_cstr(id, st.machines[k].id, id_bytes) : same_cstr(id, st.extensions[k].id, 33)) {
                    found = true;
                    if (machine) st.machines[k].selected = 1;
                    else st.extensions[k].chosen = 1;
                }
            if (!found) return refuse(st, Status::unknown_id, id);
        }
        for (U32 i = 0; i != st.machine_count; ++i) st.packages += st.machines[i].selected;
        for (U32 i = 0; i != st.extension_count; ++i) st.packages += st.extensions[i].chosen;
    }
    return Status::ok;
}

// The k-th chosen package (machines, then extensions, in collection order): 1-based index.
U32 chosen_package(const State& st, U32 k) {
    for (U32 i = 0; i != st.machine_count + st.extension_count; ++i)
        if ((i < st.machine_count ? st.machines[i].selected : st.extensions[i - st.machine_count].chosen) && !k--)
            return i + 1;
    return 0;
}
void set_package(State& st, U32 package) { // 0 base, 1.. machines, then "ext-" + extensions
    U32 at = 0;
    const char* id = "base";
    if (package > st.machine_count) {
        for (const char* p = "ext-"; *p; ++p) st.package[at++] = *p;
        id = st.extensions[package - 1 - st.machine_count].id;
    } else if (package) id = st.machines[package - 1].id;
    for (U32 i = 0; id[i] && at < max_package; ++i) st.package[at++] = id[i];
    st.package[at] = 0;
}
void set_path(State& st, const char* path, U32 n) {
    for (U32 i = 0; i != n && i < max_path; ++i) st.path[i] = path[i];
    st.path[n < max_path ? n : max_path] = 0;
}

// A chosen package's info.txt (even step) or objects.txt (odd step).
Status parse_package(State& st, U32 package, bool objects, Cursor& c) {
    bool machine = package <= st.machine_count;
    if (objects) return parse_objects(st, c, package, machine ? package : 0, machine ? 0 : package - st.machine_count);
    Status status = parse_info(st, c);
    if (status != Status::ok) return status;
    if (!same_cstr(st.info.generation, st.generation, 64)) return refuse(st, Status::generation, st.package);
    bool same = same_cstr(st.info.type, "mod", 16);
    if (machine) {
        const Machine& m = st.machines[package - 1];
        same = same && m.kind < 5 && same_cstr(st.info.machine, kind_names[m.kind], 16) &&
               same_cstr(st.info.id, m.id, id_bytes) &&
               equal_bytes(bytes(st.info.prefix), length_of(st.info.prefix, 48), bytes(st.text + m.prefix),
                           m.prefix_length);
    } else {
        same = same && same_cstr(st.info.machine, "extension", 16) &&
               same_cstr(st.info.id, st.extensions[package - 1 - st.machine_count].id, 33);
    }
    return same ? Status::ok : refuse(st, Status::package, st.package);
}

// After the last package file: kit order, openKit's number checks, services, which objects link.
Status prepare(State& st) {
    for (U32 i = 1; i < st.object_count; ++i) { // kit order: ascending keys (insertion sort)
        Object o = st.objects[i];
        U32 j = i;
        while (j && st.objects[j - 1].order > o.order) {
            st.objects[j] = st.objects[j - 1];
            --j;
        }
        st.objects[j] = o;
    }
    for (U32 i = 1; i < st.object_count; ++i)
        if (st.objects[i].order == st.objects[i - 1].order) return Status::order;
    if (!st.machine_count || st.machine_count > st.rules.engines)
        return Status::collection; // 'Unsupported curated kit'
    for (U32 i = 0; i != st.machine_count; ++i) {
        const Machine& m = st.machines[i];
        if (!numbers::valid_request(st.rules, m.kind, m.requested)) return refuse(st, Status::numbers_request, m.id);
        for (U32 f = 0; f != st.fixed_count; ++f)
            if (same_cstr(st.fixed[f].id, m.id, id_bytes) && !numbers::fits(m.requested, st.fixed[f].identity))
                return refuse(st, Status::fixed_differs, m.id);
    }
    U32 count = 0;
    Status status = numbers::layout(st.rules, st.fixed, st.fixed_count, 0, 0, 0, 0, st.numbers_work, st.numbers,
                                    st.limits.numbers, count, st.detail);
    if (status != Status::ok) return status;
    U32 users = 0;
    bool extension = false, processor = false;
    for (U32 i = 0; i != st.machine_count; ++i)
        if (st.machines[i].selected) users |= st.machines[i].users;
    for (U32 i = 0; i != st.extension_count; ++i)
        if (st.extensions[i].chosen) {
            extension = true;
            processor = processor || st.extensions[i].processor;
        }
    if (extension) users |= word_bit(st, bytes("extension"), 9);
    if (processor) users |= word_bit(st, bytes("master_filter"), 13);
    for (U32 i = 0; i != st.service_count; ++i)
        if (st.services[i].users & users) st.active |= 1u << i;
    for (U32 i = 0; i != st.object_count; ++i) {
        Object& o = st.objects[i];
        o.state = Byte(o.role == role_runtime                                    ? candidate :
                       o.role != role_service || ((st.active >> o.service) & 1u) ? selected :
                                                                                   unused);
        if (o.role == role_extension) ++st.extensions[o.extension - 1].objects;
    }
    return Status::ok;
}

// ------------------------------------------------------------------ objects
U32 package_of(const State& st, const Object& o) {
    return o.owner ? o.owner : o.extension ? st.machine_count + o.extension : 0u;
}
bool skip(const elf::Symbol& s, U32 length) {
    return !s.binding || !length || s.type == 4;
} // locals, unnamed, STT_FILE

__attribute__((noinline)) Status changed(State& st, const Object& o) {
    return refuse(st, Status::changed, bytes(st.text + o.path + 8), o.path_length - 8u);
}
Status check_object(State& st, U32 index, const Byte* data, U32 size, elf::Header& h, bool first) {
    Object& o = st.objects[index];
    st.result->object_bytes += size;
    U32 hash = numbers::fnv1a(data, size);
    if (!first && (hash != o.hash || size != o.bytes)) return changed(st, o);
    o.hash = hash;
    o.bytes = size;
    const Byte* name;
    U32 name_length;
    Status status = elf::check(data, size, st.elf_work, st.elf_work_bytes, h, name, name_length);
    if (status == Status::section_kind) st.detail.add(name, name_length);
    else if (status != Status::ok) st.detail.add(bytes(st.text + o.path + 8), o.path_length - 8u);
    return status;
}

// Pass 1: openKit's readObject on every object; for those that can link, their allocated sections,
// their exports (with what definedValue needs) and imports. Weak symbols are refused later, in
// linkKit's order.
Status pass1(State& st, U32 index, const Byte* d, U32 size) {
    Object& o = st.objects[index];
    elf::Header h;
    Status status = check_object(st, index, d, size, h, true);
    if (status != Status::ok) return status;
    bool linkable = o.state == selected || o.state == candidate;
    if (linkable) {
        o.first = st.placed_count;
        for (U32 k = 0; k != h.count; ++k) {
            st.section_map[k] = no_place;
            U32 g = elf::group(d, h, k);
            if (g == elf::none) continue;
            if (st.placed_count >= st.limits.sections) return refuse(st, Status::limit, "sections");
            elf::Section s;
            elf::section(d, h, k, s);
            Placed& p = st.placed[st.placed_count];
            p.address = 0;
            p.size = s.size;
            p.alignment = s.alignment;
            p.index = U16(k);
            p.group = Byte(g);
            st.section_map[k] = U16(st.placed_count++);
            ++o.count;
        }
    }
    o.first_record = st.record_count;
    U32 symbols = elf::symbol_count(d, h);
    for (U32 k = 0; k != symbols; ++k) {
        elf::Symbol s;
        elf::symbol(d, h, k, s);
        const Byte* text;
        U32 length;
        elf::symbol_name(d, h, s, text, length);
        // Device rule beyond core.mjs: a package object (machine or extension) has no absolute symbol
        // a relocation or the link could use (any binding but STT_FILE): it would reach any address.
        if (s.section == elf::shn_abs && s.type != 4 && (o.owner || o.extension))
            return refuse(st, Status::absolute, bytes(st.text + o.path + 8), o.path_length - 8u);
        if (skip(s, length)) continue;
        if (s.binding != 1) {
            o.weak = 1;
            continue;
        }
        if (o.role == role_extension && s.section) { // openKit's accessor checks
            Extension& e = st.extensions[o.extension - 1];
            U32 id = length_of(e.id, 33);
            if (length > 15 + id && equal_bytes(text, 14, bytes("sdk_extension_"), 14) &&
                equal_bytes(text + 14, id, bytes(e.id), id) && text[14 + id] == '_')
                for (U32 x = 0; x != 4; ++x)
                    if (equal(text + 15 + id, length - 15 - id, accessor_names[x])) e.exports |= Byte(1u << x);
        }
        if (!linkable) continue;
        U32 name;
        if ((status = intern(st, text, length, name)) != Status::ok) return status;
        if (o.state == selected) st.names[name].flags |= Byte(s.section ? f_defined : f_imported);
        if (o.state == selected && !s.section) continue;
        if (st.record_count == st.limits.records) return refuse(st, Status::limit, "records");
        Record& r = st.records[st.record_count++];
        r.name = U16(name);
        r.import = !s.section;
        r.value = s.value;
        r.inside = 1;
        r.place = abs_place;
        if (s.section && s.section != elf::shn_abs) {
            elf::Section sec;
            elf::section(d, h, s.section, sec);
            r.place = st.section_map[s.section];
            r.inside = s.size <= sec.size && s.value <= sec.size - s.size;
        }
        ++o.records;
    }
    return Status::ok;
}

// selectRuntime: runtime members in name order, while one defines a pending import.
bool path_before(const State& st, U32 a, U32 b) {
    const Object& x = st.objects[a];
    const Object& y = st.objects[b];
    U32 n = x.path_length < y.path_length ? x.path_length : y.path_length;
    for (U32 i = 0; i != n; ++i)
        if (st.text[x.path + i] != st.text[y.path + i]) return Byte(st.text[x.path + i]) < Byte(st.text[y.path + i]);
    return x.path_length < y.path_length;
}
Status runtime_closure(State& st) {
    U32 count = 0;
    for (U32 i = 0; i != st.object_count; ++i) {
        if (st.objects[i].state != candidate) continue;
        U32 j = count++;
        while (j && path_before(st, i, st.sorted[j - 1])) {
            st.sorted[j] = st.sorted[j - 1];
            --j;
        }
        st.sorted[j] = i;
    }
    st.candidates = count;
    for (U32 round = 0; round <= count; ++round) {
        U32 additions = 0;
        for (U32 c = 0; c != count; ++c) {
            Object& o = st.objects[st.sorted[c]];
            if (o.state != candidate) continue;
            for (U32 r = 0; r != o.records; ++r) {
                const Record& rec = st.records[o.first_record + r];
                if (!rec.import && (st.names[rec.name].flags & (f_imported | f_defined)) == f_imported) {
                    o.state = adding;
                    ++additions;
                    break;
                }
            }
        }
        if (!additions) break;
        for (U32 c = 0; c != count; ++c) {
            Object& o = st.objects[st.sorted[c]];
            if (o.state != adding) continue;
            for (U32 r = 0; r != o.records; ++r) {
                const Record& rec = st.records[o.first_record + r];
                if (!rec.import && (st.names[rec.name].flags & f_defined))
                    return refuse(st, Status::runtime_replaces, bytes(st.text + o.path + 8), o.path_length - 8u);
            }
            for (U32 r = 0; r != o.records; ++r) {
                const Record& rec = st.records[o.first_record + r];
                st.names[rec.name].flags |= Byte(rec.import ? f_imported : f_defined);
            }
            o.state = chosen;
        }
    }
    return Status::ok;
}

U32 align_up(U32 n, U32 a, bool& overflow) {
    if (n > 0xffffffffu - (a - 1)) {
        overflow = true;
        return n;
    }
    return (n + a - 1) & ~(a - 1);
}
Status capacity(State& st) { // the detail is limit / 1024 as JavaScript prints it
    st.detail.add_unsigned(st.module_bytes >> 10);
    U32 fraction = st.module_bytes & 1023u;
    if (fraction) st.detail.add(".");
    for (U32 i = 0; i != 10 && fraction; ++i) {
        fraction *= 10u;
        st.detail.add_unsigned(fraction >> 10);
        fraction &= 1023u;
    }
    return Status::capacity;
}
Status place(State& st, Sizes& sizes) {
    if (!st.linked_count) return Status::empty; // (core.mjs links the padding alone if module_base is unaligned)
    U32 cursor = st.module_base, end = st.module_base + st.module_bytes, initialized_end = cursor;
    for (U32 g = elf::text; g <= elf::bss; ++g) {
        U32 alignment = g == elf::text ? 1 : 4;
        for (U32 l = 0; l != st.linked_count; ++l) {
            const Object& o = st.objects[st.linked[l]];
            for (U32 k = 0; k != o.count; ++k)
                if (st.placed[o.first + k].group == g && st.placed[o.first + k].alignment > alignment)
                    alignment = st.placed[o.first + k].alignment;
        }
        bool overflow = false;
        cursor = align_up(cursor, alignment, overflow);
        for (U32 l = 0; l != st.linked_count; ++l) {
            const Object& o = st.objects[st.linked[l]];
            for (U32 k = 0; k != o.count; ++k) {
                Placed& p = st.placed[o.first + k];
                if (p.group != g) continue;
                cursor = align_up(cursor, p.alignment, overflow);
                p.address = cursor;
                if (overflow || cursor > end || p.size > end - cursor) return capacity(st);
                cursor += p.size;
            }
        }
        if (g == elf::data) initialized_end = cursor;
    }
    U32 bss_start = cursor;
    for (U32 l = 0; l != st.linked_count; ++l) {
        const Object& o = st.objects[st.linked[l]];
        for (U32 k = 0; k != o.count; ++k) {
            const Placed& p = st.placed[o.first + k];
            if (p.group == elf::bss && p.size && p.address < bss_start) bss_start = p.address;
        }
    }
    sizes.allocated = cursor - st.module_base;
    sizes.initialized = initialized_end - st.module_base;
    sizes.bss = cursor - bss_start;
    sizes.available = st.module_bytes - sizes.allocated;
    if (!sizes.initialized) return Status::empty;
    if (sizes.initialized > st.module_capacity) return refuse(st, Status::limit, "module buffer");
    st.zeroed = 0; // the zero phase clears it, zero_slice bytes per step
    return Status::ok;
}

Status set_definition(State& st, const Byte* text, U32 length, U32 value) {
    U32 index;
    Status status = intern(st, text, length, index);
    if (status != Status::ok) return status;
    st.names[index].flags |= Byte(f_global | f_definition | f_defined);
    st.names[index].value = value;
    return Status::ok;
}
Status number_symbols(State& st) { // numberSymbols: each machine's numbers when selected, else 0
    for (U32 i = 0; i != st.machine_count; ++i) {
        const Machine& m = st.machines[i];
        const Identity* own = 0;
        for (U32 k = 0; k != st.number_count && m.selected && !own; ++k)
            if (same_cstr(st.numbers[k].id, m.id, id_bytes)) own = &st.numbers[k].identity;
        U32 n = own ? U32(own->number) : 0, page = own ? U32(own->page) : 0, count = 2;
        const char* suffix[3] = {"_engine_id", "_page_id", "_screen_page_id"};
        U32 values[3] = {n, page, page};
        if (m.kind == numbers::player) suffix[0] = "_player_id";
        else if (m.kind == numbers::effect) {
            suffix[0] = "_fx_id";
            suffix[1] = "_fx_page_id";
        } else if (m.kind == numbers::sample && !m.requested.has_page) {
            values[1] = own ? 2 : 0;
            count = 3;
        }
        for (U32 k = 0; k != count; ++k) {
            Byte buffer[64];
            U32 at = 0;
            for (U32 c = 0; c != m.prefix_length; ++c) buffer[at++] = Byte(st.text[m.prefix + c]);
            for (U32 c = 0; suffix[k][c]; ++c) buffer[at++] = Byte(suffix[k][c]);
            Status status = set_definition(st, buffer, at, values[k]);
            if (status != Status::ok) return status;
            st.names[lookup(st, buffer, at)].owner = U16(i + 1);
        }
    }
    return Status::ok;
}

Status alias(State& st, U32 symbol, U32 fallback, Status refusal) {
    if ((st.names[symbol].flags & f_global) || fallback == none || !(st.names[fallback].flags & f_global))
        return refuse(st, refusal, name_text(st, symbol), st.names[symbol].length);
    st.names[symbol].flags |= Byte(f_alias);
    st.names[symbol].alias = st.names[fallback].value;
    return Status::ok;
}
Status aliases(State& st) {
    Status status;
    U32 kit_absent = lookup(st, bytes("kit_absent"), 10);
    for (U32 i = 0; i != st.extension_count; ++i) {
        const Extension& e = st.extensions[i];
        for (U32 x = 0; x != 4 && !e.chosen; ++x) {
            if (x && !(e.processor && (e.accessors & (1u << x)))) continue;
            Byte buffer[64];
            U32 at = 0;
            const char* parts[] = {"sdk_extension_", e.id, "_", accessor_names[x]};
            for (U32 p = 0; p != 4; ++p)
                for (U32 c = 0; parts[p][c]; ++c) buffer[at++] = Byte(parts[p][c]);
            U32 symbol;
            if ((status = intern(st, buffer, at, symbol)) != Status::ok ||
                (status = alias(st, symbol, kit_absent, Status::absent_extension)) != Status::ok)
                return status;
        }
    }
    for (U32 m = 0; m != st.machine_count; ++m)
        for (U32 i = 0; i != st.absent_count && !st.machines[m].selected; ++i)
            if (st.absent[i].owner == m &&
                (status = alias(st, st.absent[i].symbol, st.absent[i].fallback, Status::absent_machine)) != Status::ok)
                return status;
    // Service fallbacks, by service in order of first appearance, while the service is unused.
    for (U32 i = 0; i != st.fallback_count; ++i) {
        U32 service = st.fallbacks[i].owner;
        bool first = true, active = false;
        for (U32 k = 0; k != i && first; ++k) first = st.fallbacks[k].owner != service;
        for (U32 s = 0; s != st.service_count; ++s)
            active = active ||
                     ((st.active >> s) & 1u && equal_bytes(bytes(st.text + st.services[s].name), st.services[s].length,
                                                           name_text(st, service), st.names[service].length));
        for (U32 k = i; k != st.fallback_count && first && !active; ++k)
            if (st.fallbacks[k].owner == service &&
                (status = alias(st, st.fallbacks[k].symbol, st.fallbacks[k].fallback, Status::service_fallback)) !=
                    Status::ok)
                return status;
    }
    return Status::ok;
}

// core.mjs claimLabel: the label the first chosen extension (collection order) has for the claim,
// decoded, else the claim with '_' as ' '.
Status claim_refusal(State& st, const Byte* claim, U32 n) {
    for (U32 i = 0; i != st.extension_count; ++i) {
        const Extension& e = st.extensions[i];
        const Byte* labels = bytes(st.text + e.labels);
        for (U32 c = 0, start = 0, at = 0; c <= e.claims_length && e.chosen; ++c) {
            if (c != e.claims_length && st.text[e.claims + c] != ',') continue;
            U32 end = at;
            while (end != e.labels_length && labels[end] != ',') ++end;
            if (end != at && equal_bytes(claim, n, bytes(st.text + e.claims + start), c - start)) {
                for (U32 k = at; k != end; ++k) {
                    Byte ch = labels[k];
                    if (ch == '%') {
                        ch = Byte(hex_digit(labels[k + 1]) << 4 | hex_digit(labels[k + 2]));
                        k += 2;
                    }
                    st.detail.add(&ch, 1);
                }
                return Status::claim;
            }
            start = c + 1;
            at = end + 1;
        }
    }
    for (U32 b = 0; b != n; ++b) {
        Byte ch = claim[b] == '_' ? Byte(' ') : claim[b];
        st.detail.add(&ch, 1);
    }
    return Status::claim;
}

// After pass 1: linkKit from extensionSelection to the absent aliases.
Status link_tables(State& st, Sizes& sizes) {
    for (U32 i = 0; i != st.extension_count; ++i) { // openKit's accessor checks
        const Extension& e = st.extensions[i];
        if (!e.chosen) continue;
        if (e.objects != 1 || !(e.exports & 1)) return refuse(st, Status::accessor, e.id);
        if (e.processor && (e.exports & 6) != 6 && !(e.exports & 8))
            return refuse(st, Status::processor_accessor, e.id);
    }
    for (U32 i = 0; i != st.extension_count; ++i) { // extensionSelection: no claim twice
        const Extension& e = st.extensions[i];
        for (U32 c = 0, start = 0; c <= e.claims_length && e.chosen; ++c) {
            if (c != e.claims_length && st.text[e.claims + c] != ',') continue;
            const Byte* claim = bytes(st.text + e.claims + start);
            U32 n = c - start;
            start = c + 1;
            for (U32 j = 0; j != i; ++j) {
                const Extension& x = st.extensions[j];
                for (U32 d = 0, s = 0; d <= x.claims_length && x.chosen; ++d) {
                    if (d != x.claims_length && st.text[x.claims + d] != ',') continue;
                    if (n && equal_bytes(claim, n, bytes(st.text + x.claims + s), d - s))
                        return claim_refusal(st, claim, n);
                    s = d + 1;
                }
            }
        }
    }
    U32 machines = 0;
    for (U32 i = 0; i != st.machine_count; ++i)
        if (st.machines[i].selected) {
            numbers::Machine& m = st.numbering[machines++];
            m.id = st.machines[i].id;
            m.kind = st.machines[i].kind;
            m.requested = st.machines[i].requested;
        }
    const Selection& sel = *st.selection;
    Status status = numbers::layout(st.rules, st.fixed, st.fixed_count, sel.has_layout ? sel.layout : 0,
                                    sel.has_layout ? sel.layout_count : 0, st.numbering, machines, st.numbers_work,
                                    st.numbers, st.limits.numbers, st.number_count, st.detail);
    if (status != Status::ok || (status = number_symbols(st)) != Status::ok) return status;
    for (U32 i = 0; i != st.object_count; ++i)
        if (st.objects[i].weak && (st.objects[i].state == selected || st.objects[i].state == candidate))
            return Status::weak;
    if ((status = runtime_closure(st)) != Status::ok) return status;
    st.linked_count = 0;
    for (U32 i = 0; i != st.object_count; ++i)
        if (st.objects[i].state == selected) st.linked[st.linked_count++] = i;
    for (U32 c = 0; c != st.candidates; ++c)
        if (st.objects[st.sorted[c]].state == chosen) st.linked[st.linked_count++] = st.sorted[c];
    if ((status = place(st, sizes)) != Status::ok) return status;
    for (U32 l = 0; l != st.linked_count; ++l) { // globals, from pass 1's records
        const Object& o = st.objects[st.linked[l]];
        for (U32 r = 0; r != o.records; ++r) {
            const Record& rec = st.records[o.first_record + r];
            if (rec.import) continue;
            Name& n = st.names[rec.name];
            if (n.flags & f_global) return refuse(st, Status::duplicate_global, name_text(st, rec.name), n.length);
            if (rec.place == no_place || !rec.inside)
                return refuse(st, Status::symbol_outside, name_text(st, rec.name), n.length);
            n.value = rec.place == abs_place ? rec.value : st.placed[rec.place].address + rec.value;
            n.flags |= Byte(f_global);
            n.owner = U16(package_of(st, o));
        }
    }
    return aliases(st);
}

// Pass 2: an object's bytes into the module, then its relocations (core.mjs's loop). The file
// passed check() again; its placed sections must also be as pass 1 found them (an equal hash and
// size do not prove an unchanged file).
Status pass2(State& st, U32 index, const Byte* d, U32 size) {
    const Object& o = st.objects[index];
    elf::Header h;
    Status status = check_object(st, index, d, size, h, false);
    if (status != Status::ok) return status;
    for (U32 k = 0; k != h.count; ++k) st.section_map[k] = no_place;
    for (U32 k = 0; k != o.count; ++k) {
        const Placed& p = st.placed[o.first + k];
        elf::Section s;
        s.size = ~p.size;
        if (p.index < h.count) elf::section(d, h, p.index, s);
        if (s.size != p.size || elf::group(d, h, p.index) != p.group) return changed(st, o);
        st.section_map[p.index] = U16(o.first + k);
        if (p.group == elf::bss) continue;
        Byte* out = st.module + (p.address - st.module_base);
        XYF_WORK(s.size);
        for (U32 i = 0; i != s.size; ++i) out[i] = d[s.offset + i];
    }
    bool sdk = !o.owner && !o.extension;
    for (U32 t = 0; t != h.count; ++t) {
        elf::Section table;
        elf::section(d, h, t, table);
        if (table.type != 4) continue;
        if (st.section_map[table.info] == no_place) return changed(st, o);
        U32 base = st.placed[st.section_map[table.info]].address, count = elf::entries(table);
        for (U32 e = 0; e != count; ++e) {
            elf::Relocation r;
            elf::relocation(d, table, e, r);
            elf::Symbol sym;
            elf::symbol(d, h, r.symbol, sym);
            const Byte* text;
            U32 length;
            elf::symbol_name(d, h, sym, text, length);
            U32 value;
            if (sym.section == elf::shn_abs) value = sym.value;
            else if (sym.section) { // definedValue
                elf::Section sec;
                elf::section(d, h, sym.section, sec);
                U32 p = st.section_map[sym.section];
                if (p == no_place || sym.size > sec.size || sym.value > sec.size - sym.size)
                    return refuse(st, Status::symbol_outside, text, length);
                value = st.placed[p].address + sym.value;
            } else {
                U32 n = lookup(st, text, length);
                if (n != none && (st.names[n].flags & f_global) && !sdk && st.names[n].owner &&
                    st.names[n].owner != package_of(st, o))
                    return refuse(st, Status::foreign_import, text, length); // device rule: another package's symbol
                if (n != none && (st.names[n].flags & f_global)) value = st.names[n].value;
                else if (n != none && sdk && (st.names[n].flags & f_alias)) value = st.names[n].alias;
                else return refuse(st, Status::unresolved, text, length);
            }
            if (r.addend < 0 ? value < 0u - U32(r.addend) : value > 0xffffffffu - U32(r.addend))
                return Status::overflow;
            value += U32(r.addend);
            U32 address = base + r.offset;
            Byte* field = st.module + (address - st.module_base);
            if (r.type == 18) {
                field[0] = Byte(value);
                field[1] = Byte(value >> 8);
                field[2] = Byte(value >> 16);
                field[3] = Byte(value >> 24);
            } else if (r.type == 6 || r.type == 7) {
                U32 half = r.type == 6 ? value : value >> 16;
                field[0] = Byte(half);
                field[1] = Byte(half >> 8);
            } else {
                U32 opcode = elf::u16(field - 2), from = address - 2;
                bool forward = value >= from;
                U32 distance = forward ? value - from : from - value;
                if ((opcode & 0xff00u) != (r.type == 10 ? 0xe300u : 0xe200u) || (distance & 1u) ||
                    (forward ? distance >= (1u << 24) : distance > (1u << 24))) {
                    st.detail.add_unsigned(e); // the relocation's index in its table
                    return Status::branch;
                }
                U32 encoded = (forward ? distance >> 1 : 0u - (distance >> 1)) & 0xffffffu;
                U32 high = (opcode & 0xff00u) | (encoded >> 16);
                field[-2] = Byte(high);
                field[-1] = Byte(high >> 8);
                field[0] = Byte(encoded);
                field[1] = Byte(encoded >> 8);
            }
        }
    }
    return Status::ok;
}

// Scratch layout: State, then each table, every part 4-byte aligned.
struct Layout {
    U32 elf, names, heads, text, objects, placed, records, linked, map, machines, absent, fallbacks, numbers,
        numbers_work, numbering, total, head_count;
};
bool plan(const Limits& l, Layout& p) {
    if (!l.objects || l.objects > 16384 || !l.sections || l.sections > 65000 || !l.names || l.names > 65000 ||
        !l.records || l.records > 65535 || l.text_bytes < 1024 || l.text_bytes > 16u << 20 || !l.machines ||
        l.machines > 1024 || l.absent > 65536 || l.fallbacks > 65536 || l.numbers < l.machines + max_fixed ||
        l.numbers > 4096)
        return false;
    U32 heads = 1;
    while (heads < l.names) heads <<= 1;
    p.head_count = heads;
    U32 at = align4(sizeof(State));
    p.elf = at;
    at += align4(elf::work_bytes(elf::max_bytes));
    p.names = at;
    at += l.names * sizeof(Name);
    p.heads = at;
    at += align4(heads * 2);
    p.text = at;
    at += align4(l.text_bytes);
    p.objects = at;
    at += l.objects * sizeof(Object);
    p.placed = at;
    at += l.sections * sizeof(Placed);
    p.records = at;
    at += l.records * sizeof(Record);
    p.linked = at;
    at += l.objects * 8;
    p.map = at;
    at += elf::max_sections * 2;
    p.machines = at;
    at += l.machines * sizeof(Machine);
    p.absent = at;
    at += l.absent * sizeof(Alias);
    p.fallbacks = at;
    at += l.fallbacks * sizeof(Alias);
    p.numbers = at;
    at += l.numbers * sizeof(Number);
    p.numbers_work = at;
    at += align4(numbers::work_bytes(l.numbers));
    p.numbering = at;
    at += l.machines * sizeof(numbers::Machine);
    p.total = at;
    return true;
}
State* state_of(const Workspace& w) {
    if (!w.scratch || w.capacity < sizeof(State) || (reinterpret_cast<unsigned long>(w.scratch) & 3u)) return 0;
    return reinterpret_cast<State*>(w.scratch);
}
}

void Text::add(const Byte* text, U32 count) {
    for (U32 i = 0; i != count && length + 1 < capacity; ++i) buffer[length++] = char(text[i]);
    if (capacity) buffer[length] = 0;
}
void Text::add(const char* text) { add(bytes(text), length_of(text, 0xffff)); }
void Text::add_unsigned(U32 value) {
    static const U32 steps[10] = {1000000000u, 100000000u, 10000000u, 1000000u, 100000u, 10000u, 1000u, 100u, 10u, 1u};
    bool started = false;
    for (U32 s = 0; s != 10; ++s) {
        Byte digit = '0';
        while (value >= steps[s] && digit < '9') {
            value -= steps[s];
            ++digit;
        }
        if (digit != '0' || started || s == 9) {
            add(&digit, 1);
            started = true;
        }
    }
}
void Text::add_number(int value) {
    if (value < 0) add("-");
    add_unsigned(value < 0 ? 0u - U32(value) : U32(value));
}

U32 scratch_bytes(const Limits& limits) {
    Layout p;
    return plan(limits, p) ? p.total : 0;
}

Status begin(const Selection& sel, const Limits& limits, Workspace& workspace, Byte* module, U32 module_capacity,
             Result& result) {
    Byte* r = reinterpret_cast<Byte*>(&result);
    for (U32 i = 0; i != sizeof(Result); ++i) r[i] = 0;
    result.status = Status::argument;
    Layout p;
    State* st = state_of(workspace);
    if (!st || !plan(limits, p) || workspace.capacity < p.total || !module || (sel.machine_count && !sel.machines) ||
        (sel.extension_count && !sel.extensions) || (sel.has_layout && sel.layout_count && !sel.layout) ||
        sel.machine_count > limits.machines || sel.extension_count > max_extensions ||
        sel.layout_count > limits.numbers - max_fixed - limits.machines)
        return Status::argument;
    for (U32 i = 0; i != sel.machine_count + sel.extension_count; ++i)
        if (!(i < sel.machine_count ? sel.machines[i] : sel.extensions[i - sel.machine_count])) return Status::argument;
    Byte* base = workspace.scratch;
    for (U32 i = 0; i != p.total; ++i) base[i] = 0; // no table of an earlier link survives
    st->limits = limits;
    st->selection = &sel;
    st->result = &result;
    st->module = module;
    st->module_capacity = module_capacity;
    st->elf_work = base + p.elf;
    st->elf_work_bytes = elf::work_bytes(elf::max_bytes);
    st->names = reinterpret_cast<Name*>(base + p.names);
    st->heads = reinterpret_cast<U16*>(base + p.heads);
    st->head_mask = p.head_count - 1;
    for (U32 i = 0; i != p.head_count; ++i) st->heads[i] = no_place;
    st->text = reinterpret_cast<char*>(base + p.text);
    st->objects = reinterpret_cast<Object*>(base + p.objects);
    st->placed = reinterpret_cast<Placed*>(base + p.placed);
    st->records = reinterpret_cast<Record*>(base + p.records);
    st->linked = reinterpret_cast<U32*>(base + p.linked);
    st->sorted = st->linked + limits.objects;
    st->section_map = reinterpret_cast<U16*>(base + p.map);
    st->machines = reinterpret_cast<Machine*>(base + p.machines);
    st->absent = reinterpret_cast<Alias*>(base + p.absent);
    st->fallbacks = reinterpret_cast<Alias*>(base + p.fallbacks);
    st->numbers = reinterpret_cast<Number*>(base + p.numbers);
    st->numbers_work = base + p.numbers_work;
    st->numbering = reinterpret_cast<numbers::Machine*>(base + p.numbering);
    st->detail.buffer = result.detail;
    st->detail.capacity = detail_bytes;
    st->detail.length = 0;
    st->phase = ph_base;
    // modkit rebuild_kit's first checks: each id once, at least one machine.
    Status status = Status::ok;
    for (U32 i = 0; i != sel.machine_count && status == Status::ok; ++i)
        for (U32 k = i + 1; k != sel.machine_count; ++k)
            if (same_cstr(sel.machines[i], sel.machines[k], 64))
                status = refuse(*st, Status::duplicate_selection, sel.machines[i]);
    for (U32 i = 0; i != sel.extension_count && status == Status::ok; ++i)
        for (U32 k = i + 1; k != sel.extension_count; ++k)
            if (same_cstr(sel.extensions[i], sel.extensions[k], 64))
                status = refuse(*st, Status::duplicate_selection, sel.extensions[i]);
    if (status == Status::ok && !sel.machine_count) status = Status::no_machine;
    if (status != Status::ok) st->phase = ph_done;
    result.status = status;
    return status;
}

bool next(const Workspace& workspace, Request& request) {
    State* st = state_of(workspace);
    if (!st || st->phase >= ph_done) return false;
    if (st->phase == ph_base) {
        set_package(*st, 0);
        set_path(*st, base_paths[st->index], length_of(base_paths[st->index], 32));
    } else if (st->phase == ph_package) {
        set_package(*st, chosen_package(*st, st->index >> 1));
        set_path(*st, st->index & 1 ? "objects.txt" : "info.txt", st->index & 1 ? 11 : 8);
    } else if (st->phase == ph_zero) {
        st->package[0] = st->path[0] = 0; // no file: step(workspace, 0, 0, result)
    } else {
        const Object& o = st->objects[st->phase == ph_pass1 ? st->index : st->linked[st->index]];
        set_package(*st, o.owner ? o.owner : o.extension ? st->machine_count + o.extension : 0);
        set_path(*st, st->text + o.path, o.path_length);
    }
    request.package = st->package;
    request.path = st->path;
    return true;
}

Status step(Workspace& workspace, const Byte* data, U32 size, Result& result) {
    State* st = state_of(workspace);
    if (!st || st->phase >= ph_done || st->result != &result || (!data && size)) return Status::sequence;
    Status status = Status::ok;
    ++st->steps;
    result.step = st->steps;
    Cursor c = {data, size, 0};
    st->line_number = 0;
    Request request;
    next(workspace, request); // st->package and st->path name this file
    if (st->phase == ph_base || st->phase == ph_package) { // device rule: no NUL byte in a text table
        for (U32 i = 0; i != size; ++i)
            if (!data[i]) {
                status = refuse(*st, Status::text_nul, bytes(st->path), length_of(st->path, max_path));
                break;
            }
    }
    if (status != Status::ok) {
    } else if (st->phase == ph_base) {
        status = parse_base(*st, st->index, c);
        if (status == Status::ok && ++st->index >= base_files) {
            st->phase = ph_package;
            st->index = 0;
        }
    } else if (st->phase == ph_package) {
        status = parse_package(*st, chosen_package(*st, st->index >> 1), st->index & 1, c);
        if (status == Status::ok && ++st->index >= 2 * st->packages) {
            status = prepare(*st);
            st->phase = ph_pass1;
            st->index = 0;
        }
    } else if (st->phase == ph_pass1) {
        status = pass1(*st, st->index++, data, size);
    } else if (st->phase == ph_zero) { // the module's initialized bytes, zero_slice per step
        if (size) status = Status::sequence;
        else {
            U32 end =
                result.sizes.initialized - st->zeroed > zero_slice ? st->zeroed + zero_slice : result.sizes.initialized;
            for (U32 i = st->zeroed; i != end; ++i) st->module[i] = 0;
            st->zeroed = end;
            if (end == result.sizes.initialized) st->phase = ph_pass2;
        }
    } else {
        status = pass2(*st, st->linked[st->index], data, size);
        if (status == Status::ok && ++st->index >= st->linked_count) {
            st->phase = ph_done;
            result.services = st->active;
            for (U32 i = 0; i != st->extension_count; ++i)
                if (st->extensions[i].chosen) result.extensions |= 1u << i;
            result.objects = st->linked_count;
            result.numbers = st->number_count;
            for (U32 i = 0; i != st->name_count; ++i) {
                result.globals += (st->names[i].flags & f_global) != 0;
                result.aliases += (st->names[i].flags & f_alias) != 0;
            }
        }
    }
    if (status == Status::ok && st->phase == ph_pass1 && st->index >= st->object_count) { // also no objects at all
        status = link_tables(*st, result.sizes); // linked_count 0: empty, so pass 2 has an object
        st->phase = ph_zero;
        st->index = 0;
    }
    result.module_base = st->module_base;
    result.limit = st->module_bytes;
    if (status != Status::ok) {
        st->phase = ph_done;
        Byte* sizes = reinterpret_cast<Byte*>(&result.sizes);
        for (U32 i = 0; i != sizeof(Sizes); ++i) sizes[i] = 0;
    }
    result.status = status;
    return status;
}

bool global(const Workspace& workspace, const char* name, U32& value) {
    State* st = state_of(workspace);
    if (!st || !st->names || !name) return false;
    U32 index = lookup(*st, bytes(name), length_of(name, 0xffff));
    if (index == none || !(st->names[index].flags & f_global)) return false;
    value = st->names[index].value;
    return true;
}
bool name_at(const Workspace& workspace, U32 index, const char*& name, U32& length, U32& value, U32& kind) {
    State* st = state_of(workspace);
    if (!st || !st->names || index >= st->name_count) return false;
    const Name& n = st->names[index];
    name = st->text + n.text;
    length = n.length;
    kind = (n.flags & f_global) ? ((n.flags & f_definition) ? 2 : 1) : (n.flags & f_alias) ? 3 : 0;
    value = kind == 3 ? n.alias : n.value;
    return true;
}
bool object_at(const Workspace& workspace, U32 index, const char*& name, U32& length) {
    State* st = state_of(workspace);
    if (!st || !st->objects || index >= st->linked_count) return false;
    const Object& o = st->objects[st->linked[index]];
    name = st->text + o.path + 8;
    length = o.path_length - 8u;
    return true;
}
bool number_at(const Workspace& workspace, U32 index, Number& number) {
    State* st = state_of(workspace);
    if (!st || !st->numbers || index >= st->number_count) return false;
    number = st->numbers[index];
    return true;
}
bool extension_at(const Workspace& workspace, U32 index, const char*& name, U32& length) {
    State* st = state_of(workspace);
    if (!st || index >= st->extension_count) return false;
    name = st->extensions[index].id;
    length = length_of(name, 33);
    return true;
}
bool service_at(const Workspace& workspace, U32 index, const char*& name, U32& length) {
    State* st = state_of(workspace);
    if (!st || index >= st->service_count) return false;
    name = st->text + st->services[index].name;
    length = st->services[index].length;
    return true;
}
void usage(const Workspace& workspace, Usage& out) {
    State* st = state_of(workspace);
    if (!st) return;
    out.objects = st->object_count;
    out.sections = st->placed_count;
    out.names = st->name_count;
    out.records = st->record_count;
    out.text_bytes = st->text_used;
    out.absent = st->absent_count;
    out.fallbacks = st->fallback_count;
    out.numbers = st->number_count;
}
}}

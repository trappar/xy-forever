// Mod assembly, content step: a port of open-xy builder/core.mjs buildAssets.
#include "mod_content.hpp"
namespace xyf_core { namespace mod_content {
namespace {
enum Phase { packages_phase, finish_phase, done, failed };
const U32 none = 0xffffffffu, max_depth = 32;
const char catalog_path[] = "content/op1_factory.json";
const char* const group_keys[3] = {"engine_types", "fx_types", "seq_types"};

__attribute__((noinline)) U32 length(const char* s, U32 limit) { U32 n = 0; while (n != limit && s[n]) ++n; return n; }
__attribute__((noinline)) bool starts(const char* s, const char* prefix) {
    for (U32 i = 0; prefix[i]; ++i) if (s[i] != prefix[i]) return false;
    return true;
}
__attribute__((noinline)) int compare(const char* a, const char* b) {
    for (U32 i = 0; i <= max_path; ++i) {
        const Byte x = Byte(a[i]), y = Byte(b[i]);
        if (x != y) return x < y ? -1 : 1;
        if (!x) return 0;
    }
    return 0;
}
bool same(const char* a, const char* b) { return !compare(a, b); }
Status refuse(Builder& b, Status status, const char* what, const char* name = "") {
    U32 n = 0;
    for (U32 i = 0; what[i] && n < detail_bytes - 1; ++i) b.detail[n++] = what[i];
    if (name[0] && n < detail_bytes - 1) b.detail[n++] = ' ';
    for (U32 i = 0; name[i] && n < detail_bytes - 1; ++i) b.detail[n++] = name[i];
    b.detail[n] = 0;
    b.phase = failed;
    b.failure = status;
    return status;
}
__attribute__((noinline)) bool lower_or_digit(char c) { return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9'); }
bool valid_id(const char* id, U32 max) {   // [a-z][a-z0-9_]{0,max-1}
    if (!id || !(id[0] >= 'a' && id[0] <= 'z')) return false;
    U32 n = 1;
    for (; n <= max && id[n]; ++n) if (!lower_or_digit(id[n]) && id[n] != '_') return false;
    return n <= max;
}
bool valid_asset(const char* a) {          // [a-z][a-z0-9_]*\.svg
    const U32 n = length(a, 65);
    if (n < 5 || n > 64 || !(a[0] >= 'a' && a[0] <= 'z') || !same(a + n - 4, ".svg")) return false;
    for (U32 i = 1; i + 4 < n; ++i) if (!lower_or_digit(a[i]) && a[i] != '_') return false;
    return true;
}
bool preset_name(const char* s, const char* extra, U32 max) {   // [a-z0-9][a-z0-9 extra]{0,max-1}\.json
    const U32 n = length(s, max + 6);
    if (n < 6 || n > max + 5 || !same(s + n - 5, ".json") || !lower_or_digit(s[0])) return false;
    for (U32 i = 1; i + 5 < n; ++i) {
        bool ok = lower_or_digit(s[i]);
        for (U32 k = 0; extra[k]; ++k) ok = ok || s[i] == extra[k];
        if (!ok) return false;
    }
    return true;
}
const char* after_folder(const char* s, const char* folder) {   // "<folder>/rest" -> rest
    const U32 n = length(folder, 64);
    for (U32 i = 0; i != n; ++i) if (s[i] != folder[i]) return 0;
    return s[n] == '/' ? s + n + 1 : 0;
}
bool list_path_ok(const char* p) {         // docs/storage/packages.md files.sha256 path rules
    const U32 n = length(p, max_path + 1);
    if (!n || n > max_path || p[n - 1] == '/' || p[n - 1] == '.' || p[n - 1] == ' ') return false;
    const char* word = "bootloader";      // no name containing it (case ignored)
    for (U32 i = 0; i != n; ++i) {
        const char c = p[i];
        if (c < 0x20 || c > 0x7e || c == '\\' || c == ':') return false;
        if (c == '/' && (!i || p[i - 1] == '/' || p[i - 1] == '.' || p[i - 1] == ' ')) return false;
        U32 k = 0;
        while (k != 10 && i + k < n && (p[i + k] | 0x20) == word[k]) ++k;
        if (k == 10) return false;
    }
    return true;
}
bool object_path(const char* p) {          // objects/[A-Za-z0-9_./-]+\.o, no ".."
    const U32 n = length(p, max_path + 1);
    if (!starts(p, "objects/") || n < 11 || n > max_path || !same(p + n - 2, ".o")) return false;
    for (U32 i = 8; i != n; ++i) {
        const char c = p[i];
        if (!lower_or_digit(c) && !(c >= 'A' && c <= 'Z') && c != '_' && c != '.' && c != '/' && c != '-') return false;
        if (c == '.' && p[i - 1] == '.') return false;
    }
    return true;
}
const File* find(const Package& p, const char* path) {
    for (U32 i = 0; i != p.file_count; ++i) if (p.files[i].path && same(p.files[i].path, path)) return &p.files[i];
    return 0;
}
__attribute__((noinline)) bool is_machine(Kind k) { return k != Kind::extension; }
bool instrument(Kind k) { return k == Kind::synth || k == Kind::drum || k == Kind::sample; }
// What a package may carry (modkit MOD_FILES, narrowed to what buildAssets reads per kind).
bool allowed(const Package& p, const char* path, bool listed) {
    if (same(path, "info.txt") || same(path, "objects.txt") || same(path, "NOTICE.txt") || object_path(path)) return true;
    if (!is_machine(p.kind)) {
        if (same(path, "extension.json")) return true;
    } else if (same(path, "machine.json") || same(path, "catalog.txt")) return true;
    if (starts(path, "content/display/")) return p.asset && same(path + 16, p.asset);
    if (!is_machine(p.kind)) return false;
    const char* rest;
    if (starts(path, "content/presets/") && (rest = after_folder(path + 16, p.id)))
        return instrument(p.kind) && listed && preset_name(rest, "_-", 24);
    if (starts(path, "content/aux_presets/") &&
        ((rest = after_folder(path + 20, p.id)) || (rest = after_folder(path + 20, "cv"))))
        return !listed && preset_name(rest, " _-", 15);
    return false;
}

__attribute__((noinline)) U32 le16(const Byte* p) { return U32(p[0]) | U32(p[1]) << 8; }
U32 le32(const Byte* p) { return le16(p) | le16(p + 2) << 16; }

// ------------------------------------------------------------------- JSON (RFC 8259), bounded, iterative
bool ws(Byte c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r'; }
bool digit(Byte c) { return c >= '0' && c <= '9'; }
bool hexdigit(Byte c) { return digit(c) || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'); }
U32 string_end(const Byte* p, U32 n, U32 i) {   // the closing quote of the string at i, or none
    for (U32 j = i + 1; j < n;) {
        const Byte c = p[j];
        if (c == '"') return j;
        if (c < 0x20) return none;
        if (c == '\\') {
            if (j + 1 >= n) return none;
            const Byte e = p[j + 1];
            if (e == 'u') {
                if (n - j < 6) return none;
                for (U32 k = 2; k != 6; ++k) if (!hexdigit(p[j + k])) return none;
                j += 6;
            } else if (e == '"' || e == '\\' || e == '/' || e == 'b' || e == 'f' || e == 'n' || e == 'r' || e == 't') {
                j += 2;
            } else return none;
            continue;
        }
        if (c < 0x80) { ++j; continue; }
        U32 more; Byte low = 0x80, high = 0xbf;   // UTF-8 without overlongs or surrogates
        if (c >= 0xc2 && c <= 0xdf) more = 1;
        else if (c == 0xe0) { more = 2; low = 0xa0; }
        else if ((c >= 0xe1 && c <= 0xec) || c == 0xee || c == 0xef) more = 2;
        else if (c == 0xed) { more = 2; high = 0x9f; }
        else if (c == 0xf0) { more = 3; low = 0x90; }
        else if (c >= 0xf1 && c <= 0xf3) more = 3;
        else if (c == 0xf4) { more = 3; high = 0x8f; }
        else return none;
        if (n - j <= more) return none;
        for (U32 k = 1; k <= more; ++k) {
            const Byte x = p[j + k];
            if (x < (k == 1 ? low : 0x80) || x > (k == 1 ? high : 0xbf)) return none;
        }
        j += more + 1;
    }
    return none;
}
U32 number_end(const Byte* p, U32 n, U32 i) {   // the last byte of the number at i, or none
    U32 j = i;
    if (p[j] == '-') ++j;
    if (j >= n || !digit(p[j])) return none;
    if (p[j] == '0') ++j;
    else while (j < n && digit(p[j])) ++j;
    if (j < n && p[j] == '.') {
        if (++j >= n || !digit(p[j])) return none;
        while (j < n && digit(p[j])) ++j;
    }
    if (j < n && (p[j] == 'e' || p[j] == 'E')) {
        if (++j < n && (p[j] == '+' || p[j] == '-')) ++j;
        if (j >= n || !digit(p[j])) return none;
        while (j < n && digit(p[j])) ++j;
    }
    return j - 1;
}
U32 literal_end(const Byte* p, U32 n, U32 i) {
    const char* const words[3] = {"true", "false", "null"};
    for (U32 w = 0; w != 3; ++w) {
        const U32 len = length(words[w], 8);
        bool match = n - i >= len;
        for (U32 k = 0; match && k != len; ++k) match = p[i + k] == Byte(words[w][k]);
        if (match) return i + len - 1;
    }
    return none;
}
// Where a top-level key's array closes, and the last byte before that.
struct ArrayEnd { U32 close, last, found; };
// p[0..n) holds exactly one JSON value, at most `levels` deep. For each top-level object key keys[k]
// whose value is an array, ends[k] says where the array closes. The top-level key whose string starts
// at `mark`: `marked` = the last byte of its value when that is a number, else none.
bool json(const Byte* p, U32 n, const char* const* keys, U32 key_count, ArrayEnd* ends, U32 levels, U32 mark,
          U32& marked) {
    enum Want { value, value_or_close, key_or_close, key, colon, comma_or_close };
    Byte stack[max_depth];
    U32 depth = 0, previous = none;
    int current = -1, array = -1;
    Want want = value;
    bool complete = false, marking = false;
    marked = none;
    for (U32 k = 0; k != key_count; ++k) { ends[k].close = ends[k].last = none; ends[k].found = 0; }
    for (U32 i = 0; i < n;) {
        const Byte c = p[i];
        if (ws(c)) { ++i; continue; }
        if (complete) return false;
        bool closing = false;
        U32 end = i;
        if (want == key_or_close && c == '}') closing = true;
        else if (want == value_or_close && c == ']') closing = true;
        else if (want == comma_or_close) {
            if (c == ',') { want = stack[depth - 1] == '{' ? key : value; previous = i++; continue; }
            if (c != (stack[depth - 1] == '{' ? '}' : ']')) return false;
            closing = true;
        } else if (want == key || want == key_or_close) {
            if (c != '"' || (end = string_end(p, n, i)) == none) return false;
            if (depth == 1) {
                marking = i == mark;
                current = -1;
                for (U32 k = 0; k != key_count; ++k) {
                    const U32 len = length(keys[k], 32);
                    bool match = end - i - 1 == len;
                    for (U32 x = 0; match && x != len; ++x) match = p[i + 1 + x] == Byte(keys[k][x]);
                    if (match) current = int(k);
                }
            }
            want = colon; previous = end; i = end + 1;
            continue;
        } else if (want == colon) {
            if (c != ':') return false;
            want = value; previous = i++;
            continue;
        } else if (c == '{' || c == '[') {
            if (depth == levels) return false;   // levels <= max_depth
            marking = false;
            stack[depth++] = c;
            if (c == '[' && depth == 2 && stack[0] == '{') array = current;
            want = c == '{' ? key_or_close : value_or_close;
            previous = i++;
            continue;
        } else if (c == '"') end = string_end(p, n, i);
        else if (c == '-' || digit(c)) end = number_end(p, n, i);
        else if (c == 't' || c == 'f' || c == 'n') end = literal_end(p, n, i);
        else return false;
        if (marking && !closing && c != '"' && c != 't' && c != 'f' && c != 'n') marked = end;   // a number
        marking = false;
        if (closing) {
            if (c == ']' && depth == 2 && stack[0] == '{' && array >= 0) {
                ends[array].close = i; ends[array].last = previous; ++ends[array].found;
            }
            if (--depth == 1) array = -1;
        } else if (end == none) return false;
        previous = end; i = end + 1;
        if (depth == 0) complete = true;
        else want = comma_or_close;
    }
    return complete;
}
// Whitespace, ",", one JSON object (catalogEntry's text); `id_end`: the last byte of the number of the
// object's own "id" key when its key string starts at `id_at`, else none.
bool entry_ok(const Byte* p, U32 n, U32 id_at, U32& id_end) {
    U32 i = 0;
    id_end = none;
    while (i < n && ws(p[i])) ++i;
    if (i >= n || p[i] != ',') return false;
    U32 j = ++i;
    while (j < n && ws(p[j])) ++j;
    U32 marked;
    if (j >= n || p[j] != '{' || !json(p + i, n - i, 0, 0, 0, max_entry_depth, id_at - i, marked)) return false;
    if (marked != none && id_at > i) id_end = marked + i;
    return true;
}
bool regex_space(Byte c) { return c == ' ' || (c >= '\t' && c <= '\r'); }
U32 id_match(const Byte* p, U32 n, U32 i) {   // /"id":\s*\d+/ at i: the end of its digits, or none
    const char* key = "\"id\":";
    if (n - i < 6) return none;
    for (U32 k = 0; k != 5; ++k) if (p[i + k] != Byte(key[k])) return none;
    U32 j = i + 5;
    while (j < n && regex_space(p[j])) ++j;
    if (j >= n || !digit(p[j])) return none;
    while (j < n && digit(p[j])) ++j;
    return j;
}
__attribute__((noinline)) U32 decimal(U32 v, Byte* out) {            // digits by subtraction: no division helper
    static const U32 powers[10] = {1000000000u, 100000000u, 10000000u, 1000000u, 100000u, 10000u, 1000u, 100u, 10u, 1u};
    U32 n = 0;
    for (U32 k = 0; k != 10; ++k) {
        Byte d = 0;
        while (v >= powers[k]) { v -= powers[k]; ++d; }
        if (d || n || k == 9) out[n++] = Byte('0' + d);
    }
    return n;
}
// linked.numbers[id]: an effect's number, a player's number + 4.
bool entry_number(const Builder& b, const Package& p, U32& number) {
    mod_link::Number n;
    for (U32 i = 0; mod_link::number_at(*b.link, i, n); ++i) {
        if (compare(n.id, p.id)) continue;
        const mod_link::Identity& id = n.identity;
        if (!id.has_number || id.number < 0) return false;
        if (p.kind == Kind::effect && id.slot == mod_link::slot_effect) { number = U32(id.number); return true; }
        if (p.kind == Kind::player && id.slot == mod_link::slot_player && U32(id.number) <= 0xfffffffbu) {
            number = U32(id.number) + 4; return true;
        }
        return false;
    }
    return false;
}
// Whether the link selected machine `p` (the selection the link began with) and numbered it in its slot.
bool linked_machine(const Builder& b, const Package& p) {
    bool selected = false;
    for (U32 s = 0; s != b.selection->machine_count && !selected; ++s)
        selected = b.selection->machines[s] && same(b.selection->machines[s], p.id);
    const U32 slot = instrument(p.kind) ? U32(mod_link::slot_engine) :
                     p.kind == Kind::effect ? U32(mod_link::slot_effect) : U32(mod_link::slot_player);
    mod_link::Number n;
    for (U32 i = 0; selected && mod_link::number_at(*b.link, i, n); ++i)
        if (!compare(n.id, p.id)) return n.identity.slot == slot;
    return false;
}
// Bytewise order of two unterminated strings (a prefix first).
__attribute__((noinline)) int order(const Byte* a, U32 an, const Byte* c, U32 cn) {
    for (U32 i = 0; i != an && i != cn; ++i) if (a[i] != c[i]) return a[i] < c[i] ? -1 : 1;
    return an == cn ? 0 : an < cn ? -1 : 1;
}
// Whether extensions.txt's chosen rows (linked.extensions) hold `id`; `count` = how many are chosen.
bool extension_chosen(const Builder& b, const char* id, U32& count) {
    bool chosen = false;
    const char* name; U32 length;
    count = 0;
    for (U32 i = 0; i != 32 && mod_link::extension_at(*b.link, i, name, length); ++i)
        if ((b.linked->extensions >> i) & 1u) { ++count; chosen = chosen || same(name, id); }
    return chosen;
}
__attribute__((noinline)) void swap(Listed& a, Listed& c) {
    const char* path = a.path; const U32 package = a.package; const hash::Digest* sha256 = a.sha256;
    a.path = c.path; a.package = c.package; a.sha256 = c.sha256;
    c.path = path; c.package = package; c.sha256 = sha256;
}
void sift(Listed* l, U32 root, U32 n) {
    for (U32 guard = 0; guard != 32; ++guard) {
        U32 child = 2 * root + 1;
        if (child >= n) return;
        if (child + 1 < n && compare(l[child].path, l[child + 1].path) < 0) ++child;
        if (compare(l[root].path, l[child].path) >= 0) return;
        swap(l[root], l[child]);
        root = child;
    }
}
struct Out {
    Byte* at; U32 capacity, size;
    __attribute__((noinline)) bool put(const Byte* p, U32 n) {
        if (n > capacity - size) return false;
        for (U32 i = 0; i != n; ++i) at[size + i] = p[i];
        size += n;
        return true;
    }
};
const char hex_digits[] = "0123456789abcdef";

// One package: policy, ids, kind rules, its catalog entry, the files it adds.
__attribute__((noinline)) Status add_package(Builder& b, U32 index) {
    const Package& p = b.packages[index];
    const bool machine = is_machine(p.kind);
    if (!p.id || U32(p.kind) > U32(Kind::extension) || !valid_id(p.id, machine ? 15 : 32) ||
        (p.asset && !valid_asset(p.asset)) || (p.file_count && !p.files) || p.file_count > 4096)
        return refuse(b, Status::package, "package", p.id && valid_id(p.id, 32) ? p.id : "");
    const File* entry = find(p, "catalog.txt");
    const bool listed = machine && entry;
    for (U32 i = 0; i != p.file_count; ++i) {
        const char* path = p.files[i].path;
        if (!path || !list_path_ok(path) || (i && compare(p.files[i - 1].path, path) >= 0) || !allowed(p, path, listed))
            return refuse(b, Status::package, "file", path && list_path_ok(path) ? path : p.id);
    }
    for (U32 j = 0; j != index; ++j)
        if (same(b.packages[j].id, p.id) || (is_machine(b.packages[j].kind) == machine && b.packages[j].order == p.order))
            return refuse(b, Status::duplicate, "package", p.id);
    if (!machine) {
        U32 chosen_count;
        if (!extension_chosen(b, p.id, chosen_count) || p.numbered || p.catalog_text)
            return refuse(b, Status::package, "extension", p.id);
        ++b.extension_count;
    } else {
        if (b.machine_count == b.limit_packages) return refuse(b, Status::capacity, "packages");
        if (!linked_machine(b, p)) return refuse(b, Status::unlinked, "machine", p.id);
        const bool numbering = p.kind == Kind::effect || p.kind == Kind::player;
        if (p.numbered != (listed && numbering)) return refuse(b, Status::number, "machine", p.id);
        U32 written = 0;
        if (listed) {   // catalog.txt: hash, JSON, number position (the first /"id":\s*\d+/)
            const Byte* t = p.catalog_text;
            const U32 n = p.catalog_bytes;
            if (!t || !n || n > max_entry) return refuse(b, Status::entry, "catalog.txt", p.id);
            hash::Sha256 h;
            hash::Digest d;
            h.update(t, n); h.finish(d);
            if (!hash::equal(d, entry->sha256)) return refuse(b, Status::package, "catalog.txt sha256", p.id);
            U32 id_end;
            if (!entry_ok(t, n, p.numbered ? p.number_start : none, id_end))
                return refuse(b, Status::entry, "catalog.txt", p.id);
            written = n;
            if (p.numbered) {
                U32 number = 0;
                Byte digits[10];
                if (p.number_start >= p.number_end || p.number_end > n || id_match(t, n, p.number_start) != p.number_end ||
                    id_end != p.number_end - 1) return refuse(b, Status::number, "position", p.id);
                for (U32 q = 0; q != p.number_start; ++q)
                    if (id_match(t, n, q) != none) return refuse(b, Status::number, "position", p.id);
                if (!entry_number(b, p, number)) return refuse(b, Status::number, "no number", p.id);
                written = n - (p.number_end - p.number_start) + 6 + decimal(number, digits);
            }
        } else if (p.catalog_text) return refuse(b, Status::package, "catalog.txt", p.id);
        b.entry_bytes[index] = written;
        U32 at = b.machine_count++;   // insertion sort by collection order
        while (at && b.packages[b.machines[at - 1]].order > p.order) { b.machines[at] = b.machines[at - 1]; --at; }
        b.machines[at] = (unsigned short)index;
    }
    U32 presets = 0;
    bool display = false;
    for (U32 i = 0; i != p.file_count; ++i) {
        const char* path = p.files[i].path;
        const bool art = starts(path, "content/display/");
        const bool aux = starts(path, "content/aux_presets/");
        if (!art && !aux && !starts(path, "content/presets/")) continue;
        if (art) display = true;
        else ++presets;
        if (aux) {
            const char* folder = path + 20;
            bool seen = false;
            for (U32 k = 0; k != b.aux_folders; ++k) {
                const char* a = b.folders[k];
                U32 x = 0;
                while (x < 64 && a[x] == folder[x] && a[x] != '/' && a[x]) ++x;
                seen = seen || (a[x] == '/' && folder[x] == '/');
            }
            if (!seen) {
                if (b.aux_folders == 16) return refuse(b, Status::capacity, "aux preset folders");
                b.folders[b.aux_folders++] = folder;
            }
        }
        if (b.listed_count == b.limit_files) return refuse(b, Status::capacity, "files");
        Listed& l = b.listed[b.listed_count++];
        l.path = path; l.package = index; l.sha256 = &p.files[i].sha256;
    }
    if (p.asset && !display) return refuse(b, Status::missing, "display", p.asset);
    if (machine && presets != p.presets) return refuse(b, Status::missing, "presets", p.id);
    return Status::ok;
}

// The listed files sorted by path; one aux preset path of two machines kept once (the later machine in
// collection order: core.mjs's files[path] keeps the last write); any other repeated path refused.
Status sort_listed(Builder& b) {
    const U32 n = b.listed_count;
    Listed* l = b.listed;
    for (U32 i = n / 2; i;) sift(l, --i, n);
    for (U32 end = n; end > 1;) {
        --end;
        swap(l[0], l[end]);
        sift(l, 0, end);
    }
    U32 kept = 0;
    b.aux = 0;
    for (U32 i = 0; i != n; ++i) {
        if (kept && same(l[kept - 1].path, l[i].path)) {
            if (!starts(l[i].path, "content/aux_presets/")) return refuse(b, Status::duplicate, "file", l[i].path);
            if (b.packages[l[i].package].order > b.packages[l[kept - 1].package].order) swap(l[kept - 1], l[i]);
            continue;
        }
        if (kept != i) swap(l[kept], l[i]);
        b.aux += starts(l[kept].path, "content/aux_presets/");
        ++kept;
    }
    b.listed_count = kept;
    return Status::ok;
}

// The bytes of `prefix` when path[0, n) starts with it, else 0.
__attribute__((noinline)) U32 prefix_of(const Byte* path, U32 n, const char* prefix) {
    U32 k = 0;
    for (; prefix[k]; ++k) if (k == n || path[k] != Byte(prefix[k])) return 0;
    return k;
}
// The official content list (content.sha256: "<64 hex> <space or *><path>" lines, paths relative to
// content/, sorted bytewise), in one pass: its form and order, no listed package file in it (a merge
// with the sorted list), no package id naming one of its presets or aux_presets folders.
Status check_official(Builder& b) {
    const Byte* previous = 0;
    U32 previous_bytes = 0, next = 0;
    for (U32 at = 0; at != b.official_bytes;) {
        const Byte* line = b.official + at;
        const Byte* path = line + 66;
        const U32 room = b.official_bytes - at;
        if (room < 68 || line[64] != ' ' || (line[65] != ' ' && line[65] != '*'))
            return refuse(b, Status::official, "list");
        U32 bytes = 0;
        while (bytes != room - 66 && bytes != max_path && path[bytes] != '\n') ++bytes;
        if (!bytes || bytes == room - 66 || path[bytes] != '\n' ||
            (previous && order(previous, previous_bytes, path, bytes) >= 0)) return refuse(b, Status::official, "list");
        for (; next != b.listed_count; ++next) {
            const Listed& l = b.listed[next];
            if (l.package == none) continue;   // the catalog: replaced by design
            const int c = order(reinterpret_cast<const Byte*>(l.path + 8), length(l.path + 8, max_path), path, bytes);
            if (!c) return refuse(b, Status::official, "file", l.path + 8);
            if (c > 0) break;
        }
        U32 skip = prefix_of(path, bytes, "presets/");
        if (!skip) skip = prefix_of(path, bytes, "aux_presets/");
        U32 slash = skip;
        while (skip && slash != bytes && path[slash] != '/') ++slash;
        for (U32 q = 0; skip && slash != bytes && q != b.count; ++q) {
            const char* id = b.packages[q].id;
            if (!order(reinterpret_cast<const Byte*>(id), length(id, 33), path + skip, slash - skip))
                return refuse(b, Status::official, "id", id);
        }
        previous = path; previous_bytes = bytes;
        at += 67 + bytes;
    }
    return Status::ok;
}

// The catalog with every entry at its offset (ascending), then files.sha256.
Status finish(Builder& b) {
    U32 chosen = 0;
    extension_chosen(b, "", chosen);
    if (b.extension_count != chosen) return refuse(b, Status::package, "extensions");
    if (!b.machine_count || b.machine_count != b.selection->machine_count)   // linkKit needs one
        return refuse(b, Status::unlinked, "machine");
    Status listing = sort_listed(b);
    if (listing != Status::ok || (listing = check_official(b)) != Status::ok) return listing;
    if (b.aux > 64) return refuse(b, Status::capacity, "aux presets");   // core.mjs: distinct paths
    const U32 n = b.listed_count;
    Out out = {b.out, b.out_capacity, 0};
    const U32 at[3] = {b.pins->engine_at, b.pins->fx_at, b.pins->seq_at};
    U32 sorted[3] = {0, 1, 2};
    for (U32 x = 0; x != 3; ++x)
        for (U32 y = x + 1; y != 3; ++y)
            if (at[sorted[y]] < at[sorted[x]]) { const U32 t = sorted[x]; sorted[x] = sorted[y]; sorted[y] = t; }
    U32 position = 0;
    for (U32 g = 0; g != 3; ++g) {
        const U32 group = sorted[g];
        if (!out.put(b.catalog + position, at[group] - position)) return refuse(b, Status::capacity, catalog_path);
        position = at[group];
        for (U32 m = 0; m != b.machine_count; ++m) {
            const Package& p = b.packages[b.machines[m]];
            const U32 kind_group = instrument(p.kind) ? 0 : p.kind == Kind::effect ? 1 : 2;
            if (!b.entry_bytes[b.machines[m]] || kind_group != group) continue;
            bool ok;
            if (!p.numbered) ok = out.put(p.catalog_text, p.catalog_bytes);
            else {
                U32 number = 0;
                entry_number(b, p, number);
                Byte text[16];
                const char* key = "\"id\": ";
                for (U32 k = 0; k != 6; ++k) text[k] = Byte(key[k]);
                const U32 digits = 6 + decimal(number, text + 6);
                ok = out.put(p.catalog_text, p.number_start) && out.put(text, digits) &&
                     out.put(p.catalog_text + p.number_end, p.catalog_bytes - p.number_end);
            }
            if (!ok) return refuse(b, Status::capacity, catalog_path);
        }
    }
    if (!out.put(b.catalog + position, b.catalog_size - position)) return refuse(b, Status::capacity, catalog_path);
    hash::Sha256 h;
    h.update(out.at, out.size); h.finish(b.catalog_sha256);
    b.catalog_bytes = out.size;
    Out list = {b.list, b.list_capacity, 0};
    for (U32 i = 0; i != n; ++i) {
        const Listed& l = b.listed[i];
        Byte line[66];
        for (U32 k = 0; k != 32; ++k) {
            line[2 * k] = Byte(hex_digits[l.sha256->bytes[k] >> 4]);
            line[2 * k + 1] = Byte(hex_digits[l.sha256->bytes[k] & 15]);
        }
        line[64] = line[65] = ' ';
        const Byte newline = '\n';
        if (!list.put(line, 66) || !list.put(reinterpret_cast<const Byte*>(l.path), length(l.path, max_path)) ||
            !list.put(&newline, 1)) return refuse(b, Status::capacity, "files.sha256");
    }
    b.list_bytes = list.size;
    return Status::ok;
}
}

U32 scratch_bytes(const Limits& l) {
    if (!l.packages || l.packages > max_packages || !l.files || l.files > max_files) return 0;
    return l.files * U32(sizeof(Listed)) + l.packages * 4 + ((2 * l.packages + 3) & ~3u);
}

Status start(Builder& b, const Catalog& pins, const Byte* catalog, U32 catalog_size, const Byte* official,
             U32 official_bytes, const Package* packages, U32 count, const mod_link::Selection& selection,
             const mod_link::Workspace& link, const mod_link::Result& linked, Byte* out, U32 out_capacity,
             Byte* list, U32 list_capacity, const Limits& limits, Byte* scratch, U32 scratch_capacity) {
    b.pins = &pins; b.catalog = catalog; b.catalog_size = catalog_size; b.packages = packages; b.count = count;
    b.official = official; b.official_bytes = official_bytes; b.selection = &selection;
    b.link = &link; b.linked = &linked; b.out = out; b.out_capacity = out_capacity; b.list = list; b.list_capacity = list_capacity;
    b.phase = failed; b.failure = Status::argument; b.detail[0] = 0;
    b.cursor = b.listed_count = b.machine_count = b.extension_count = b.aux = b.aux_folders = 0;
    b.catalog_bytes = b.list_bytes = 0;
    const U32 need = scratch_bytes(limits);
    if (!catalog || !packages || !count || !out || !list || !need || !scratch || !official || !official_bytes ||
        official_bytes > max_catalog ||
        (reinterpret_cast<unsigned long>(scratch) & 3) || scratch_capacity < need || count > limits.packages ||
        linked.status != mod_link::Status::ok) return refuse(b, Status::argument, "arguments");
    b.limit_packages = limits.packages; b.limit_files = limits.files;
    b.listed = reinterpret_cast<Listed*>(scratch);
    b.entry_bytes = reinterpret_cast<U32*>(scratch + limits.files * sizeof(Listed));
    b.machines = reinterpret_cast<unsigned short*>(scratch + limits.files * sizeof(Listed) + limits.packages * 4);
    // The official catalog: size, SHA-256, the three insertion points (after an entry, before "]").
    if (!catalog_size || catalog_size > max_catalog) return refuse(b, Status::catalog, "size");
    hash::Sha256 h;
    h.update(catalog, catalog_size); h.finish(b.catalog_sha256);
    if (!hash::equal(b.catalog_sha256, pins.sha256)) return refuse(b, Status::catalog, "sha256");
    ArrayEnd ends[3];
    U32 marked;
    if (!json(catalog, catalog_size, group_keys, 3, ends, max_depth, none, marked))
        return refuse(b, Status::catalog, "json");
    const U32 at[3] = {pins.engine_at, pins.fx_at, pins.seq_at};
    for (U32 k = 0; k != 3; ++k) {
        const ArrayEnd& e = ends[k];
        if (e.found != 1 || e.last == none || catalog[e.last] != '}' || at[k] <= e.last || at[k] > e.close ||
            at[k] == at[k ? k - 1 : 2]) return refuse(b, Status::insert, group_keys[k]);
    }
    Listed& l = b.listed[b.listed_count++];
    l.path = catalog_path; l.package = none; l.sha256 = &b.catalog_sha256;   // its digest: set by finish()
    b.phase = packages_phase;
    return Status::more;
}

Status step(Builder& b) {
    switch (b.phase) {
    case packages_phase: {
        const Status s = add_package(b, b.cursor);
        if (s != Status::ok) return s;
        if (++b.cursor == b.count) b.phase = finish_phase;
        return Status::more;
    }
    case finish_phase: {
        const Status s = finish(b);
        if (s != Status::ok) return s;
        b.phase = done;
        return Status::ok;
    }
    case done: return Status::ok;
    case failed: return b.failure;
    }
    return Status::argument;
}

Status check_object(const Byte* d, U32 size) {
    if (!d || size < 52 || size > (1u << 20) || d[0] != 0x7f || d[1] != 'E' || d[2] != 'L' || d[3] != 'F' ||
        d[4] != 1 || d[5] != 1 || le16(d + 16) != 1 || le16(d + 18) != 106) return Status::package;
    const U32 table = le32(d + 32), count = le16(d + 48);
    if (le16(d + 46) != 40 || !count || count >= 4096 || table > size || count * 40 > size - table) return Status::package;
    for (U32 s = 0; s != count; ++s) {
        const Byte* sh = d + table + s * 40;
        if (le32(sh + 4) != 2) continue;   // SHT_SYMTAB
        const U32 at = le32(sh + 16), bytes = le32(sh + 20), names = le32(sh + 24);
        if (at > size || bytes > size - at || le32(sh + 36) != 16 || (bytes & 15) || names >= count) return Status::package;
        const Byte* link = d + table + names * 40;
        const U32 strings = le32(link + 16), string_bytes = le32(link + 20);
        if (strings > size || string_bytes > size - strings) return Status::package;
        for (U32 off = 0; off < bytes; off += 16) {
            const Byte* sym = d + at + off;
            const U32 name = le32(sym);
            if (le16(sym + 14) != 0xfff1 || (sym[12] & 15) == 4 || !name) continue;
            if (name >= string_bytes || d[strings + name]) return Status::package;   // malformed, or named
        }
    }
    return Status::ok;
}

const char* status_name(Status s) {
    switch (s) {
    case Status::ok: return "ok";
    case Status::more: return "more";
    case Status::argument: return "argument";
    case Status::capacity: return "capacity";
    case Status::catalog: return "catalog";
    case Status::insert: return "insert";
    case Status::entry: return "entry";
    case Status::number: return "number";
    case Status::package: return "package";
    case Status::missing: return "missing";
    case Status::duplicate: return "duplicate";
    case Status::official: return "official";
    case Status::unlinked: return "unlinked";
    }
    return "unknown";
}
} }

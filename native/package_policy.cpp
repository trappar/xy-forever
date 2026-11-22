#include "package_policy.hpp"
namespace xyf_core { namespace package {
namespace {
U32 length(const char* text) {
    U32 n = 0;
    while (text[n]) ++n;
    return n;
}
bool same(const char* a, U32 size, const char* b) {
    const U32 n = length(b);
    if (size != n) return false;
    for (U32 i = 0; i < n; ++i)
        if (a[i] != b[i]) return false;
    return true;
}
bool prefix(const char* a, U32 size, const char* b) {
    const U32 n = length(b);
    if (size < n) return false;
    for (U32 i = 0; i < n; ++i)
        if (a[i] != b[i]) return false;
    return true;
}
char lower(char c) { return c >= 'A' && c <= 'Z' ? char(c + 'a' - 'A') : c; }
bool bootloader(const char* name, U32 size) {
    const char word[] = "bootloader";
    for (U32 i = 0; i + 10 <= size; ++i) {
        U32 j = 0;
        while (j < 10 && lower(name[i + j]) == word[j]) ++j;
        if (j == 10) return true;
    }
    return false;
}
Status output(Destination& out, Role role, const char* leading, const char* trailing = 0, U32 count = 0) {
    const U32 n = length(leading);
    if (n + count > 256) return Status::capacity;
    out.role = role;
    for (U32 i = 0; i < n; ++i) out.relative[i] = leading[i];
    for (U32 i = 0; i < count; ++i) out.relative[n + i] = trailing[i];
    out.relative[n + count] = 0;
    return Status::ok;
}
struct Rule {
    const char* source;
    Role role;
    const char* destination;
};
const Rule rules[] = {{"firmware/blackfin/bin/os.ldr", Role::os, "payload/os.ldr"},
                      {"firmware/backplane/bin/backplane.cyacd", Role::backplane, "companions/backplane.cyacd"},
                      {"firmware/ble/bin/ble.bin", Role::ble, "companions/ble.bin"},
                      {"firmware/blackfin/bin/os.version", Role::metadata, "metadata/os.version"},
                      {"firmware/backplane/bin/backplane.version", Role::metadata, "metadata/backplane.version"},
                      {"firmware/ble/bin/ble.version", Role::metadata, "metadata/ble.version"},
                      {"firmware/firmware.version", Role::metadata, "metadata/firmware.version"},
                      {"firmware/versions.txt", Role::metadata, "metadata/versions.txt"}};
}
Status classify(const char* name, U32 size, Destination& out) {
    out.role = Role::excluded_host;
    out.relative[0] = 0;
    if (!name || !size || size > 256) return Status::unsafe_name;
    U32 component = 0;
    for (U32 i = 0; i <= size; ++i) {
        if (i == size || name[i] == '/') {
            const U32 count = i - component;
            if (!count || (count == 1 && name[component] == '.') ||
                (count == 2 && name[component] == '.' && name[component + 1] == '.') || name[i - 1] == '.' ||
                name[i - 1] == ' ')
                return Status::unsafe_name;
            component = i + 1;
        } else if (Byte(name[i]) < 32 || Byte(name[i]) >= 127 || name[i] == '\\' || name[i] == ':')
            return Status::unsafe_name;
    }
    if (bootloader(name, size)) return output(out, Role::excluded_bootloader, "");
    U32 basename = 0;
    for (U32 i = 0; i < size; ++i)
        if (name[i] == '/') basename = i + 1;
    if (same(name + basename, size - basename, ".DS_Store") || prefix(name, size, "__MACOSX/"))
        return output(out, Role::excluded_host, "");
    for (U32 i = 0; i < sizeof(rules) / sizeof(rules[0]); ++i)
        if (same(name, size, rules[i].source)) return output(out, rules[i].role, rules[i].destination);
    const char content[] = "firmware/blackfin/content/";
    const U32 start = sizeof(content) - 1;
    if (size > start && prefix(name, size, content))
        return output(out, Role::content, "content/", name + start, size - start);
    return Status::unknown_member;
}
Status parse_version(const Byte* bytes, U32 size, Version& out) {
    if (!bytes || size < 7 || size > 128) return Status::version;
    Version candidate = {{0, 0, 0, 0}};
    U32 at = 0;
    for (U32 i = 0; i < 4; ++i) {
        const U32 begin = at;
        while (at < size && bytes[at] >= '0' && bytes[at] <= '9') {
            if (at - begin >= 5) return Status::version;
            candidate.field[i] = candidate.field[i] * 10 + bytes[at++] - '0';
            if (candidate.field[i] > 65535) return Status::version;
        }
        if (at == begin) return Status::version;
        if (i < 3 && (at == size || bytes[at++] != (i == 2 ? '+' : '.'))) return Status::version;
    }
    if (at < size && bytes[at] == '@') {
        ++at;
        if (size - at < 40) return Status::version;
        for (U32 i = 0; i < 40; ++i) {
            const Byte c = bytes[at++];
            if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'))) return Status::version;
        }
    }
    if (at < size && bytes[at] == '\r') ++at;
    if (at < size && bytes[at] == '\n') ++at;
    if (at != size) return Status::version;
    out = candidate;
    return Status::ok;
}
int compare(const Version& a, const Version& b) {
    for (U32 i = 0; i < 4; ++i)
        if (a.field[i] != b.field[i]) return a.field[i] < b.field[i] ? -1 : 1;
    return 0;
}
}}

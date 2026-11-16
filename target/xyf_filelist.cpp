#include "xyf_manifest.hpp"
// The streamed files.sha256 reader (xyf_manifest.hpp). Memory: the ListReader (one line and
// the previous path); duplicates are found because the list must be sorted by path.
namespace xyf { namespace manifest {
namespace {
char lower(char c) { return c >= 'A' && c <= 'Z' ? char(c + 'a' - 'A') : c; }
uint32_t nibble(char c) {
    return c >= '0' && c <= '9' ? uint32_t(c - '0') : c >= 'a' && c <= 'f' ? uint32_t(c - 'a' + 10) : 16;
}
// As native/package_policy's classify: "bootloader" anywhere, ASCII case ignored.
bool bootloader(const char* s, uint32_t n) {
    static const char word[] = "bootloader";
    for (uint32_t i = 0; i + 10 <= n; ++i) {
        uint32_t j = 0;
        while (j != 10 && lower(s[i + j]) == word[j]) ++j;
        if (j == 10) return true;
    }
    return false;
}
bool valid_path(const char* p, uint32_t n) {
    if (!n || n > max_path) return false;
    uint32_t start = 0;
    for (uint32_t i = 0; i <= n; ++i) {
        if (i == n || p[i] == '/') {
            // Empty (leading, doubled or trailing '/'), ".", ".." and names ending in '.' or ' '.
            if (i == start || p[i - 1] == '.' || p[i - 1] == ' ') return false;
            start = i + 1;
            continue;
        }
        const uint8_t c = uint8_t(p[i]);
        if (c < 0x20 || c > 0x7e || c == '\\' || c == ':') return false;
    }
    return true;
}
ListStatus fail(ListReader& r, ListStatus s) { r.status = s; return s; }
// The line without its LF is r.line[0..r.length).
ListStatus line(ListReader& r, FileVisit visit, void* context) {
    ++r.lines;
    FileEntry e;
    if (r.length < 64) return fail(r, list_bad_hex);
    for (uint32_t i = 0; i != 32; ++i) {
        const uint32_t hi = nibble(r.line[2 * i]), lo = nibble(r.line[2 * i + 1]);
        if (hi == 16 || lo == 16) return fail(r, list_bad_hex);
        e.sha256[i] = uint8_t(hi << 4 | lo);
    }
    if (r.length < 66 || r.line[64] != ' ' || (r.line[65] != ' ' && r.line[65] != '*')) return fail(r, list_syntax);
    const char* path = r.line + 66;
    const uint32_t n = r.length - 66;
    if (!valid_path(path, n)) return fail(r, list_bad_path);
    static const char self[] = "files.sha256";
    bool is_self = n == sizeof(self) - 1;
    for (uint32_t i = 0; is_self && i != n; ++i) is_self = path[i] == self[i];
    if (is_self || bootloader(path, n)) return fail(r, list_refused_path);
    if (r.count) {
        int order = 0;
        const uint32_t m = n < r.previous_length ? n : r.previous_length;
        for (uint32_t i = 0; i != m && !order; ++i)
            if (path[i] != r.previous[i]) order = uint8_t(path[i]) < uint8_t(r.previous[i]) ? -1 : 1;
        if (!order) order = n == r.previous_length ? 0 : n < r.previous_length ? -1 : 1;
        if (!order) return fail(r, list_duplicate);
        if (order < 0) return fail(r, list_order);
    }
    if (r.count == max_files) return fail(r, list_too_many);
    for (uint32_t i = 0; i != n; ++i) r.previous[i] = path[i];
    r.previous[n] = 0;
    r.previous_length = n;
    r.line[r.length] = 0;
    e.path = path;
    e.length = n;
    e.index = r.count;
    e.binary = r.line[65] == '*';
    ++r.count;
    if (visit && !visit(context, e)) return fail(r, list_stopped);
    return list_ok;
}
}

void list_begin(ListReader& r) {
    for (uint32_t i = 0; i != sizeof(r); ++i) reinterpret_cast<char*>(&r)[i] = 0;
}

ListStatus list_feed(ListReader& r, const char* data, uint32_t length, FileVisit visit, void* context) {
    if (r.status) return ListStatus(r.status);
    if (!data && length) return fail(r, list_syntax);
    for (uint32_t i = 0; i != length; ++i) {
        if (data[i] == '\n') {
            const ListStatus s = line(r, visit, context);
            if (s != list_ok) return s;
            r.length = 0;
            continue;
        }
        if (r.length == list_line) { ++r.lines; return fail(r, list_line_too_long); }
        r.line[r.length++] = data[i];
    }
    return list_ok;
}

ListStatus list_end(ListReader& r) {
    if (r.status) return ListStatus(r.status);
    if (r.length) { ++r.lines; return fail(r, list_no_newline); }
    if (!r.count) return fail(r, list_empty);
    return list_ok;
}
} }

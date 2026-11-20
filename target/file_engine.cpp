#include "file_engine.hpp"
// The file engine's path policy and request decision (see file_engine.hpp).
namespace xy_forever { namespace file_engine {
namespace {
static_assert(max_path - 1 <= 255, "a name (<= 255 bytes) always fits in a path");

char fold(char c) { return c >= 'A' && c <= 'Z' ? char(c + ('a' - 'A')) : c; }
// The n bytes at s are name (ASCII case ignored when folded).
bool named(const char* s, uint32_t n, const char* name, bool folded) {
    uint32_t k = 0;
    for (; k != n; ++k)
        if (!name[k] || (folded ? fold(s[k]) : s[k]) != name[k]) return false;
    return !name[k];
}

// A path as the policy sees it: names below /yaffs2 ('/' runs are one separator, a trailing
// '/' ends nothing), whether it is the dev-menu marker, whether it lies inside the engine's
// tmp folder (/yaffs2/xy-forever/tmp/<name>...), whether it is the stock updater's inbox /yaffs2/upgrade
// or below it (first name "upgrade", ASCII case ignored: over-refuses, as the marker), whether it is the Settings
// record (#65: /yaffs2/xy-forever/settings.txt or settings.old, ASCII case ignored).
struct Parsed { uint32_t names; bool marker, tmp, inbox, settings; };
Verdict parse(const char* path, Parsed& p) {
    p.names = 0; p.marker = false; p.tmp = false; p.inbox = false; p.settings = false;
    if (!path) return refuse_path;
    uint32_t length = 0;
    for (; length != max_path && path[length]; ++length) {
        const uint8_t c = uint8_t(path[length]);
        if (c < 0x20 || c == 0x7f) return refuse_path;            // control bytes
    }
    if (length == max_path) return refuse_path;                     // no NUL within max_path
    static const char root[] = "/yaffs2";
    for (uint32_t i = 0; i != sizeof(root) - 1; ++i)
        if (path[i] != root[i]) return refuse_outside;
    if (path[7] && path[7] != '/') return refuse_outside;          // "/yaffs20" is another name
    bool tmp_folder = false, own = false;
    for (uint32_t i = 7; path[i];) {
        if (path[i] == '/') { ++i; continue; }
        const uint32_t start = i;
        while (path[i] && path[i] != '/') ++i;
        const char* name = path + start;
        const uint32_t n = i - start;
        if (name[0] == '.' && (n == 1 || (n == 2 && name[1] == '.'))) return refuse_path;
        ++p.names;
        if (p.names == 1) {
            p.marker = named(name, n, "bootloader.dev", true);   // case ignored: over-refuses (.hpp)
            p.inbox = named(name, n, "upgrade", true);
            tmp_folder = named(name, n, "xy-forever", false);
            own = named(name, n, "xy-forever", true);
        } else {
            p.marker = false;
            p.settings = p.names == 2 && own && (named(name, n, "settings.txt", true) || named(name, n, "settings.old", true));
            if (p.names == 2) tmp_folder = tmp_folder && named(name, n, "tmp", false);
            else if (tmp_folder) p.tmp = true;
        }
    }
    return allow;
}
}

Verdict decide_engine(Operation op, const char* path, const char* second) {
    if (op == op_raw) return refuse_raw;
    if (op < op_read || op > op_rename || (second && op != op_rename)) return refuse_operation;
    Parsed p;
    const Verdict v = parse(path, p);
    if (v != allow) return v;
    const bool protected_name = p.names == 0 || p.marker || p.inbox;   // /yaffs2, the marker, the inbox
    switch (op) {
    case op_unlink: case op_rmdir:
        return protected_name ? refuse_protected : allow;
    case op_mkdir: case op_write:                                    // /yaffs2 itself: harmless (EEXIST, EISDIR)
        if (p.marker || p.inbox) return refuse_protected;
        return p.names > max_folders + (op == op_write) ? refuse_depth : allow;
    case op_rename: {
        if (protected_name) return refuse_protected;
        Parsed q;
        const Verdict w = parse(second, q);
        if (w != allow) return w;
        if (q.names == 0 || q.marker || q.inbox) return refuse_protected;
        return p.names > max_folders + 1 || q.names > max_folders + 1 ? refuse_depth : allow;   // A: rename_from (q = p)
    }
    case op_stream:
        return !p.tmp ? refuse_tmp : p.names > max_folders + 1 ? refuse_depth : allow;
    default:
        return allow;                                                // read, list, stat
    }
}

bool settings_record(const char* path) {
    Parsed p;
    return parse(path, p) == allow && p.settings;
}

bool upload_size(const char* path, uint32_t& size) {
    static const char head[] = "/yaffs2/xy-forever/tmp/put-";
    uint32_t i = 0;
    size = 0;
    for (; head[i]; ++i) if (!path || path[i] != head[i]) return false;
    for (uint32_t k = 0; k != 17; ++k) {
        const char c = path[i + k];
        if (k == 8) { if (c != '-') return false; continue; }
        const uint32_t d = c >= '0' && c <= '9' ? uint32_t(c - '0') : c >= 'a' && c <= 'f' ? uint32_t(c - 'a' + 10) : 16;
        if (d == 16) return false;
        if (k < 8) size = size << 4 | d;
    }
    return !path[i + 17];
}
void upload_name(char* out, uint32_t size, uint32_t tag) {
    static const char head[] = "/yaffs2/xy-forever/tmp/put-", hex[] = "0123456789abcdef";
    uint32_t i = 0;
    for (; head[i]; ++i) out[i] = head[i];
    for (uint32_t k = 0; k != 8; ++k) out[i + k] = hex[(size >> (28 - 4 * k)) & 15];
    out[i + 8] = '-';
    for (uint32_t k = 0; k != 8; ++k) out[i + 9 + k] = hex[(tag >> (28 - 4 * k)) & 15];
    out[i + 17] = 0;
}

static const uint32_t upload_head = 27;                // "/yaffs2/xy-forever/tmp/put-"
bool join_upload(const char* source, const char* target, char* out) {
    uint32_t size = 0, n = 0;
    if (!upload_size(source, size)) return false;
    while (target[n]) if (++n + upload_tail >= max_path) return false;
    for (uint32_t i = 0; i != n; ++i) out[i] = target[i];
    out[n] = upload_mark;
    for (uint32_t i = 0; i != upload_tail - 1; ++i) out[n + 1 + i] = source[upload_head + i];
    out[n + upload_tail] = 0;
    return true;
}
bool split_upload(char* path, char* source) {
    uint32_t n = 0, size = 0;
    while (n != max_path && path[n]) ++n;
    if (n == max_path || n < upload_tail + 1 || path[n - upload_tail] != upload_mark) return false;
    upload_name(source, 0, 0);
    for (uint32_t i = 0; i != upload_tail - 1; ++i) source[upload_head + i] = path[n - upload_tail + 1 + i];
    if (!upload_size(source, size)) return false;
    path[n - upload_tail] = 0;
    return true;
}

uint32_t depth(const char* path) {
    Parsed p;
    return parse(path, p) == allow ? p.names : 0xffffffffu;
}

bool raw_request(uint32_t vtable, uint32_t op) {
    return (vtable == partition_class && (op == 4 || op == 0x14 || op == 0x18)) ||
           (vtable == sector_class && (op == 0x11 || op == 0x12)) ||
           (vtable == write_class && (op == 0x15 || op == 0x16));
}

namespace {
Action refusal(Verdict v) {
    return v == refuse_raw ? refuse_raw_layer : v == refuse_outside ? refuse_outside_path :
           v == refuse_protected ? refuse_protected_path : v == refuse_tmp ? refuse_tmp_path :
           v == refuse_path || v == refuse_depth ? refuse_bad_path : refuse_op;
}
}

Action decide_request(uint32_t vtable, uint32_t op, uint32_t argument, uint32_t path_pointer, const char* path,
                      const State& state, const char* source) {
    if (raw_request(vtable, op)) return refusal(decide_engine(op_raw, 0));
    if (state.partition) return refuse_partition;
    if (!state.on) return refuse_off;
    uint32_t operation = 0;                      // Operation of a path request
    bool file = false, folder = false, change = false;
    Action run = refuse_op;
    if (vtable == partition_class) {
        if (op == 8) return run_mount;
        if (op == 9) return run_unmount;
        if (op == 0x2c || op == 0x2d) { run = run_file; file = true; }
        else if (op == 0x30) { run = run_closedir; folder = true; }
        else return refuse_op;                   // 0x34 fsync, 0x36 truncate, unknown
    } else if (vtable == path_class) {
        if (op == 0x29) { run = run_open; operation = argument ? op_write : op_read; }
        else if (op == 0x2e) { run = run_opendir; operation = op_list; }
        else if (op == 0x21 && argument == rename_from) { run = run_rename_from; operation = op_rename; }
        else if (op == 0x21 && (argument == rename_to || argument == rename_over || argument == rename_upload)) {
            if (!source) return refuse_op;       // no rename_from just before it (rename_upload: not split_upload's form)
            uint32_t size;
            if (argument != rename_to && !upload_size(source, size)) return refuse_tmp_path;
            run = argument == rename_to ? run_rename_to : run_rename_over; operation = op_rename;
        }
        else if (op == 0x21) { run = run_path; operation = op_stat; }
        else if (op == 0x25) { run = run_path; operation = op_unlink; }
        else if (op == 0x26) { run = run_path; operation = op_mkdir; }
        else if (op == 0x27) { run = run_path; operation = op_rmdir; }
        else return refuse_op;                   // 0x28 sync, 0x35 set times, unknown
    } else if (vtable == query_class) {
        if (op != 0x22 && op != 0x23 && op != 0x31) return refuse_op;
        run = run_query; operation = op_stat;
    } else if (vtable == read_class) {
        if (op != 0x2a) return refuse_op;
        run = run_read; file = true;
    } else if (vtable == sector_class) {
        if (op != 0x2f) return refuse_op;
        run = run_readdir; folder = true;
    } else if (vtable == write_class) {
        if (op != 0x2b) return refuse_op;
        run = run_write; file = true; change = true;
    } else return refuse_class;
    if (operation) {
        if (path_pointer != path_buffer) return refuse_bad_path;
        // A rename source is checked as a source and as a name (second: itself), its target with the source.
        const Verdict v = operation != op_rename ? decide_engine(static_cast<Operation>(operation), path) :
                          decide_engine(op_rename, run != run_rename_from ? source : path, path);
        if (v != allow) return refusal(v);
        change = operation == op_write || operation == op_mkdir || operation == op_unlink || operation == op_rmdir ||
                 run == run_rename_to || run == run_rename_over;
    }
    if (!state.session) return refuse_mount;
    if (change && !state.window) return refuse_read_only;
    if ((file && !state.fd_known) || (folder && !state.dir_known)) return refuse_handle;
    if ((run == run_open && !state.fd_space) || (run == run_opendir && !state.dir_space)) return refuse_full;
    return run;
}
} }

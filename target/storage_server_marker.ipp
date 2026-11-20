// Part of storage_server.cpp, included there at this point: not compiled alone.
// #144: A's own checks inside a request (stat_into below, engine_identity) counted and timed for
// storage_server_timing.ipp, so a request's time splits into ours and the stock executor's.
struct Checks { U32 count; uint64_t cycles; };
Checks a_checks __attribute__((section(".data"))) = {0, 0};
struct CheckSpan {
    const uint64_t start;
    CheckSpan() : start(xyf::clock::cycles()) {}
    ~CheckSpan() { ++a_checks.count; a_checks.cycles += xyf::clock::cycles() - start; }
};
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
// The dev-menu marker around every unlink and rmdir (the operations that remove a name;
// and rename, rename_now). The policy checks the spelling, but yaffs follows symlinks and hardlinks inside a
// path (/yaffs2/s/bootloader.dev with s -> /yaffs2). "The marker exists" = the stock posix stat of
// marker_path succeeds: whatever object the name resolves to (file, folder, links followed), as the
// 1.2.1 bootloader's access("bootloader.dev", R_OK) (88a1d7e6) finds it. Before the operation: a
// path that stats to the marker's own record is refused. After it: a marker that existed before and
// is gone is re-created (empty; stock "wb", mode 0666). A marker that did not exist before (removed
// through the stock dev menu, the owner's right) is never created. Lowercase, the name the
// bootloader asks for (88a202b0): yaffs compares names case-sensitively (file_engine.hpp).
const char marker_path[] = "/yaffs2/bootloader.dev";
static const U32 record_words = 0x80 / 4;                             // >= the stock stat record (0x34)
// [0] the marker, [1] a path, [2] the inbox, [3] a physical ancestor, [4] the mount root.
U32 marker_record[5][record_words] __attribute__((section(".data"))) = {};
// #144: whether the marker and the inbox exist, each stat'ed at most once per request (protect_reset at its start:
// nothing changes storage between a request's checks and its operation): bit 0/2 asked, bit 1/3 found.
U32 protect_known __attribute__((section(".data"))) = 0;
void protect_reset() { protect_known = 0; }
__attribute__((noinline)) bool stat_into(const char* path, U32* record) {   // noinline: the builder's scope
    const CheckSpan span;
    for (U32 i = 0; i != record_words; ++i) record[i] = 0;
    return stock<0x889f3516u, int>(path, record) == 0;                 // stock posix stat (on a: yaffs)
}
bool same_record(const U32* a, const U32* b) {
    for (U32 i = 0; i != record_words; ++i) if (a[i] != b[i]) return false;
    return true;
}
bool same_object() { return same_record(marker_record[0], marker_record[1]); }
// The marker's record in marker_record[0] (this request's first ask stats it); false: no marker.
bool marker_exists() {
    if (!(protect_known & 1u)) protect_known |= 1u | (stat_into(marker_path, marker_record[0]) ? 2u : 0u);
    return (protect_known & 2u) != 0;
}
// The marker vanished although its spellings are refused: put it back. The executor has already
// replied (it queues its own answer), so the refusal is in a's log (0xfff9 restored, 0xfff8 not).
__attribute__((noinline)) void restore_marker(U32 op) {
    const int fd = stock<0x889f3b50u, int>(0u, marker_path, 0x8301u);   // stock posix open, "wb"
    if (fd >= 0) stock<0x889f285au, int>(fd);                          // stock posix close
    ++result.refused;
    note(op, file_engine::refuse_protected_path, stat_into(marker_path, marker_record[1]) ? 0xfff9u : 0xfff8u);
}
#endif

#if defined(XYF_USB_FILE_WRITE)
// The two write-rule exceptions through links (yaffs follows them inside a path): a change whose path stats to
// the marker, or whose containing directory resolves inside /yaffs2/upgrade, is refused.
const char inbox_path[] = "/yaffs2/upgrade";
bool inbox_exists() {
    if (!(protect_known & 4u)) protect_known |= 4u | (stat_into(inbox_path, marker_record[2]) ? 8u : 0u);
    return (protect_known & 8u) != 0;
}
bool mount_root_exists() {
    if (!(protect_known & 16u)) protect_known |= 16u | (stat_into("/yaffs2", marker_record[4]) ? 32u : 0u);
    return (protect_known & 32u) != 0;
}
bool same_identity(const U32* a, const U32* b) { return a[0] == b[0] && a[1] == b[1]; }   // device, inode
// A legal client path has fewer than max_path/2 one-byte directory components. Give resolved aliases the same
// bound: deeper aliases are refused if ancestry cannot be established, never mistaken for outside the inbox.
// YAFFS resolves internal ".." after following directory links (1.2.1: 889fa246/889fa258; final ..: 889fa300).
const U32 protect_parent_steps = max_path / 2;
char protect_walk[max_path + 3 * protect_parent_steps] __attribute__((section(".data"))) = {0};
bool directory_reaches_inbox(const char* path) {
    U32 end = 0;
    while (end != max_path && path[end]) { protect_walk[end] = path[end]; ++end; }
    if (end == max_path || !mount_root_exists()) return true;       // no ancestry proof: refuse the change
    protect_walk[end] = 0;
    // Missing names and regular files have no usable /.. lookup. Start from their nearest existing directory;
    // retaining the whole prefix lets YAFFS, rather than lexical chopping, follow any directory symlink.
    for (;;) {
        if (stat_into(protect_walk, marker_record[3]) && (marker_record[3][2] & 0xf000u) == 0x4000u) break;
        while (end && protect_walk[--end] != '/') {}
        if (end < 7) return true;
        protect_walk[end] = 0;
    }
    for (U32 step = 0; step != protect_parent_steps; ++step) {
        if (same_identity(marker_record[3], marker_record[2])) return true;
        if (same_identity(marker_record[3], marker_record[4])) return false;
        protect_walk[end++] = '/'; protect_walk[end++] = '.'; protect_walk[end++] = '.'; protect_walk[end] = 0;
        if (!stat_into(protect_walk, marker_record[3])) return true;
    }
    return true;
}
// #6: a writable open follows a final link (stock open 889fbbe4: find 889fa2be, then FollowLink 889fa134), and stat
// identifies its target but no parent. Only writable opens need this: unlink, rmdir and rename act on the link entry.
// This adapter is the one caller of the admitted stock lookup (xyf_build.py A_FIND_CALLS): find(relative, path,
// depth, equivalent, &parent, &notdir, &loop) returns the object a path names, its final link not followed, and the
// folder holding that name. As stock does, it follows the final links (each text copied here first; relative from the
// link's own parent), then walks the result's parents by object to the mount root. A missing final target is judged
// by the folder that would hold it. Every bound reached, a loop, or a missing root refuses. yaffs_obj (1.2.1 listing):
// parent +0x1c (889fa248), type +0x64 (2 link, 3 folder: 889fa158, 889fa26a), link text +0x68 (889fa15c).
const U32 object_parent = 0x1c, object_type = 0x64, object_alias = 0x68, type_link = 2, type_folder = 3;
const U32 final_links = 6;                 // stock gives up past 6 (depth > 5: 889fa1ac), each at depth 0, 1, ...
char link_text[max_path] __attribute__((section(".data"))) = {0};
__attribute__((noinline, optimize("no-move-loop-invariants"))) bool final_link_reaches_inbox(const char* path) {
    const CheckSpan span;
    if (!spans_match()) return true;                                   // the lookup only while its spans match
    const char* const names[3] = {"/yaffs2", inbox_path, path};
    U32 found[3] = {0, 0, 0};                                          // the root, the inbox, the target
    for (U32 k = 0; k != 3; ++k) {
        const char* text = names[k];
        U32 relative = 0, object = 0, parent = 0;
        for (U32 link = 0;; ++link) {
            int notdir = 0, loop = 0;
            parent = 0;
            object = stock<0x889fa2beu, U32>(relative, text, int(link ? link - 1 : 0), 1, &parent, &notdir, &loop);
            if (loop) return true;
            if (!object || word(object + object_type) != type_link) break;
            if (link == final_links) return true;
            const U32 alias = word(object + object_alias);
            U32 i = 0;
            while (alias && i != max_path && (link_text[i] = char(byte(alias + i)))) ++i;
            relative = link_text[0] == '/' ? 0 : word(object + object_parent);
            if (!alias || i == max_path || (link_text[0] != '/' && !relative)) return true;
            text = link_text;
        }
        found[k] = object ? object : k == 2 ? parent : 0;              // a missing target: the folder for it
    }
    if (!found[0] || word(found[0] + object_type) != type_folder) return true;
    if (!found[1] || !found[2]) return false;                          // no inbox, or nowhere to write
    U32 object = found[2];
    for (U32 step = 0; step != protect_parent_steps; ++step) {
        if (object == found[1]) return true;
        if (object == found[0]) return false;
        object = word(object + object_parent);
        if (!object) return true;
    }
    return true;
}
// The caller's full-path record is preserved (rename keeps its mode/size). Marker/inbox/root are cached only
// within this request; no storage operation can run between its ancestry checks and its mutation.
// A link directly to an inbox FILE: final_link_reaches_inbox (writable opens) resolves its target's parents.
__attribute__((noinline)) bool reaches_protected(char* path, U32* record = marker_record[1], bool* found = 0) {
    const bool marker = marker_exists(), inbox = inbox_exists();
    const bool got = (marker || inbox || found) && stat_into(path, record);
    if (found) *found = got;
    if (got && ((marker && same_record(record, marker_record[0])) || (inbox && same_record(record, marker_record[2]))))
        return true;
    return inbox && directory_reaches_inbox(path);
}
// file.rename on a, after rename_to passed decide_request (the pair's policy; the write window is
// open): both paths in the write area again, the source must exist (ENOENT) and the target must not
// (EEXIST: the stock rename would replace it), the marker by another name is refused and a marker that
// vanishes is re-created (as for unlink and rmdir), then the yaffs rename. 0, or the error number.
// over (rename_over, #4): the source is an upload whose stat size must equal its name's (EINVAL), the
// target a file or absent (EISDIR). yaffs_rename_obj 88a01afa replaces it in one header write: the
// source's new header names the old object as shadowed (88a018a6 with its id, gc off), so a scan after a
// power cut deletes the old one; then the old one is unlinked (88a01696). Old or new, never neither.
const U32 yaffs_error = 0x8806f940u;                                  // 889f998c / 889f9982
const U32 error_noent = 2, error_io = 5, error_exist = 17, error_isdir = 21, error_inval = 22;
U32 rename_now(char* to, bool over) {
    const char* from = rename_source;
    uint32_t size = 0;
    if (!valid_rename(from, to)) return error_access;
    // #144: each path stat'ed once (its record's mode and size kept), the marker and the inbox once (reaches_protected).
    bool source = false, target = false;
    if (reaches_protected(rename_source, marker_record[1], &source)) return error_access;
    const U32 source_mode = marker_record[1][2] & 0xf000u, source_size = marker_record[1][7];
    if (reaches_protected(to, marker_record[1], &target)) return error_access;
    if (!source) return error_noent;
    if (over && (!file_engine::upload_size(from, size) || source_mode != 0x8000u || source_size != size))
        return error_inval;
    if (target && (!over || (marker_record[1][2] & 0xf000u) != 0x8000u)) return over ? error_isdir : error_exist;
    const bool marker = marker_exists();           // (the source as the marker by another name: refused above)
    const int r = a_side::rename_object(from, to);
    U32 error = 0;
    if (r == -2) { note(0x21, file_engine::refuse_off, 0xfff7u); error = error_io; }   // spans differ: not called
    else if (r < 0) { error = U32(-int32_t(word(yaffs_error))); if (!error || error > 0xffffu) error = error_io; }
    if (marker && !stat_into(marker_path, marker_record[1])) restore_marker(0x21);
    return error;
}
#endif

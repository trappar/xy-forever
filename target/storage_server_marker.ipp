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
U32 marker_record[2][record_words] __attribute__((section(".data"))) = {};
__attribute__((noinline)) bool stat_into(const char* path, U32* record) {   // noinline: the builder's scope
    const CheckSpan span;
    for (U32 i = 0; i != record_words; ++i) record[i] = 0;
    return stock<0x889f3516u, int>(path, record) == 0;                 // stock posix stat (on a: yaffs)
}
bool same_object() {
    for (U32 i = 0; i != record_words; ++i) if (marker_record[0][i] != marker_record[1][i]) return false;
    return true;
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
// the marker, or whose path or any folder above it stats to /yaffs2/upgrade, is refused. The path is cut in place
// at each '/' and restored.
const char inbox_path[] = "/yaffs2/upgrade";
__attribute__((noinline)) bool reaches_protected(char* path) {
    if (stat_into(marker_path, marker_record[0]) && stat_into(path, marker_record[1]) && same_object()) return true;
    if (!stat_into(inbox_path, marker_record[0])) return false;
    for (U32 i = 9; i != max_path; ++i) {
        const char c = path[i];
        if (c != '/' && c) continue;
        path[i] = 0;
        const bool inside = stat_into(path, marker_record[1]) && same_object();
        path[i] = c;
        if (inside) return true;
        if (!c) break;
    }
    return false;
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
    if (!valid_rename(from, to) || reaches_protected(rename_source) || reaches_protected(to)) return error_access;
    if (!stat_into(from, marker_record[1])) return error_noent;
    if (over && (!file_engine::upload_size(from, size) || (marker_record[1][2] & 0xf000u) != 0x8000u ||
                 marker_record[1][7] != size)) return error_inval;
    if (stat_into(to, marker_record[1]) && (!over || (marker_record[1][2] & 0xf000u) != 0x8000u))
        return over ? error_isdir : error_exist;
    const bool marker = stat_into(marker_path, marker_record[0]);
    if (marker && stat_into(from, marker_record[1]) && same_object()) {
        note(0x21, file_engine::refuse_protected_path, 0xfffau);
        return error_access;
    }
    const int r = a_side::rename_object(from, to);
    U32 error = 0;
    if (r == -2) { note(0x21, file_engine::refuse_off, 0xfff7u); error = error_io; }   // spans differ: not called
    else if (r < 0) { error = U32(-int32_t(word(yaffs_error))); if (!error || error > 0xffffu) error = error_io; }
    if (marker && !stat_into(marker_path, marker_record[1])) restore_marker(0x21);
    return error;
}
#endif

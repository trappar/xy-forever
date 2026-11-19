// Part of stock.cpp, included inside its anonymous namespace.
// ---- yaffs: the host folder standing in for /yaffs2 (A) ------------------------------------
struct Handle { bool used = false, directory = false, written = false; int fd = -1; DIR* dir = 0; };
const unsigned max_handles = 64;
Handle handles[max_handles];
int last_error = 0;
uint32_t yaffs_device = 0;
const uint32_t media_write = 0x88a3454au;              // fresh-shape media write callback (dev + 0x80)
int fail(int error) { last_error = error; return -1; }
bool to_host(const char* path, std::string& out, bool stat_parents = false) {
    if (!path || strncmp(path, "/yaffs2", 7) || (path[7] && path[7] != '/')) return false;
    std::string rest = path + 7;
    size_t at = 0;
    while (at < rest.size()) {                         // client requests cannot spell dot components
        size_t end = rest.find('/', at);
        if (end == std::string::npos) end = rest.size();
        const std::string part = rest.substr(at, end - at);
        if (!stat_parents && (part == "." || part == "..")) return false;
        at = end + 1;
    }
    out = std::string(shared->root) + rest;
    return true;
}
bool mounted() { return yaffs_device && r32(yaffs_device + 0xe4) == 1; }
uint32_t selected = 0;                                 // A: 0 user area, 1 or 2 boot partition (stock select)
// A yaffs media change (read-write mount, unmount with its checkpoint, create, write, path change):
// on the device it goes wherever the partition register points. Counted when that is a boot
// partition (the folder still takes it: the count is the evidence).
void media_change(const char* what) {
    if (!selected) return;
    ++shared->boot_writes;
    log("yaffs %s while boot partition %u is selected (the device would write it there)", what, selected);
}
bool read_only() { return r32(yaffs_device + 0xe8) != 0; }
// A media write: allowed only on a read-write mount whose media callbacks are the stock ones.
// A deny gate there (cold_storage begin_fresh) is called as the media would call it.
bool media(int& error) {
    if (!mounted()) { error = 19; return false; }
    if (read_only()) { error = 30; return false; }
    const uint32_t gate = r32(yaffs_device + 0x80);
    if (gate != media_write) {
        if (gate >= 0xc0000000u) function<int()>(gate)();
        error = 5; return false;
    }
    media_change("write");
    return true;
}
// Fault hooks: fail_write = the Nth data write from now fails (EIO); space = bytes left (ENOSPC).
bool write_fault(uint32_t bytes, int& error, bool data = true) {
    const int32_t n = data ? int32_t(shared->fail_write) : 0;
    if (n > 0) {
        shared->fail_write = n - 1;
        if (n == 1) { log("fault: storage write failed"); error = 5; return true; }
    }
    const int64_t space = shared->space;
    if (space >= 0) {
        if (int64_t(bytes) > space) { log("fault: storage full"); error = 28; return true; }
        shared->space = space - bytes;
    }
    return false;
}
bool provider();
int handle_of(int fd) { return fd >= 0 && unsigned(fd) < max_handles && handles[fd].used ? fd : -1; }
int open_file(const char* path, bool write, bool create, bool truncate, bool exclusive) {
    std::string where;
    if (path && strstr(path, "/xy-forever/stage2/")) ++shared->stage2_opens;          // test count
    if (!mounted()) return fail(19);
    if (!to_host(path, where)) return fail(2);
    int error = 0;
    if ((write || create || truncate) && !media(error)) return fail(error);
    if ((create || truncate) && write_fault(0, error, false)) return fail(error);
    struct stat st;
    if (!stat(where.c_str(), &st) && S_ISDIR(st.st_mode)) return fail(21);
    const int flags = (write ? O_RDWR : O_RDONLY) | (create ? O_CREAT : 0) | (truncate ? O_TRUNC : 0) |
                      (exclusive ? O_EXCL : 0);
    const int fd = open(where.c_str(), flags, 0644);
    if (fd < 0) return fail(errno);
    for (unsigned i = 0; i != max_handles; ++i)
        if (!handles[i].used) {
            handles[i] = Handle(); handles[i].used = true; handles[i].fd = fd; handles[i].written = create || truncate;
            if (create || truncate) storage_op_done();
            return int(i);
        }
    close(fd);
    return fail(24);
}
int read_file(int h, uint8_t* out, uint32_t count) {
    if (handle_of(h) < 0 || handles[h].directory) return fail(9);
    const ssize_t n = read(handles[h].fd, out, count);
    return n < 0 ? fail(errno) : int(n);
}
int write_file(int h, const uint8_t* data, uint32_t count) {
    if (handle_of(h) < 0 || handles[h].directory) return fail(9);
    int error = 0;
    if (!media(error) || write_fault(count, error)) return fail(error);
    const ssize_t n = write(handles[h].fd, data, count);
    if (n < 0) return fail(errno);
    handles[h].written = true;
    storage_op_done();
    return int(n);
}
int close_file(int h) {
    if (handle_of(h) < 0) return fail(9);
    if (handles[h].dir) closedir(handles[h].dir);
    if (handles[h].fd >= 0) close(handles[h].fd);
    const bool written = handles[h].written;
    handles[h] = Handle();
    if (written) storage_op_done();
    return 0;
}
int seek_file(int h, int offset, int whence) {
    if (handle_of(h) < 0 || handles[h].directory) return fail(9);
    const off_t at = lseek(handles[h].fd, offset, whence == 1 ? SEEK_CUR : whence == 2 ? SEEK_END : SEEK_SET);
    return at < 0 ? fail(errno) : int(at);
}
int path_op(uint32_t op, const char* path) {          // mkdir 0x26, unlink 0x25, rmdir 0x27, access 0x21
    std::string where;
    if (!mounted()) return fail(19);
    if (!to_host(path, where)) return fail(2);
    if (op == 0x21) { struct stat st; return stat(where.c_str(), &st) ? fail(errno) : 0; }
    int error = 0;
    if (!media(error) || write_fault(0, error, false)) return fail(error);
    // The device's yaffs error numbers (1.2.1 listing 889fa4b0), not the Mac's: a folder for unlink
    // is EISDIR 21 (the Mac: EPERM), a folder not empty ENOTEMPTY 93 (the Mac: 66).
    struct stat st;
    if (op == 0x25 && !lstat(where.c_str(), &st) && S_ISDIR(st.st_mode)) return fail(21);   // yaffs: a link itself
    const int r = op == 0x26 ? mkdir(where.c_str(), 0755) : op == 0x25 ? unlink(where.c_str()) : rmdir(where.c_str());
    if (r) return fail(errno == ENOTEMPTY ? 93 : errno);
    if (op != 0x26 && shared->vanish_marker.exchange(0)) {      // test hook: the marker goes with it
        unlink((std::string(shared->root) + "/bootloader.dev").c_str());
        log("fault: /yaffs2/bootloader.dev vanished with this operation");
    }
    storage_op_done();
    return 0;
}
// The yaffs rename A calls itself (889fa834(old, new) -> yaffsfs rename 889fa604, 1.2.1 listing):
// 0, or -1 with the negative error number in yaffs's error word 0x8806f940 (889f998c stores it, the
// posix wrapper 889f165a negates 889f9982's read of it). The stock checks, in order: either parent
// folder or the source missing ENOENT 2, read-only EROFS 30, a file onto a folder EISDIR 21
// (889f9a4a), a folder onto a file ENOTDIR 20 (889f9a28), onto a folder that holds entries ENOTEMPTY 93
// (88a009c2), a folder into itself EINVAL 22; an existing target is otherwise replaced (yaffs_rename_obj
// 88a01afa). The host's rename(2) gives the same outcomes and numbers but ENOTEMPTY (66 there).
int rename_object(const char* from, const char* to) {
    auto failed = [](int error) { w32(0x8806f940u, uint32_t(-error)); return fail(error); };
    std::string a, b;
    if (!mounted()) return failed(19);
    if (!to_host(from, a) || !to_host(to, b)) return failed(2);
    struct stat st;
    if (stat(a.c_str(), &st)) return failed(2);
    int error = 0;
    if (!media(error) || write_fault(0, error, false)) return failed(error);
    const int32_t n = shared->rename_fault, kind = shared->rename_fault_kind;   // fault hook
    const bool hit = n > 0 && (shared->rename_fault = n - 1) == 0;
    if (hit && kind == 1) { log("fault: rename failed"); return failed(5); }
    if (rename(a.c_str(), b.c_str())) return failed(errno == ENOTEMPTY ? 93 : errno);
    if (hit && kind == 3) { log("fault: A silent after this rename"); shared->a_silent = 1; }
    if (hit && kind == 2) shared->cut_after = 1;                         // the power cut right after it
    storage_op_done();
    return 0;
}
// A's local posix stat (889f3516 on A; the query executor's 0x22 record): device, inode, mode, links,
// uid, gid, rdev, size (+0x1c), change time. Links are followed.
int stat_record(const char* path, uint32_t* out) {
    std::string where;
    if (!provider() || !mounted()) return fail(19);
    // A's protection guard uses stat(dir/..) to follow resolved directory ancestors, as stock YAFFS does.
    // Only this local stat adapter admits those internal lookups; request executors retain strict paths.
    if (!to_host(path, where, true)) return fail(2);
    struct stat st;
    if (stat(where.c_str(), &st)) return fail(errno);
    const uint32_t record[9] = {uint32_t(st.st_dev), uint32_t(st.st_ino), uint32_t(st.st_mode), uint32_t(st.st_nlink),
                                0, 0, 0, uint32_t(st.st_size), uint32_t(st.st_ctime)};
    memcpy(out, record, sizeof(record));
    return 0;
}
const uint32_t dir_handles = 0x8aa98000u, dirents = 0x8aa90000u, dirent_bytes = 0x120u;
uint32_t open_directory(const char* path) {
    std::string where;
    if (!mounted() || !to_host(path, where)) { last_error = 2; return 0; }
    DIR* d = opendir(where.c_str());
    if (!d) { last_error = errno; return 0; }
    for (unsigned i = 0; i != max_handles; ++i)
        if (!handles[i].used) {
            handles[i] = Handle(); handles[i].used = true; handles[i].directory = true; handles[i].dir = d;
            return dir_handles + i * 16;
        }
    closedir(d); last_error = 24;
    return 0;
}
int directory_of(uint32_t handle) {
    const uint32_t i = (handle - dir_handles) / 16;
    return handle >= dir_handles && i < max_handles && handles[i].used && handles[i].directory ? int(i) : -1;
}
uint32_t next_entry(uint32_t handle) {                 // yaffs_dirent: type at +10, name at +11
    const int i = directory_of(handle);
    if (i < 0) { last_error = 9; return 0; }
    for (;;) {
        dirent* e = readdir(handles[i].dir);
        if (!e) return 0;
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        const uint32_t at = dirents + uint32_t(i) * dirent_bytes;
        memset(host(at), 0, dirent_bytes);
        w8(at + 10, e->d_type == DT_DIR ? 4 : e->d_type == DT_LNK ? 10 : 8);   // yaffs reports links (DT_LNK)
        strncpy(static_cast<char*>(host(at + 11)), e->d_name, 255 < dirent_bytes - 12 ? 255 : dirent_bytes - 12);
        return at;
    }
}
int close_directory(uint32_t handle) {
    const int i = directory_of(handle);
    if (i < 0) return fail(9);
    closedir(handles[i].dir);
    handles[i] = Handle();
    return 0;
}
bool any_open() { for (const Handle& h : handles) if (h.used) return true; return false; }


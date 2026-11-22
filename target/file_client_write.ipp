// Part of file_client.cpp, included there at this point: not compiled alone.
// B's file client's write commands (file_client.hpp, XYF_USB_FILE_WRITE): folders, a streamed write, its
// check, removals and the rename with its depth walk.
namespace {
uint32_t failed(const Reply& r, volatile uint32_t& mounted) {
    mounted = mounted_stale;
    return status(r.outcome);
}
}
uint32_t make_folders(const char* path, volatile uint32_t& mounted) {
    // Test 050: create the missing folders below the write area's root (mkdir -p; existing ones fail
    // EEXIST, which is fine: the open after it decides).
    const U32 root_bytes = storage_server::write_root(path);             // 8: /yaffs2/
    char folder[file_engine::max_path];
    for (U32 k = 0; path[k] && k + 1 != file_engine::max_path; ++k) {
        folder[k] = path[k];
        if (path[k] != '/' || k < root_bytes) continue;
        folder[k] = 0;
        const Reply d = mkdir(folder);                                   // op 0x26, mode 0
        if (d.outcome == link || d.outcome == busy || d.outcome == lost) return failed(d, mounted);
        folder[k] = '/';
    }
    return xyf::usbp::s_ok;
}

uint32_t open_write(const char* path, int& fd, volatile uint32_t& mounted) {
    fd = -1;
    if (!storage_server::valid_write_path(path)) return xyf::usbp::s_refused;
    if (mounted == mounted_stale) return xyf::usbp::s_state;            // the caller sends op 9 first
    if (!mounted) {
        const Reply m = mount();                                         // op 8: a's read-only mount
        if (m.outcome) return status(m.outcome);
        if (m.value) return xyf::usbp::s_storage;
        mounted = 1;
    }
    // a opens its read-write window on the first change below and keeps it for the next
    // writes; b closes it (op 9) once no write came for a while (usb_service). Past a request's
    // bound: op 9 before the next file command (a closes b's handles and the window).
    // #144: the open first; the folders (mkdir -p) only when it fails ENOENT, then the open again: an upload's
    // tmp/ is there after the first one, so a file takes one request here instead of one per folder too.
    Reply o = open(path, storage_server::write_open_flags);              // op 0x29, "wb"
    if (o.outcome) return failed(o, mounted);
    if (o.value < 0 && o.error == enoent) {
        const U32 made = make_folders(path, mounted);
        if (made != xyf::usbp::s_ok) return made;
        o = open(path, storage_server::write_open_flags);
        if (o.outcome) return failed(o, mounted);
    }
    if (o.value < 0) return xyf::usbp::s_storage;
    fd = o.value;
    return xyf::usbp::s_ok;
}

uint32_t write_more(int fd, const uint8_t* bytes, uint32_t size, uint32_t& written, volatile uint32_t& mounted) {
    for (U32 done = 0; done != size;) {
        const U32 n = size - done < chunk ? size - done : chunk;
        const Reply w = write(fd, bytes + done, n);                      // op 0x2b
        if (w.outcome) return failed(w, mounted);
        if (w.value != int(n)) return xyf::usbp::s_storage;
        done += n;
        written += n;
    }
    return xyf::usbp::s_ok;
}

uint32_t close_write(int fd, volatile uint32_t& mounted) {
    const Reply c = close(fd);                                           // op 0x2d
    if (c.outcome) return failed(c, mounted);
    return c.value ? xyf::usbp::s_storage : xyf::usbp::s_ok;
}

uint32_t read_crc(const char* path, uint32_t size, uint32_t& total, uint32_t& crc, volatile uint32_t& mounted) {
    total = 0;
    crc = 0;
    Reply o = open(path, 0);                                             // op 0x29, read-only
    if (o.outcome) return failed(o, mounted);
    if (o.value < 0) return xyf::usbp::s_not_found;
    U32 code = xyf::usbp::s_ok;
    uint8_t chunk_bytes[chunk];
    for (U32 guard = 0; guard != (size >> 10) + 4; ++guard) {
        const Reply g = read(o.value, chunk_bytes, chunk);               // op 0x2a
        if (g.outcome) return failed(g, mounted);
        if (g.value < 0) {
            code = xyf::usbp::s_storage;
            break;
        }
        if (!g.value) break;
        crc = xyf::crc::update(crc, chunk_bytes, U32(g.value));
        total += U32(g.value);
    }
    const Reply cl = close(o.value);
    if (cl.outcome) return failed(cl, mounted);
    return code;
}

uint32_t write_file(const char* path, const uint8_t* bytes, uint32_t size, uint32_t crc, uint32_t& written,
                    uint32_t& readback_crc, volatile uint32_t& mounted) {
    written = 0;
    readback_crc = 0;
    // file_write checks the path first; the rest cannot happen from it (staged data). Size 0 (a copy
    // of an empty file) opens, writes nothing and closes.
    if (!bytes) return xyf::usbp::s_refused;
    int fd;
    U32 code = open_write(path, fd, mounted);
    if (code != xyf::usbp::s_ok) return code;
    code = write_more(fd, bytes, size, written, mounted);
    if (mounted == mounted_stale) return code;
    const U32 closed = close_write(fd, mounted);
    if (code == xyf::usbp::s_ok) code = closed;
    if (code != xyf::usbp::s_ok) return code;
    uint32_t total;                                                         // read back to EOF (the open window)
    code = read_crc(path, size, total, readback_crc, mounted);
    if (code == xyf::usbp::s_ok && (total != size || readback_crc != crc)) code = xyf::usbp::s_crc;
    return code;
}

uint32_t copy_target(const char* path, uint32_t& error, volatile uint32_t& mounted) {
    error = 0;
    const uint32_t ready = mount_ready(mounted);                         // stale: op 9 first; op 8
    if (ready != xyf::usbp::s_ok) return ready;
    char parent[file_engine::max_path];
    U32 n = 0, cut = 0;
    for (; path[n] && n + 1 != file_engine::max_path; ++n) {
        parent[n] = path[n];
        if (path[n] == '/') cut = n;
    }
    parent[cut] = 0;
    uint8_t record[record_bytes];
    const Reply t = stat(path, record);                                  // op 0x22, as file.list
    if (t.outcome == refused) return xyf::usbp::s_refused;
    if (t.outcome) {
        mounted = mounted_stale;
        return status(t.outcome);
    }
    if (t.value == 0) {
        error = eexist;
        return xyf::usbp::s_state;
    }
    const Reply p = stat(parent, record);
    if (p.outcome == refused) return xyf::usbp::s_refused;
    if (p.outcome) {
        mounted = mounted_stale;
        return status(p.outcome);
    }
    if (p.value != 0) {
        error = enoent;
        return xyf::usbp::s_not_found;
    }
    return xyf::usbp::s_ok;
}

uint32_t remove_partial(const char* path, volatile uint32_t& mounted) {
    if (mounted == mounted_stale) return 1;                              // a request is still out
    const Reply u = unlink(path);                                        // op 0x25, as FILE_DELETE
    if (u.outcome == done && u.value >= 0) return 0;
    if (u.outcome) {
        if (u.outcome != refused) mounted = mounted_stale;
        return 1;
    }
    uint8_t record[record_bytes];
    const Reply q = stat(path, record);                                  // the unlink failed: is there one?
    if (q.outcome) {
        if (q.outcome != refused) mounted = mounted_stale;
        return 1;
    }
    return q.value == 0 ? 1 : 0;
}

uint32_t change(uint32_t op, const char* path, uint32_t& error, volatile uint32_t& mounted) {
    error = 0;
    if (mounted == mounted_stale) return xyf::usbp::s_state;            // the caller sends op 9 first
    const uint32_t ready = mount_ready(mounted);                         // op 8: a's read-only mount
    if (ready != xyf::usbp::s_ok) return ready;
    const Reply r = op == 0x25 ? unlink(path) : op == 0x27 ? rmdir(path) : mkdir(path);   // a opens its window
    if (r.outcome == refused) return xyf::usbp::s_refused;
    if (r.outcome) {
        mounted = mounted_stale;
        return status(r.outcome);
    }
    if (r.value >= 0) return xyf::usbp::s_ok;
    error = r.error;
    return change_status(error);
}

namespace {
// The depth rule for a folder moved deeper (#124, file_engine.hpp): the target and every folder below
// the source must stay within max_folders after the move. Walks the source's folders only (readdir's
// type: DT_DIR 4; a stat for an entry that says neither folder, file nor link), down to the first
// one too deep, one folder handle at a time (a level resumes after the subfolder it went into).
// s_ok (also when nothing gets deeper or the source is not a folder: the rename answers for it),
// s_refused, else the request's status.
uint32_t deeper_ok(const char* from, const char* to, volatile uint32_t& mounted) {
    const uint32_t s = file_engine::depth(from), t = file_engine::depth(to);
    if (t <= s) return xyf::usbp::s_ok;                                  // nothing gets deeper (both parse)
    auto failed = [&mounted](const Reply& r) -> uint32_t {
        if (r.outcome == link) mounted = mounted_stale;
        return status(r.outcome);
    };
    uint8_t record[record_bytes], entry[entry_bytes + 4];
    const Reply q = stat(from, record);                                  // op 0x22
    if (q.outcome) return failed(q);
    if (q.value != 0 || (xyf::usbp::get32(record + 8) & 0xf000u) != 0x4000u) return xyf::usbp::s_ok;
    if (t > file_engine::max_folders) return xyf::usbp::s_refused;
    const U32 room = file_engine::max_folders - t;                       // folder levels allowed below the target
    char path[file_engine::max_path], child[file_engine::max_path];
    for (U32 k = 0; k != file_engine::max_path; ++k)
        if (!(path[k] = from[k])) break;   // < max_path: it parsed
    U32 skip[file_engine::max_folders + 1], level = 0;
    skip[0] = 0;
    for (;;) {
        const Reply d = opendir(path);                                   // op 0x2e
        if (d.outcome) return failed(d);
        if (!d.value || d.value == -1) return d.error == emfile ? xyf::usbp::s_storage : xyf::usbp::s_not_found;
        U32 code = xyf::usbp::s_ok, seen = 0;
        bool down = false;
        for (; seen != 0x10000u && !down; ++seen) {
            const Reply r = readdir(U32(d.value), entry);                // op 0x2f
            if (r.outcome) return failed(r);
            const U32 got = readdir_result(r.value);
            if (got == folder_end) break;
            if (got == folder_error) {
                code = xyf::usbp::s_storage;
                break;
            }
            if (seen < skip[level]) continue;
            entry[11 + max_name] = 0;
            if (!child_path(path, reinterpret_cast<const char*>(entry + 11), child)) {
                code = xyf::usbp::s_refused;
                break;
            }
            bool folder = entry[10] == 4;
            if (entry[10] != 4 && entry[10] != 8 && entry[10] != 10) {   // not said: stat it
                const Reply e = stat(child, record);
                if (e.outcome) return failed(e);
                folder = e.value == 0 && (xyf::usbp::get32(record + 8) & 0xf000u) == 0x4000u;
            }
            if (!folder) continue;
            if (level + 1 > room) code = xyf::usbp::s_refused;           // this folder would sit too deep
            skip[level] = seen + 1;
            down = true;
        }
        const Reply c = closedir(U32(d.value));                          // op 0x30
        if (c.outcome) return failed(c);
        if (code != xyf::usbp::s_ok) return code;
        if (down) {
            for (U32 k = 0; k != file_engine::max_path; ++k)
                if (!(path[k] = child[k])) break;
            skip[++level] = 0;
            continue;
        }
        if (!level) return xyf::usbp::s_ok;
        U32 cut = 0;                                                     // back up to the parent folder
        for (U32 k = 0; path[k]; ++k)
            if (path[k] == '/') cut = k;
        path[cut] = 0;
        --level;
    }
}
}

uint32_t rename(const char* from, const char* to, uint32_t& error, volatile uint32_t& mounted, bool over) {
    error = 0;
    if (!storage_server::valid_rename(from, to)) return xyf::usbp::s_refused;   // file_rename checks it first
    if (mounted == mounted_stale) return xyf::usbp::s_state;            // the caller sends op 9 first
    const uint32_t ready = mount_ready(mounted);                         // op 8: a's read-only mount
    if (ready != xyf::usbp::s_ok) return ready;
    // #124: a folder moved deeper. #144: not for rename_over, whose source A takes only as a file (an upload).
    const uint32_t deep = over ? U32(xyf::usbp::s_ok) : deeper_ok(from, to, mounted);
    if (deep != xyf::usbp::s_ok) return deep;
    // #144: an upload's rename_over in one request (rename_upload) when its target leaves room for the source's tail.
    char joined[file_engine::max_path];
    const bool one = over && file_engine::join_upload(from, to, joined);
    const U32 tags[2] = {file_engine::rename_from, over ? file_engine::rename_over : file_engine::rename_to};
    const char* paths[2] = {from, to};
    for (U32 i = one ? 1 : 0; i != 2; ++i) {                           // the pair checked above: op_stat here
        const Reply r = one ? path_request(to, file_engine::op_stat, 0x21, file_engine::rename_upload, 0, joined) :
                              path_request(paths[i], file_engine::op_stat, 0x21, tags[i]); // a opens its window here
        if (r.outcome == refused) return xyf::usbp::s_refused;
        if (r.outcome) {
            mounted = mounted_stale;
            return status(r.outcome);
        }
        if (r.value < 0) {
            error = r.error;
            return rename_status(error);
        }
    }
    return xyf::usbp::s_ok;
}

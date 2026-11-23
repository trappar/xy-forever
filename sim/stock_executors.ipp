// Part of sim/stock.cpp, included there at this point (its helpers in scope): not compiled alone.
// ---- A's stock request executors (a pool slot; they queue their own answer) -------------------
void executor(uint32_t address, uint32_t request) {
    xyf_sim::active();
    const uint32_t cls = r32(request), op = r8(request + 5), a0 = r32(request + 8);
    if ((cls == cls_path || cls == cls_query) && shared->swap_armed.exchange(0)) {
        strncpy(static_cast<char*>(host(0x8803f6e0u)), shared->swap_path, 239);
        log("fault: stock path buffer now holds %s", shared->swap_path);
    }
    if (a0 == 0x41535447u) ++shared->stage_executor_hits; // quarantined RAM handle must never reach stock
    const char* path = cls == cls_path || cls == cls_query ? static_cast<const char*>(host(r32(request + 0x18))) : 0;
    if (address == 0x889f76cau && cls == cls_partition) {
        if (op == 0x2c)
            return finish(request, provider() ? seek_file(int(a0), int(r32(request + 0x18)), int(r32(request + 0xc))) :
                                                fail(19));
        if (op == 0x2d) return finish(request, close_file(int(a0)));
        if (op == 0x18) return finish(request, local_select(a0));
        if (op == 0x14) return finish(request, trim(a0) ? 0 : fail(5));
        if (op == 0x30) return finish(request, close_directory(a0));              // MTP: closedir
        if (op == 0x34 || op == 0x36) return finish(request, handle_of(int(a0)) < 0 ? fail(9) : 0); // fsync, truncate
    }
    if (address == 0x889f6feau && cls == cls_path) {
        if (op == 0x29 && (a0 & 3u) && shared->fail_write_open.load() > 0) { // test hook (#195)
            --shared->fail_write_open;
            log("fault: B's open for writing failed (EBADF)");
            return finish(request, fail(9));
        }
        if (op == 0x29) return finish(request, posix_open(path, a0));
        if (op == 0x26 || op == 0x25 || op == 0x27 || op == 0x21)
            return finish(request, provider() ? path_op(op, path) : fail(19));
        // A failed opendir answers 0 (NULL), as the stock client 889f7286 expects (was -1).
        if (op == 0x2e) {
            const uint32_t d = provider() ? open_directory(path) : 0;
            return finish(request, int(d));
        }
        if (op == 0x28 || op == 0x35) return finish(request, provider() ? 0 : fail(19)); // sync, set times
    }
    if (address == 0x889f72e4u && cls == cls_query) { // MTP queries: stat 0x22, statfs 0x23, free space 0x31
        std::string where;
        struct stat st;
        const bool found = provider() && mounted() && to_host(path, where) && !stat(where.c_str(), &st);
        const uint32_t bytes = op == 0x23 ? 0x0cu : op == 0x31 ? 8u : 0x34u;
        memset(host(a_out_buffer), 0, bytes);
        if (op == 0x22 && found) { // the stock yaffs stat record (889fa848)
            w32(a_out_buffer + 4, uint32_t(st.st_ino));
            w32(a_out_buffer + 8, S_ISDIR(st.st_mode) ? 0x41edu : S_ISREG(st.st_mode) ? 0x81a4u : 0xa1ffu);
            w32(a_out_buffer + 0xc, uint32_t(st.st_nlink));
            w32(a_out_buffer + 0x1c, S_ISDIR(st.st_mode) ? 0 : uint32_t(st.st_size));
        }
        if (op == 0x31 && found) {
            struct statvfs fs = {};
            const int64_t quota = shared->space.load();
            const uint64_t free_bytes = quota >= 0                   ? uint64_t(quota) :
                                        !statvfs(where.c_str(), &fs) ? uint64_t(fs.f_bavail) * fs.f_frsize :
                                                                       0;
            w32(a_out_buffer, uint32_t(free_bytes));
            w32(a_out_buffer + 4, uint32_t(free_bytes >> 32));
        }
        int result = op == 0x31 ? 0 : found ? 0 : fail(2);
        if (op == 0x23 && found) {
            struct statvfs fs = {};
            if (shared->statfs_fail || statvfs(where.c_str(), &fs)) result = fail(5);
            else {
                const uint32_t block =
                    shared->statfs_override ? uint32_t(shared->statfs_block_bytes.load()) : uint32_t(fs.f_frsize);
                const int64_t quota = shared->space.load();
                const uint32_t free = shared->statfs_override ? uint32_t(shared->statfs_free_blocks.load()) :
                                      quota >= 0 && block     ? uint32_t(uint64_t(quota) / block) :
                                                                uint32_t(fs.f_bavail);
                w32(a_out_buffer, block);
                w32(a_out_buffer + 4, shared->statfs_override ? 0xffffffffu : uint32_t(fs.f_blocks));
                w32(a_out_buffer + 8, free);
            }
        }
        w32(request + 0x20, a_out_buffer);
        w32(request + 0x24, bytes);
        // Space queries retain YAFFS errno even on success (#12); model that bug faithfully.
        if (op == 0x23 || op == 0x31) {
            w32(request + 0x10, uint32_t(result));
            w32(request + 0x14, uint32_t(last_error));
            a_respond(request);
            return;
        }
        return finish(request, result);
    }
    if (address == 0x889f6cd6u && cls == cls_read && op == 0x2a) {
        // #144: one 0x1000-byte body per run (the builder's chunk). While bytes are left it sends the body and
        // re-posts itself (889f66fc), served again by A's next poll; the last body comes with the answer, whose
        // result is every byte read (the stock fread's count). Bytes so far at +0x28.
        const uint32_t count = r32(request + 0xc), done = r32(request + 0x28);
        const uint32_t want = count > done ? (count - done < 0x1000u ? count - done : 0x1000u) : 0;
        const int n = read_file(int(a0), static_cast<uint8_t*>(host(a_out_buffer)), want);
        w32(request + 0x20, a_out_buffer);
        w32(request + 0x24, n > 0 ? uint32_t(n) : 0);
        if (n > 0 && uint32_t(n) == want && done + want < count) {
            w32(request + 0x28, done + want);
            return a_stream(request);
        }
        return finish(request, n < 0 ? n : int(done) + n);
    }
    if (address == 0x889f6dcau && cls == cls_write) {
        const uint8_t* data = static_cast<const uint8_t*>(host(r32(request + 0x20)));
        const uint32_t bytes = r32(request + 0x24);
        if (op == 0x2b) return finish(request, write_file(int(a0), data, bytes));
        if (op == 0x15 || op == 0x16)
            return finish(request, sectors_io(true, a0, bytes / 512u, const_cast<uint8_t*>(data)) ? 0 : fail(5));
    }
    if (address == 0x889f6b7cu && cls == cls_sector && (op == 0x11 || op == 0x12)) {
        const uint32_t count = op == 0x11 ? 1 : r32(request + 0x18);
        const bool ok = count && count <= a_buffer_bytes / 512u &&
                        sectors_io(false, a0, count, static_cast<uint8_t*>(host(a_out_buffer)));
        w32(request + 0x20, a_out_buffer);
        w32(request + 0x24, ok ? count * 512u : 0);
        return finish(request, ok ? 0 : fail(5));
    }
    if (address == 0x889f6b7cu && cls == cls_sector && op == 0x2f) { // MTP: readdir
        // Stock 889f6bfa..889f6c26: the result is yaffs_readdir's dirent address (was 1, which
        // hid build 165's empty listings), 0 at the end with the posix errno, which earlier calls
        // left (last_error is never cleared either); a 0x10c-byte body either way.
        const uint32_t entry = next_entry(a0);
        w32(request + 0x20, entry ? entry : a_out_buffer);
        w32(request + 0x24, 0x10c);
        if (!entry) memset(host(a_out_buffer), 0, 0x10c);
        w32(request + 0x10, entry);
        w32(request + 0x14, entry ? 0 : uint32_t(last_error));
        return a_respond(request);
    }
    crash("stock executor %08x: class %08x op %02x is not simulated ", address, cls, op);
}

// Part of usb_service.cpp, included there at this point: not compiled alone.
// Before every change (write, delete, rmdir, mkdir, rename, MTP's first change): a request left past
// its bound (stale) is closed first (op 9, which must reach a in RPC mode), then A's write entry. #3: A
// is asked whether it still runs XY Forever (a_serving: a fresh command item, also when an earlier one
// latched read_enabled) BEFORE op 9 goes out, never after: after a lone restart A's bootloader would
// take the op 9 (it has no path policy). Not serving: link, nothing sent.
U32 enter_writes() {
    if (mounted == file_client::mounted_stale) {
        if (!a_serving()) return P::s_link;
        file_client::release(mounted);
    }
    return menu_link::usb_command(storage_server::file_write_item) ? P::s_ok : P::s_link;
}
// Path NUL + 32-byte confirmation (the SHA-256 of the path bytes). The path's length (0: the
// request does not match its path), else 0.
U32 confirmed_path(const uint8_t* payload, U32 length, bool& confirmed) {
    U32 n = 0;
    while (n < length && payload[n]) ++n;
    confirmed = false;
    if (n + 1 + 32 != length || n + 1 > storage_server::max_path) return 0;
    xyf_core::hash::Sha256 h;
    xyf_core::hash::Digest d;
    confirmed = h.update(payload, n) && h.finish(d) && sha_equal(d.bytes, payload + n + 1);
    return n;
}
// FILE_DELETE / FILE_RMDIR / FILE_MKDIR: the path in the write area and through the engine's
// policy (refused; valid_change_path, as FILE_WRITE), its
// confirmation (sha), then one change in A's write window (writes_due: B closes it as after a
// FILE_WRITE). Reply: u32 status, u32 A's error number.
void file_change(uint8_t type, uint8_t seq, const uint8_t* payload, U32 length) {
    const file_engine::Operation policy = type == P::t_delete_file ? file_engine::op_unlink :
                                          type == P::t_remove_dir ? file_engine::op_rmdir : file_engine::op_mkdir;
    const U32 op = type == P::t_delete_file ? 0x25u : type == P::t_remove_dir ? 0x27u : 0x26u;
    const char* path = reinterpret_cast<const char*>(payload);
    bool confirmed;
    uint32_t error = 0;
    U32 code = P::s_ok;
    if (!confirmed_path(payload, length, confirmed)) code = P::s_bad_request;
    else if (!storage_server::valid_change_path(path, policy) || file_engine::settings_record(path))
        code = P::s_refused;                                             // the write area (#65: not settings.txt)
    else if (!confirmed) code = P::s_sha;
    else if ((code = enter_writes()) == P::s_ok) {
        storage_used = 1;
        code = file_client::change(op, path, error, mounted);
        writes_due = 1; writes_ms = now();
    }
    uint8_t p[8]; P::put32(p, code); P::put32(p + 4, error);
    result.last_status = code;
    send(type, seq, p, 8);
}
// FILE_RENAME: source NUL + target NUL + 32-byte confirmation (the SHA-256 of source, NUL,
// target). Both paths in the write area and the pair through the engine's policy (refused; nothing
// sent), its confirmation (sha), then A renames in its write window (writes_due, as FILE_DELETE). The
// target must not exist (state). Reply: u32 status, u32 A's error number.
void file_rename(uint8_t seq, const uint8_t* payload, U32 length) {
    U32 a = 0, b;
    while (a < length && payload[a]) ++a;
    for (b = a + 1; b < length && payload[b]; ++b) {}
    const char* from = reinterpret_cast<const char*>(payload);
    const char* to = from + a + 1;
    uint32_t error = 0;
    U32 code = P::s_ok;
    xyf_core::hash::Sha256 h;
    xyf_core::hash::Digest d;
    if (a >= length || b + 1 + 32 != length || a + 1 > storage_server::max_path || b - a > storage_server::max_path)
        code = P::s_bad_request;
    else if (!storage_server::valid_rename(from, to) || file_engine::settings_record(from) || file_engine::settings_record(to))
        code = P::s_refused;
    else if (!h.update(payload, b) || !h.finish(d) || !sha_equal(d.bytes, payload + b + 1)) code = P::s_sha;
    else if ((code = enter_writes()) == P::s_ok) {
        storage_used = 1;
        code = file_client::rename(from, to, error, mounted);
        writes_due = 1; writes_ms = now();
    }
    uint8_t p[8]; P::put32(p, code); P::put32(p + 4, error);
    result.last_status = code;
    send(P::t_rename_file, seq, p, 8);
}
// FILE_COPY: source NUL + target NUL + 32-byte confirmation (as FILE_RENAME). B copies through
// its staging buffer with requests the device has proven, no new stock call: the target and its folder
// checked (stat, as file.list), the source read whole (as READ_FILE and stage 2's load; a staged PUT is
// discarded), then FILE_WRITE's write and read-back in A's write window (writes_due). After a failed
// write the target is removed (unlink, as FILE_DELETE). Reply: u32 status, u32 bytes read, u32 A's error
// number, u32 partial (1: a partial target may remain).
void file_copy(uint8_t seq, const uint8_t* payload, U32 length) {
    U32 a = 0, b;
    while (a < length && payload[a]) ++a;
    for (b = a + 1; b < length && payload[b]; ++b) {}
    const char* from = reinterpret_cast<const char*>(payload);
    const char* to = from + a + 1;
    uint32_t error = 0, bytes = 0, written = 0, back = 0, partial = 0;
    U32 code = P::s_ok;
    xyf_core::hash::Sha256 h;
    xyf_core::hash::Digest d;
    if (a >= length || b + 1 + 32 != length || a + 1 > storage_server::max_path || b - a > storage_server::max_path)
        code = P::s_bad_request;
    else if (!storage_server::valid_path(from) || !storage_server::valid_write_path(to) || file_engine::settings_record(to) ||
             file_engine::decide_engine(file_engine::op_read, from) != file_engine::allow)
        code = P::s_refused;
    else if (!h.update(payload, b) || !h.finish(d) || !sha_equal(d.bytes, payload + b + 1)) code = P::s_sha;
    else if (!a_serving()) code = P::s_link;
    else if ((code = file_client::copy_target(to, error, mounted)) == P::s_ok) {
        storage_used = 1;
        put_expected = 0; result.put_ok = 0;                             // the staging holds the copy now
        say("Copying a file...", to, "Do not unplug or power off.");
        code = file_client::read_file(from, 0, false, staged, image_capacity, bytes, mounted);
        if (code == P::s_ok && (code = enter_writes()) == P::s_ok) {
            code = file_client::write_file(to, staged, bytes, xyf::crc::update(0, staged, bytes), written, back, mounted);
            if (code != P::s_ok) partial = file_client::remove_partial(to, mounted);
            writes_due = 1; writes_ms = now();
        }
        say(code == P::s_ok ? "File copied and verified." : "File copy FAILED.", to);
    }
    uint8_t p[16]; P::put32(p, code); P::put32(p + 4, bytes); P::put32(p + 8, error); P::put32(p + 12, partial);
    result.last_status = code;
    send(P::t_copy_file, seq, p, 16);
}
// FILE_LIST: path NUL + u32 skip. Reply: u32 status, u32 count, u32 more, entries
// (file_client::list_entry), at most read_range_max reply bytes after the status.
void list_dir(uint8_t seq, const uint8_t* payload, U32 length) {
    U32 n = 0;
    while (n != length && payload[n]) ++n;
    const char* path = reinterpret_cast<const char*>(payload);
    uint32_t count = 0, more = 0, bytes = 0;
    U32 code = P::s_ok;
    if (n + 5 != length || n + 1 > storage_server::max_path) code = P::s_bad_request;
    else if (file_engine::decide_engine(file_engine::op_list, path) != file_engine::allow) code = P::s_refused;
    else if (!a_serving()) code = P::s_link;
    else code = file_client::list(path, P::get32(payload + n + 1), files + 12, read_range_max - 8, count, more, bytes,
                                  mounted);
    P::put32(files, code); P::put32(files + 4, count); P::put32(files + 8, more);
    result.last_status = code;
    send(P::t_list_dir, seq, files, code == P::s_ok ? 12 + bytes : 4);
}

// FILE_WRITE (Test 049): payload = path NUL + 32-byte confirm token (SHA-256
// of the staged PUT data). Writes the staged bytes to path in the write area
// (all of /yaffs2 but the marker and /yaffs2/upgrade, since 1.13) through a's write allowlist, reads the file back and
// compares its CRC-32. a's read-write window stays open for the next
// write; settle_writes closes it. Reply: u32 status, u32 bytes written, u32
// read-back CRC-32.
void file_write(uint8_t seq, const uint8_t* payload, U32 length) {
    U32 code = P::s_ok;
    uint32_t written = 0, back = 0;
    U32 path_bytes = 0;
    while (path_bytes < length && payload[path_bytes]) ++path_bytes;
    const char* path = reinterpret_cast<const char*>(payload);
    if (path_bytes + 1 + 32 != length) code = P::s_bad_request;
    else if (!storage_server::valid_write_path(path) || file_engine::settings_record(path)) code = P::s_refused;
    else if (!result.put_ok || !put_expected) code = P::s_state;
    else if (!sha_equal(payload + path_bytes + 1, put_sha)) code = P::s_sha;
    // Test 066: the mount is shared. A request left past its bound (stale) is closed first
    // (op 9: A closes b's files and its window), after A proved it runs XY Forever (enter_writes).
    else if ((code = enter_writes()) != P::s_ok) {}
    else {
        storage_used = 1;
        say("Writing a library file...", path, "Do not unplug or power off.");
        // Every request bounded. A's
        // window stays open for the next write; settle_writes closes it.
        code = file_client::write_file(path, staged, put_expected, result.put_crc, written, back, mounted);
        writes_due = 1; writes_ms = now();
        say(code == P::s_ok ? "Library file written and verified." : "Library file write FAILED.", path);
    }
    uint8_t p[12]; P::put32(p, code); P::put32(p + 4, written); P::put32(p + 8, back);
    result.last_status = code;
    send(P::t_file_write, seq, p, 12);
}


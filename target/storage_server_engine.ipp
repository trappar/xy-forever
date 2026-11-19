// Part of storage_server.cpp, included there at this point: not compiled alone.
// A's file engine: every request B sends in an engine entry, decided and run (serve_engine).
// Every request in the engine: file_engine::decide_request (with the path policy) first, then
// the class's stock executor, which must be the CRC-checked one (vtable +0x14).
void serve_engine(U32 request, U32 vtable, U32 op, cold_storage::Stock121ReadOnlySession& storage,
                  Stock121PeerSession& session) {
    Result& r = result;
    const bool writable = engine == entry_write || engine == entry_run;
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
    const bool window = writable && mounted && window_open && window_result == U32(cold_storage::Result::ok);
#else
    const bool window = false;
#endif
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
    protect_reset();                                                  // #144: the marker and the inbox: asked anew
#endif
    const U32 path_pointer = vtable == path_class || vtable == file_engine::query_class ? word(request + 0x18) : 0;
    if (path_pointer == path_buffer) {
        for (U32 i = 0; i != max_path; ++i) if (!(path_copy[i] = char(byte(path_buffer + i)))) break;
        word(request + 0x18) = reinterpret_cast<U32>(path_copy);
    }
    const U32 handle = word(request + 8);
#if defined(XYF_USB_FILE_WRITE)
    const char* source = rename_armed ? rename_source : 0;           // the request just before only
    rename_armed = 0;
    // #144: rename_upload carries its source in its path (file_engine::split_upload: path_copy keeps the target).
    if (vtable == path_class && op == 0x21u && handle == file_engine::rename_upload)
        source = word(request + 0x18) == reinterpret_cast<U32>(path_copy) &&     // A's copy of the stock buffer's path
                 file_engine::split_upload(path_copy, rename_source) ? rename_source : 0;
#else
    const char* source = 0;
#endif
    file_engine::State state;
    state.on = engine != entry_none && !engine_refused;
    state.session = engine == entry_run ? window : mounted != 0;      // the write entry opens it lazily
    state.window = window;
    state.fd_known = known(int(handle)); state.fd_space = space();
#if defined(XYF_MTP)
    state.dir_known = dir_known(handle); state.dir_space = dir_space();
#else
    state.dir_known = 0; state.dir_space = 0;                // no folder requests without XYF_MTP
#endif
    state.partition = boot_selected();
    file_engine::Action action;
    for (;;) {
        action = file_engine::decide_request(vtable, op, handle, path_pointer,
            path_pointer == path_buffer ? path_copy : 0, state, source);
#if defined(XYF_USB_FILE_WRITE)
        // The write entry's first change opens the window (the policy has passed:
        // refuse_read_only comes after it); the request is then decided again with the window open.
        if (action == file_engine::refuse_read_only && engine == entry_write && !state.window && open_window(storage)) {
            state.window = true;
            continue;
        }
#endif
        break;
    }
#if defined(XYF_USB_FILE_WRITE)
    if (window_open) window_touched = 1;
#endif
    if (action == file_engine::refuse_raw_layer && engine == entry_run) {
        // b's bootloader (b restarted alone): no answer. run_engine closes storage and restarts
        // a; the request, still in flight on b, is then answered by a's bootloader.
        session.follow(request); ++r.refused; note(op, action, 0xfffbu);
        return;
    }
#if defined(XYF_USB_FILE_WRITE)
    // B's write area on A too, outside the run entry (diag.engine_test, MTP): open for
    // writing, unlink, mkdir and rmdir only where write_root passes and no link reaches the marker or the inbox
    // (reaches_protected; rename: rename_now), whatever B checked.
    const bool change = (action == file_engine::run_open && handle) ||
                        (action == file_engine::run_path && (op == 0x25u || op == 0x26u || op == 0x27u));
    if (change && engine != entry_run && (!write_root(path_copy) || reaches_protected(path_copy))) {   // (on path_copy)
        session.reply(request, 0xffffffffu, error_access); ++r.refused; note(op, file_engine::refuse_outside_path, 0xfff6u);
        return;
    }
#endif
    const U32 executor = action == file_engine::run_file || action == file_engine::run_closedir ? 0x889f76cau :
        action == file_engine::run_open || action == file_engine::run_opendir || action == file_engine::run_path ?
            0x889f6feau :
        action == file_engine::run_query ? 0x889f72e4u : action == file_engine::run_read ? 0x889f6cd6u :
        action == file_engine::run_readdir ? 0x889f6b7cu : action == file_engine::run_write ? 0x889f6dcau : 0;
    if (executor && word(vtable + 0x14) != executor) {
        session.reply(request, 0xffffffffu, error_access); ++r.refused; note(op, file_engine::refuse_class, 0xffffu);
        return;
    }
    switch (action) {
    case file_engine::run_mount: {
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
        const bool ok = engine == entry_run ? open_window(storage) : mount(storage);
#else
        const bool ok = mount(storage);
#endif
        session.reply(request, ok ? 0 : 0xffffffffu, ok ? 0 : held_files() ? error_busy : error_access);   // 0 = mounted
        ++r.executed; note(op, action, ok ? 0 : 0xffffu);
        return;
    }
    case file_engine::run_unmount: {
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
        const bool had = window_open != 0;
        close_engine(storage);
        // As build 154: failure while the window is still open, and when this close's
        // read-only reopen failed (a's storage is then unmounted).
        const bool ok = !window_open && (!had || window_result == U32(cold_storage::Result::ok));
        if (!window_open && engine == entry_write) engine = entry_read;   // Test 066: one write per item
#else
        close_engine(storage);
        const bool ok = true, had = false;
#endif
        // Stock success is 1 (889f7710); 3 when this op 9 closed a window.
        session.reply(request, ok ? (had ? 3 : 1) : 0, ok ? 0 : error_access);
        ++r.executed; note(op, action, mounted ? 0xfffeu : 1);
        return;
    }
    case file_engine::run_file:
        if (op == 0x2d) untrack(int(handle));
        stock<0x889f76cau, void>(request);                                    // queues its own reply
        break;
    case file_engine::run_open: {
        stock<0x889f6feau, void>(request);
        const int opened = int(word(request + 0x10));                         // fd (stock reply value)
        if (opened >= 0) track(opened);
        break;
    }
    case file_engine::run_path: {                                             // access, unlink, mkdir, rmdir
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
        const bool marker = (op == 0x25u || op == 0x27u) && marker_exists();
        if (marker && stat_into(path_copy, marker_record[1]) && same_object()) {
            session.reply(request, 0xffffffffu, error_access);
            ++r.refused; note(op, file_engine::refuse_protected_path, 0xfffau);   // the marker by another name
            return;
        }
        stock<0x889f6feau, void>(request);
        if (marker && !stat_into(marker_path, marker_record[1])) restore_marker(op);
#else
        stock<0x889f6feau, void>(request);
#endif
        break;
    }
#if defined(XYF_USB_FILE_WRITE)
    case file_engine::run_rename_from: {                                      // keep the source
        const bool ok = writable && write_root(path_copy);
        if (ok) { for (U32 i = 0; i != max_path; ++i) if (!(rename_source[i] = path_copy[i])) break; rename_armed = 1; }
        session.reply(request, ok ? 0 : 0xffffffffu, ok ? 0 : error_access);
        ++(ok ? r.executed : r.refused); note(op, action, ok ? 0 : 0xffffu);
        return;
    }
    case file_engine::run_rename_to: case file_engine::run_rename_over: {     // rename it
        const U32 error = rename_now(path_copy, action == file_engine::run_rename_over);
        session.reply(request, error ? 0xffffffffu : 0, error);
        ++r.executed; note(op, action, error ? 0xff00u | error : 0);
        return;
    }
#endif
    case file_engine::run_read: stock<0x889f6cd6u, void>(request); break;     // may re-post itself (889f66fc)
    case file_engine::run_write: stock<0x889f6dcau, void>(request); break;
#if defined(XYF_MTP)
    case file_engine::run_closedir:
        stock<0x889f76cau, void>(request);
        untrack_dir(handle);
        break;
    case file_engine::run_opendir: {
        stock<0x889f6feau, void>(request);
        const U32 dir = word(request + 0x10);                                 // stock DIR pointer, 0 = failed
        if (dir) track_dir(dir);
        break;
    }
    case file_engine::run_query: stock<0x889f72e4u, void>(request); break;
    case file_engine::run_readdir: stock<0x889f6b7cu, void>(request); break;
#endif
    default: {
        const U32 error = action == file_engine::refuse_handle || action == file_engine::refuse_mount ? error_badf :
                          action == file_engine::refuse_full ? error_mfile :
                          action == file_engine::refuse_read_only ? error_rofs : error_access;
#if defined(XYF_MTP)
        if (vtable == file_engine::query_class) {
            // 131: the query class always answers with a body too (stock 889f72e4: 0x22 stat
            // 0x34 bytes, 0x23 statfs 0x0c, 0x31 a 64-bit value 8). Refuse with a zeroed one
            // of that size (stat/statfs -1 with the errno; 0x31 value 0).
            const U32 bytes = op == 0x23u ? 0x0cu : op == 0x31u ? 8u : 0x34u;
            for (U32 i = 0; i != dirent_bytes / 4; ++i) dirent_zero[i] = 0;
            stock<0x889f4f10u, void>(reinterpret_cast<U32>(dirent_zero), bytes);
            word(request + 0x20) = reinterpret_cast<U32>(dirent_zero); word(request + 0x24) = bytes;
            session.reply(request, op == 0x31u ? 0u : 0xffffffffu, error);
            ++r.refused; note(op, action, 0xfffcu);
            return;
        }
        if (vtable == sector_class && op == 0x2fu) {
            // 128: b's readdir always waits for a 0x10c-byte entry (stock 889f6b7c sends one
            // even at the end of a folder). A refusal without it froze b inside its USB
            // interrupt (126/127). Refuse with a zeroed entry, the errno and result -1 (not
            // the stock end of folder, 0, so B's file.list reports it; the stock result is A's
            // dirent address, any nonzero word, so the body travels the same way).
            for (U32 i = 0; i != dirent_bytes / 4; ++i) dirent_zero[i] = 0;
            stock<0x889f4f10u, void>(reinterpret_cast<U32>(dirent_zero), dirent_bytes);   // as stock (cache)
            word(request + 0x20) = reinterpret_cast<U32>(dirent_zero); word(request + 0x24) = dirent_bytes;
            session.reply(request, 0xffffffffu, error);
            ++r.refused; note(op, action, 0xfffdu);
            return;
        }
#endif
        session.reply(request, 0xffffffffu, error);
        ++r.refused; note(op, action, 0xffffu);
        return;
    }
    }
    ++r.executed; note(op, action, word(request + 0x10));
}

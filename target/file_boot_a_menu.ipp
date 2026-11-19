// A owns the file boot session and its staging scratch. Included by file_boot.cpp.
bool role0_menu_boot(U32& bytes, U32& crc) {
    Result& r = result;
    Stock121PeerSession session(r.diagnostic);         // zeroes the diagnostic
#if defined(XYF_MENU_LINK)
    session.stage(product_role0_idle_ms, handshake_ms);
#else
    session.stage(role0_idle_ms, handshake_ms);
#endif
#if defined(XYF_ONE_SESSION)
    // Test 065: one link session per boot. The storage server answers b's file requests in
    // this session (RPC mode) on the same storage session the library scan and boot use.
    cold_storage::Stock121ReadOnlySession storage(r.storage);
    storage_server::install(session, storage);
    SessionResult status = session.begin(services::Phase::cold_after_teardown,
        reinterpret_cast<Byte*>(scratch), scratch_bytes, 0, 0, now(), xyf_boot::role0_cap_ms);
    r.begin_result = U32(status);
#else
    SessionResult status = session.begin(services::Phase::cold_after_teardown,
        reinterpret_cast<Byte*>(scratch), scratch_bytes, 0, 0, now(), xyf_boot::role0_cap_ms);
    r.begin_result = U32(status);
    // Storage nests inside the link session: it unmounts before the session closes.
    cold_storage::Stock121ReadOnlySession storage(r.storage);
#endif
    bool mounted = false;
    xyf::library::Library& lib = *reinterpret_cast<xyf::library::Library*>(xyf_boot::library_buffer);
    xyf::library::detail::reset(lib);
    U32 catalog_bytes = 0;
    if (running(status)) {
#if defined(XYF_MENU_ON_FAILURE)
        r.timing.a_mount_start_ms = now();
#endif
#if defined(XYF_ONE_SESSION)
        const U32 mount_start = now();
#endif
        r.storage_begin = U32(storage.begin_fresh(services::Phase::cold_after_teardown));
#if defined(XYF_MENU_ON_FAILURE)
        r.timing.a_mount_ms = now() - r.timing.a_mount_start_ms;
        r.timing.valid |= xyf_boot::timing_mount;
        const U32 scan_start = now();
#endif
        mounted = r.storage_begin == U32(cold_storage::Result::ok);
#if defined(XYF_ONE_SESSION)
        // Test 065: the server shares this mount. 191: also when it failed (op 8 answers a failure
        // until a retry mounts; never a second begin_fresh of its own).
        storage_server::adopt();
        a_note_mount(storage, r.storage_begin, now() - mount_start);
#endif
        const bool ready = mounted && storage.direct_ready();
        if (ready) {
            const xyf::library::Error error = xyf::library::scan_direct(storage, lib,
                reinterpret_cast<Byte*>(xyf_boot::scan_buffer), xyf_boot::scan_capacity);
            r.menu.scan_error = U32(error);
            if (error != xyf::library::Error::ok) xyf::library::detail::reset(lib);
        } else r.menu.scan_error = 0xffu;
        r.library.count = lib.count; r.library.skipped = lib.skipped;
        catalog_bytes = xyf::catalog::encode(lib, reinterpret_cast<Byte*>(xyf_boot::catalog_buffer),
                                             xyf_boot::catalog_capacity, XYF_A_STORAGE_WORD);
        r.menu.catalog_bytes = catalog_bytes;
        r.storage_ms = now();
#if defined(XYF_MENU_ON_FAILURE)
        r.timing.a_scan_ms = now() - scan_start;
        r.timing.a_ready_ms = r.storage_ms;
        r.timing.valid |= xyf_boot::timing_scan;
#endif
        session.touch(now());
    }
    U32 handled = 0;
    while (running(status)) {
#if defined(XYF_ONE_SESSION)
        if (session.follow_now(now())) a_follow(storage);   // F1: b's bootloader asks
#endif
#if defined(XYF_ONE_SESSION) && (defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE))
        // 192: B took the catalog whose word says "repair pending" and asks again at once: repair
        // before that ask is answered, so B waits for it as for the first mount (its menu runs).
        if (a_repair == xyf_boot::repair_pending && a_failure_served && !mounted && !session.blob_pending()) {
            a_failure_served = 0;
            mounted = a_repair_mount(storage);
            catalog_bytes = a_rescan(storage, mounted, lib);
            session.touch(now());
        }
#endif
#if defined(XYF_ONE_SESSION) && defined(XYF_USB_FILE_WRITE)
        // The write window closes after its idle time; a library it changed is scanned again
        // (b fetches the new catalog after it closed the window itself, or at its next catalog item).
        storage_server::tick(storage, now());
        if (storage_server::library_changed()) { catalog_bytes = a_rescan(storage, mounted, lib); session.touch(now()); }
#endif
        if (session.agreed()) {                         // unmount first, then let it close
            if (mounted) { a_close_files(storage); r.storage_end = U32(storage.end()); mounted = false; }
            session.allow_close();
        }
        status = session.poll(now());
        ++r.polls;
        if (!r.link_ms && r.diagnostic.link_initialized) r.link_ms = now();
        if (!running(status) || session.commands_seen() == handled) continue;
        handled = session.commands_seen();
        const U32 command = session.last_command(), item = (command >> 8) & 0xffu;
        r.menu.commands_seen = handled; r.menu.last_command = command;
#if defined(XYF_TFW_CHECK)
        // A staging can never become a normal BOOT/default prepared image. Every new
        // command revokes its ownership before any rescan, default plan or engine use.
        if (storage_server::discard_install_stage()) continue; // refuse this competing command, too
#endif
#if defined(XYF_ONE_SESSION) && defined(XYF_USB_FILE_WRITE)
        // Every command item but reads, writes and the USB switch closes the write window
        // first (catalog, check, boot, set default, install, dry run, reboot, run entry).
        if (item != storage_server::command_item && item != storage_server::file_write_item &&
            item != storage_server::usb_mode_item) {
            storage_server::end_writes(storage);
            if (storage_server::library_changed()) catalog_bytes = a_rescan(storage, mounted, lib);
        }
#endif
#if defined(XYF_ONE_SESSION)
        // Test 065: file-access items switch to RPC mode (the storage server latches its own
        // read / install / write modes from the same items); transfer items switch back.
        if (item == storage_server::file_write_item) a_prepared = 0xffffffffu;
        if (item == storage_server::command_item || item == storage_server::install_item ||
            item == storage_server::file_write_item) { session.set_rpc(true); continue; }
        if (item == xyf_boot::item_catalog || item == xyf_boot::item_check_default ||
            item == xyf_boot::item_boot_default ||
            (item >= xyf_boot::item_check_entry && item < xyf_boot::item_check_entry + xyf::library::max_entries) ||
            (item >= xyf_boot::item_boot_entry && item < xyf_boot::item_boot_entry + xyf::library::max_entries))
            session.set_rpc(false);
        // b reboots (target chosen by b's marker).
        if (item == storage_server::reboot_item) {
            a_answer_out(session);
            storage_server::finish(storage);
            if (mounted) { a_close_files(storage); r.storage_end = U32(storage.end()); mounted = false; }
            usb_service::reset_self();
        }
        if (item == storage_server::dry_run_item) { usb_service::a_dry_run(); continue; }
#if defined(XYF_MTP)
        if (item == storage_server::engine_run_item) {  // the engine's run entry until b's leave item (097)
            a_prepared = 0xffffffffu;
            session.set_rpc(true);                      // b's stock file requests reach the serve hook
            storage_server::run_engine(session, storage);
            handled = session.commands_seen();          // the leave item was consumed in run_engine
            // 139: the run entry may have changed the library: rescan before b's next command (b asks
            // for the catalog right after the leave item and returns to its menu).
            catalog_bytes = a_rescan(storage, mounted, lib);
            session.touch(now());
            continue;
        }
#endif
        if (xyf::state_write::collect(a_collector, command)) {
            a_prepared = 0xffffffffu;
            usb_service::a_set_default(storage, a_collector.hash);
            // Refresh even after a failed write: the newer slot may have reached
            // storage before a later verification/remount failure was reported.
            xyf::library::detail::reset(lib);
            r.menu.scan_error = 0xffu;
            if (mounted && storage.direct_ready()) {
                const xyf::library::Error error = xyf::library::scan_direct(storage, lib,
                    reinterpret_cast<Byte*>(xyf_boot::scan_buffer), xyf_boot::scan_capacity);
                r.menu.scan_error = U32(error);
                if (error != xyf::library::Error::ok) xyf::library::detail::reset(lib);
            }
            r.library.count = lib.count; r.library.skipped = lib.skipped;
            catalog_bytes = xyf::catalog::encode(lib, reinterpret_cast<Byte*>(xyf_boot::catalog_buffer),
                                                 xyf_boot::catalog_capacity, XYF_A_STORAGE_WORD);
            r.menu.catalog_bytes = catalog_bytes;
            session.touch(now());
            continue;
        }
        if (item == storage_server::usb_mode_item) continue;
#endif
        if (item == xyf_boot::item_catalog) {
#if defined(XYF_ONE_SESSION)
            if (!mounted && a_failure_served) {         // 191: B saw A's mount fail and asks again
                a_failure_served = 0;
                mounted = a_retry_mount(storage);
                catalog_bytes = a_rescan(storage, mounted, lib);
                session.touch(now());
            }
#endif
            if (session.freeze_blob(reinterpret_cast<const Byte*>(xyf_boot::catalog_buffer), catalog_bytes) ==
                SessionResult::ok) r.menu.catalog_ok = 1;
#if defined(XYF_ONE_SESSION)
            if (!mounted) a_failure_served = 1;
#endif
            continue;
        }
#if defined(XYF_STAGE2)
        if (item == xyf_boot::item_stage2) {
            a_serve_stage2(session, storage, mounted);
            continue;
        }
#endif
#if defined(XYF_STATE_WRITE)
        if (item >= xyf::state_write::item_set_default &&
            item < xyf::state_write::item_set_default + xyf::library::max_entries) {
            // The only storage write: next A/B state slot, then rescan so the
            // next catalog shows the result. The image boot path is untouched.
            using namespace xyf::state_write;
            U32 status = status_not_mounted, seq = 0;
            services::U32 step = 0;
            U32 write_result = 0;
            if (mounted && storage.direct_ready()) {
                Plan& plan = *reinterpret_cast<Plan*>(xyf_boot::plan_buffer);
                if (!plan_default(lib, item - item_set_default, plan)) status = status_plan;
                else {
                    seq = plan.seq;
                    const cold_storage::Result w = storage.write_state_slot(plan.path,
                        reinterpret_cast<const Byte*>(plan.text), plan.length,
                        reinterpret_cast<Byte*>(xyf_boot::readback_buffer), step);
                    write_result = U32(w);
                    status = w == cold_storage::Result::ok ? status_ok : status_storage;
                }
                if (storage.direct_ready()) {
                    const xyf::library::Error error = xyf::library::scan_direct(storage, lib,
                        reinterpret_cast<Byte*>(xyf_boot::scan_buffer), xyf_boot::scan_capacity);
                    if (error != xyf::library::Error::ok) xyf::library::detail::reset(lib);
                    catalog_bytes = xyf::catalog::encode(lib, reinterpret_cast<Byte*>(xyf_boot::catalog_buffer),
                                                         xyf_boot::catalog_capacity);
                }   // else: remount failed; boot commands refuse, end() still runs below
            }
            r.menu.state_write = status | (write_result & 0xffu) << 8 | (step & 0xffu) << 16 | (seq & 0xffu) << 24;
            session.touch(now());
            continue;
        }
#endif
#if defined(XYF_MENU_ON_FAILURE)
        // Test 059: CHECK (default or entry) reads, matches and admits the image while
        // storage stays mounted, and answers with a status blob; the following BOOT of the
        // same index unmounts and serves the staged image. A failed check leaves the
        // session and the mount open, so b can show the menu and try another entry.
        const bool check = item == xyf_boot::item_check_default ||
            (item >= xyf_boot::item_check_entry && item < xyf_boot::item_check_entry + xyf::library::max_entries);
        const bool boot = item == xyf_boot::item_boot_default ||
            (item >= xyf_boot::item_boot_entry && item < xyf_boot::item_boot_entry + xyf::library::max_entries);
        if (!check && !boot) continue;
        // Test 061: a blob (catalog / check status) still being restored must finish
        // before the next freeze (bounded; harmless when nothing is pending).
        { const U32 t = now(); while (session.blob_pending() && now() - t < 2000u) session.poll(now()); }
        U32 index = check ? item - xyf_boot::item_check_entry : item - xyf_boot::item_boot_entry;
        if (item == xyf_boot::item_check_default || item == xyf_boot::item_boot_default) index = a_default_index(lib);
        if (check) {
            U32 reason = a_prepare(storage, mounted, lib, index, bytes, crc);
            if (reason == reason_no_library) reason = a_content_reason(lib, index);   // the menu says why
            a_prepared = reason == reason_ok ? index : 0xffffffffu;
            xyf_boot::CheckStatus& s = *reinterpret_cast<xyf_boot::CheckStatus*>(xyf_boot::status_buffer);
            for (U32 i = 0; i != sizeof(s); ++i) reinterpret_cast<volatile Byte*>(&s)[i] = 0;
            s.magic = xyf_boot::check_magic; s.reason = reason; s.index = index;
            s.load_reason = r.load_reason; s.admit_reason = r.admit_reason;
            s.timing = r.timing;
            if (index < lib.count)
                for (U32 i = 0; i != 31 && lib.entries[index].id[i]; ++i) s.id[i] = lib.entries[index].id[i];
            session.freeze_blob(reinterpret_cast<const Byte*>(xyf_boot::status_buffer), sizeof(s));
            session.touch(now());
            continue;
        }
        U32 reason = a_prepared == index ? U32(reason_ok) : a_prepare(storage, mounted, lib, index, bytes, crc);
        a_prepared = 0xffffffffu;
        if (mounted) { a_close_files(storage); r.storage_end = U32(storage.end()); mounted = false; }
        r.storage_safe = storage.safe_to_handoff() && r.storage_end == U32(cold_storage::Result::ok);
        if (reason == reason_ok && !r.storage_safe) reason = reason_unmount;
        r.load_reason = reason == reason_ok ? r.load_reason : reason;
        xyf_core::hash::Digest digest;
        bool serve = false;
        if (reason == reason_ok) {
            // crc: a_prepare's CRC of exactly these staged bytes (checked against info.txt).
            const U32 freeze_start = now();
            serve = session.freeze_image(reinterpret_cast<const Byte*>(staging), bytes, crc, digest) == SessionResult::ok;
            r.timing.a_freeze_ms = now() - freeze_start; r.timing.valid |= xyf_boot::timing_freeze;
            if (serve) {
                for (U32 i = 0; i != 8; ++i)
                    r.digest[i] = digest.bytes[4 * i] | U32(digest.bytes[4 * i + 1]) << 8 |
                                  U32(digest.bytes[4 * i + 2]) << 16 | U32(digest.bytes[4 * i + 3]) << 24;
                r.image_bytes = bytes; r.image_crc = crc;
            }
        }
#else
        if (item != xyf_boot::item_boot_default &&
            (item < xyf_boot::item_boot_entry || item >= xyf_boot::item_boot_entry + xyf::library::max_entries)) continue;
        // Boot: pick, read, check against info.txt, unmount, admit, hash, freeze.
        U32 index = item - xyf_boot::item_boot_entry;
        if (item == xyf_boot::item_boot_default) {
            lib.state.once[0] = 0;                      // no "once" while read-only
            uint32_t choice = 0;
            index = xyf::library::boot_choice(lib, choice) == xyf::library::Choice::menu ? 0xffu : U32(choice);
        }
        r.library.index = index;
        xyf::cold_library::Selection selection;
        U32 reason = reason_no_library;
        if (mounted && storage.direct_ready() && entry_selection(lib, index, selection)) {
            const char* id = lib.entries[index].id;
            for (U32 i = 0; i != 31 && id[i]; ++i) r.library.id[i] = id[i];
            r.library.expected_bytes = selection.bytes; r.library.expected_crc = selection.crc32;
            r.library.flags = (selection.has_bytes ? 1u : 0u) | (selection.has_crc32 ? 2u : 0u);
            reason = read_image(storage, selection.path, bytes);
            if (reason == reason_ok) {
                crc = crc32(reinterpret_cast<const volatile Byte*>(staging), bytes);
                const bool matched = xyf::cold_library::matches(selection, bytes, crc);
                r.library.flags |= matched ? 4u : 0u;
                if (!matched) reason = reason_mismatch;
            }
        }
        if (mounted) { a_close_files(storage); r.storage_end = U32(storage.end()); mounted = false; }
        r.storage_safe = storage.safe_to_handoff() && r.storage_end == U32(cold_storage::Result::ok);
        if (reason == reason_ok && !r.storage_safe) reason = reason_unmount;
        r.load_reason = reason;
        xyf_core::hash::Digest digest;
        bool serve = false;
        if (reason == reason_ok) {
            r.admit_reason = admit(reinterpret_cast<Byte*>(staging), bytes);
            if (r.admit_reason == reason_ok) {
                serve = session.freeze_image(reinterpret_cast<const Byte*>(staging), bytes, crc, digest) == SessionResult::ok;
                if (serve) {
                    for (U32 i = 0; i != 8; ++i)
                        r.digest[i] = digest.bytes[4 * i] | U32(digest.bytes[4 * i + 1]) << 8 |
                                      U32(digest.bytes[4 * i + 2]) << 16 | U32(digest.bytes[4 * i + 3]) << 24;
                    r.image_bytes = bytes; r.image_crc = crc;
                }
            }
        }
#endif
        if (!serve) { session.refuse(); r.menu.refused = 1; }
        r.hash_ms = now();
        session.touch(now());
    }
#if defined(XYF_TFW_CHECK)
    storage_server::discard_install_stage(); // session end, including failed/unmounted storage
#endif
    if (mounted) { a_close_files(storage); r.storage_end = U32(storage.end()); mounted = false; }
    r.poll_result = U32(status);
    if (status != SessionResult::ready) r.end_result = U32(session.end());
    r.declined = session.declined();
    r.menu.blobs = session.blobs();
    return session.committed();
}


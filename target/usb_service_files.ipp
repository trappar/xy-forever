// Part of usb_service.cpp, included there at this point: not compiled alone.
// READ_FILE: payload "/yaffs2/..." NUL [u32 offset, u32 length]. Reply: u32
// status, then data: the whole file (<= max_data), or the range (length <=
// read_range_max; fewer bytes = end of file).
void read_file(uint8_t seq, const uint8_t* payload, U32 length) {
    U32 path_bytes = 0;
    while (path_bytes != length && payload[path_bytes]) ++path_bytes;
    const bool ranged = path_bytes + 9 == length;                  // NUL + u32 offset + u32 length
    if (length < 9 || path_bytes + 1 > storage_server::max_path || (path_bytes + 1 != length && !ranged)) {
        status_only(P::t_read_file, seq, P::s_bad_request); return;
    }
    const U32 offset = ranged ? P::get32(payload + path_bytes + 1) : 0;
    const U32 want = ranged ? P::get32(payload + path_bytes + 5) : P::max_data;
    if (!want || want > (ranged ? read_range_max : P::max_data)) { status_only(P::t_read_file, seq, P::s_bad_request); return; }
    if (!a_serving()) { status_only(P::t_read_file, seq, P::s_link); return; }
    uint32_t bytes = 0;
    file_client::Open left;
    const U32 code = file_client::read_file(reinterpret_cast<const char*>(payload), offset, ranged, files + 4, want,
                                            bytes, mounted, &left);
    ++result.reads; result.read_bytes += bytes;
    P::put32(files, code);
    result.last_status = code;
    send(P::t_read_file, seq, files, 4 + (code == P::s_ok ? bytes : 0));
    // The probe and close after the reply, while the host sends its next request (this pays
    // for the a_serving() check); past its bound either marks the mount stale (op 9 before the next open).
    file_client::finish_read(left, mounted);
}

// Test 054: the service screen (b's display); no-op without XYF_SERVICE_SCREEN,
// and before the first HELLO of this boot: a boot without the host tool never
// draws (normal use is unchanged even if drawing were ever to fail).
volatile U32 screen_on __attribute__((section(".data"))) = 0;
volatile U32 home __attribute__((section(".data"))) = 0;             // Test 065: our reboot -> the menu is home
volatile U32 home_greeted __attribute__((section(".data"))) = 0;
volatile U32 menu_usb __attribute__((section(".data"))) = 0;     // Test 058: USB kept up for the menu
void say(const char* a, const char* b = "", const char* c = "", const char* d = "") {
#if defined(XYF_SERVICE_SCREEN)
    if (screen_on) key_ui::service_screen(a, b, c, d);
#else
    (void)a; (void)b; (void)c; (void)d;
#endif
}

// SCREEN (Test 053): reply u32 status, u32 framebuffer address, then up to
// read_range_max bytes of the framebuffer at offset. Reads memory only.
void screen(uint8_t seq, const uint8_t* payload, U32 length) {
    const U32 fb = word(screen_pointer);
    const U32 offset = length == 4 ? P::get32(payload) : screen_bytes;
    if (fb < 0x80000000u || fb > 0x90000000u - screen_bytes || (fb & 1u) || offset >= screen_bytes) {
        uint8_t p[8]; P::put32(p, P::s_range); P::put32(p + 4, fb);
        send(P::t_screen, seq, p, 8); return;
    }
    const U32 n = screen_bytes - offset < read_range_max ? screen_bytes - offset : read_range_max;
    lines(fb + offset, n, true);                       // the display path writes it by DMA/cache
    P::put32(files, P::s_ok); P::put32(files + 4, fb);
    for (U32 i = 0; i != n; ++i) files[8 + i] = byte(fb + offset + i);
    send(P::t_screen, seq, files, 8 + n);
}

// Read-only diagnostics: compare stock code in memory with the authenticated image (CRC
// spans), and read the stock interrupt tables (L1 data). Never L1 instruction memory
// (data loads from it stall the processor) or anything else.
bool within(U32 address, U32 bytes, U32 start, U32 end) { return address >= start && address <= end - bytes; }
bool peek_allowed(U32 address, U32 bytes) {
    return bytes && bytes <= read_range_max &&
           (within(address, bytes, 0x88000000u, 0x88b00000u) ||
            within(address, bytes, 0x08000000u, 0x08040000u) ||
            within(address, bytes, 0x11b00000u, 0x11b02000u) ||  // L1 data: stock IRQ/driver tables
            within(address, bytes, 0x8bfff000u, 0x8bfff800u) ||  // 111: the reboot-marker page
            within(address, bytes, 0x8b810000u, 0x8b811000u));   // the menu's catalog (xyf_boot catalog_buffer)
}
// 095/096: interrupt-controller state, 32-bit reads only, and only registers the stock
// bootloader itself reads or writes: SEC0 GCTL/GSTAT, FCTL/FSTAT, core interface 0
// CCTL/CSTAT, SCTL/SSTAT of sources 0..0x4f (stock uses up to 0x4e); core IMASK/IPEND/ILAT.
// 095 allowed SCTL of sources 0..127: one 1 KiB read of them hung b (2026-11-13), most
// likely on a source the BF707 does not implement.
bool mmr_allowed(U32 address, U32 bytes) {
    return bytes && bytes <= read_range_max && !(address & 3u) && !(bytes & 3u) &&
           (within(address, bytes, 0x20004000u, 0x20004008u) || within(address, bytes, 0x20004010u, 0x20004018u) ||
            within(address, bytes, 0x20004400u, 0x20004408u) || within(address, bytes, 0x20004800u, 0x20004a80u) ||
            within(address, bytes, 0x1fc02104u, 0x1fc02110u) ||
            // 101: RCU0 registers the stock reset 11a03518 writes: CTL/STAT, 0x1c, 0x68
            within(address, bytes, 0x20000000u, 0x20000008u) || within(address, bytes, 0x2000001cu, 0x20000020u) ||
            within(address, bytes, 0x20000068u, 0x2000006cu) ||
            within(address, bytes, 0x20046000u, 0x2004600cu));   // 106: WDOG0 CTL/CNT/STAT only (105 allowed +0xc: a read hung b)
}
void peek(uint8_t seq, const uint8_t* payload, U32 length) {
    const U32 address = length == 8 ? P::get32(payload) : 0, bytes = length == 8 ? P::get32(payload + 4) : 0;
    const bool mmr = mmr_allowed(address, bytes);
    if (!mmr && !peek_allowed(address, bytes)) { status_only(P::t_peek, seq, P::s_range); return; }
    P::put32(files, P::s_ok); P::put32(files + 4, address);
    if (mmr) for (U32 i = 0; i != bytes; i += 4) P::put32(files + 8 + i, word(address + i));
    else for (U32 i = 0; i != bytes; ++i) files[8 + i] = byte(address + i);
    send(P::t_peek, seq, files, 8 + bytes);
}

#if defined(XYF_MTP)
// 097: MTP self-test: MTP's storage calls from b's main loop (each one request to a, served by the
// engine's run entry), each step on the screen first. Through B's file client (every request
// bounded; a link error ends the test with link).
void mtp_step(uint32_t n) {
    static const char* const names[] = {"", "1 mount (op 8)", "2 storage info (0x23)", "3 object info (0x22)",
        "4 open folder (0x2e)", "5 list folder (0x2f)", "6 close folder (0x30)", "7 open/read/close file",
        "8 idle with the window open", "9 info again (0x22, 0x23)", "10 unmount (op 9)"};
    say("MTP self-test: running step", n < 11 ? names[n] : "?", "If this stays, power-cycle", "and report the step.");
}
// Results, in the reply's order.
struct MtpSelfTest { U32 step, mounted, space, stat, dir, entries, closedir, fd, read, closed,
                     wait_ms, stat2, space2, unmounted, entry_bytes, reserved; };
// The value of one client request into a result word; false when a did not answer (the test ends:
// link). A path the engine's policy refuses is not sent: its result is -1 (no folder: 0).
bool mtp_value(const file_client::Reply& r, U32& out, U32 refused = ~0u) {
    if (r.outcome == file_client::refused) { out = refused; return true; }
    out = U32(r.value);
    return r.outcome == file_client::done;
}
// One step of MTP's storage calls (results into t). 1 mount, 2 storage info, 3 object info,
// 4 open folder, 5 list it, 6 close it, 7 open/read/close a file, 9 info again, 10 unmount.
bool mtp_do(U32 n, MtpSelfTest& t, uint8_t* out) {
    namespace C = file_client;
    t.step = n;
    U32 v = 0;
    if (n == 1) {                                                         // op 8: a opens the read-write window
        if (!mtp_value(C::mount(), v)) return false;
        t.mounted = v == 0;
    } else if (n == 2) {
        return mtp_value(C::statfs("/yaffs2/", out), t.space);           // op 0x23 (storage info)
    } else if (n == 3 || n == 9) {
        if (!mtp_value(C::stat("/yaffs2/xy-forever", out), n == 3 ? t.stat : t.stat2)) return false;   // op 0x22
        if (n == 9) return mtp_value(C::statfs("/yaffs2/", out), t.space2);
    } else if (n == 4) {
        return mtp_value(C::opendir("/yaffs2/"), t.dir, 0u);             // op 0x2e (object handles)
    } else if (n == 5 && t.dir) {
        for (U32 k = 0; k != 64; ++k) {
            if (!mtp_value(C::readdir(t.dir, out), v)) return false;    // op 0x2f
            if (C::readdir_result(int32_t(v)) != C::folder_entry) break;   // file.list's reading
            ++t.entries;
        }
    } else if (n == 6 && t.dir) {
        if (!mtp_value(C::closedir(t.dir), t.closedir)) return false;   // op 0x30
        t.dir = 0;
    } else if (n == 7) {
        if (!mtp_value(C::open("/yaffs2/xy-forever/state-a.txt", 0), t.fd)) return false;   // op 0x29, O_RDONLY
        if (int(t.fd) >= 0) {
            if (!mtp_value(C::read(int(t.fd), out, 0x100u), t.read)) return false;      // op 0x2a
            if (!mtp_value(C::close(int(t.fd)), t.closed)) return false;                // op 0x2d
        }
    } else if (n == 10) {
        if (!mtp_value(C::unmount(), v)) return false;                  // op 9: a closes the window
        t.unmounted = v == 1 || v == 3;                                  // stock success is 1 (3 closed the window)
    }
    return true;
}
bool mtp_selftest(MtpSelfTest& t, U32 hold_ms, uint8_t* out) {
    for (U32 n = 1; n != 8; ++n) {
        mtp_step(n);
        if (!mtp_do(n, t, out)) return false;
        if (n == 1 && !t.mounted) return true;
    }
    mtp_step(8);                                                          // window open, a idle (as MTP between requests)
    const U32 start = now();
    while (now() - start < hold_ms) {}
    t.wait_ms = now() - start;
    for (U32 n = 9; n != 11; ++n) { mtp_step(n); if (!mtp_do(n, t, out)) return false; }
    t.step = 11;
    return true;
}
U32 mtp_enter() {
    if (storage_server::engine_identity() || file_client::identity()) return P::s_identity;
    if (mounted && !a_serving()) return P::s_link;    // #3: A is XY Forever before op 9, also when latched
    file_client::release(mounted);
    if (!menu_link::usb_command(storage_server::engine_run_item)) return P::s_link;
    storage_used = 1;                                  // the window opens: CONTINUE needs a REBOOT
    return P::s_ok;
}
U32 mtp_leave(MtpSelfTest& t) {
    if (t.mounted && !t.unmounted) mtp_do(10, t, files + 0x1000);
    read_enabled = 0;                                  // the next read latches read mode again
    return menu_link::usb_command(storage_server::engine_leave_item) ? P::s_ok : P::s_link;
}
void mtp_test(uint8_t seq, const uint8_t* payload, U32 length) {
    MtpSelfTest t = {};
    U32 code = P::s_ok;
    const U32 hold = length == 4 ? P::get32(payload) : 0;
    if (length != 4 || hold > 60000u) code = P::s_bad_request;
    else if ((code = mtp_enter()) == P::s_ok) {
        const bool answered = mtp_selftest(t, hold, files + 0x1000);
        code = mtp_leave(t);
        if (!answered) code = P::s_link;
        say(code == P::s_ok && t.step == 11 ? "MTP self-test finished." : "MTP self-test FAILED.");
    }
    P::put32(files, code);
    const U32* w = reinterpret_cast<const U32*>(&t);
    for (U32 i = 0; i != 16; ++i) P::put32(files + 4 + 4 * i, w[i]);
    result.last_status = code;
    send(P::t_mtp_test, seq, files, 4 + 64);
}
#endif
void put_begin(uint8_t seq, const uint8_t* payload, U32 length) {
    // Any stage.begin, refused or not, forgets the earlier staging: no stage.data after a
    // refused one writes into the old announcement or continues its hashes.
    put_expected = 0; result.put_ok = 0; result.put_received = 0; result.put_size = 0; result.put_crc = 0;
    if (length != 4 + 4 + 32) { status_only(P::t_put_begin, seq, P::s_bad_request); return; }
    const U32 size = P::get32(payload);
    if (!size || size > image_capacity) { status_only(P::t_put_begin, seq, P::s_too_large); return; }   // Test 049: any size
    put_expected = size; result.put_size = size; result.put_crc = P::get32(payload + 4);
    for (U32 i = 0; i != 32; ++i) put_sha[i] = payload[8 + i];
    put_running_crc = 0;
    put_hash().reset();
    status_only(P::t_put_begin, seq, P::s_ok);
}

void put_data(uint8_t seq, const uint8_t* payload, U32 length) {
    if (!put_expected || length < 5) { status_only(P::t_put_data, seq, P::s_state); return; }
    const U32 offset = P::get32(payload), n = length - 4;
    // A resend of bytes already received unchanged (the host's retry after a lost reply) is
    // ok with the count unchanged; it neither stores nor hashes them again.
    bool again = offset < result.put_received && offset + n <= result.put_received;
    for (U32 i = 0; again && i != n; ++i) again = staged[offset + i] == payload[4 + i];
    if (again) { uint8_t p[8]; P::put32(p, P::s_ok); P::put32(p + 4, result.put_received); send(P::t_put_data, seq, p, 8); return; }
    if (offset != result.put_received || offset + n > put_expected) { status_only(P::t_put_data, seq, P::s_range); return; }
    for (U32 i = 0; i != n; ++i) staged[offset + i] = payload[4 + i];
    put_running_crc = xyf::crc::update(put_running_crc, payload + 4, n);
    put_hash().update(payload + 4, n);
    result.put_received = offset + n;
    uint8_t p[8]; P::put32(p, P::s_ok); P::put32(p + 4, result.put_received);
    send(P::t_put_data, seq, p, 8);
}

bool sha_equal(const uint8_t* a, const uint8_t* b) { U32 d = 0; for (U32 i = 0; i != 32; ++i) d |= a[i] ^ b[i]; return !d; }

void put_end(uint8_t seq) {
    U32 code = P::s_ok;
    xyf_core::hash::Digest digest;
    // A repeated stage.end (the host's retry after a lost reply) answers ok again and keeps
    // the staging; the hashes were checked once and finish() must not run twice.
    if (result.put_ok && put_expected && result.put_received == put_expected) { status_only(P::t_put_end, seq, P::s_ok); return; }
    if (!put_expected || result.put_received != put_expected) code = P::s_state;
    else if (put_running_crc != result.put_crc) code = P::s_crc;
    else if (!put_hash().finish(digest) || !sha_equal(digest.bytes, put_sha)) code = P::s_sha;
    result.put_ok = code == P::s_ok;
    if (!result.put_ok) put_expected = 0;
    status_only(P::t_put_end, seq, code);
}

// Structural checks before anything is written (refuse on any doubt).
U32 validate_image(U32 size, bool uninstall) {
    if (!accept_image(uninstall, has_descriptor(staged, size), size, put_sha)) return P::s_image;
    static const xyf_core::Range allowed[] = {{0x08000000u, 0x08100000u}, {0x11800000u, 0x11c00000u},
                                              {0x80000000u, 0x90000000u}};
    const xyf_core::Policy policy = {allowed, 3, 0, 0};
    xyf_core::Summary summary;
    if (xyf_core::inspect(staged, size, policy, summary) != xyf_core::Error::ok) return P::s_image;
    return P::s_ok;
}

// Read the freshly written boot partition back and compare every byte.
U32 verify(U32 size, U32 crc) {
    uint8_t* back = reinterpret_cast<uint8_t*>(readback_buffer);
    U32 active = 0;
    for (U32 p = 1; p <= 2 && !active; ++p) {
        if (select_partition(p) != 0) continue;
        lines(readback_buffer, 512, true);
        if (read_sector0(0, back) == 0) { lines(readback_buffer, 512, true); if (header_matches(back, size, crc)) active = p; }
    }
    result.verify_partition = active;
    U32 code = P::s_install;
    if (active && select_partition(active) == 0) {
        code = P::s_ok;
        const U32 sectors = (size >> 9) + 1;       // as the stock reader 88a07240
        for (U32 s = 0; s < sectors && code == P::s_ok; s += readback_sectors) {
            const U32 count = sectors - s < readback_sectors ? sectors - s : readback_sectors;
            lines(readback_buffer, count * 512, true);
            if (read_sectors(1 + s, back, count)) { code = P::s_install; break; }
            lines(readback_buffer, count * 512, true);
            const U32 base = s * 512, limit = size - base < count * 512 ? size - base : count * 512;
            for (U32 i = 0; i != limit; ++i) if (back[i] != staged[base + i]) { ++result.verify_mismatch; code = P::s_install; break; }
            result.verify_sectors = s + count;
            tick();
        }
    }
    for (U32 tries = 0; tries != 8 && select_partition(0) != 0; ++tries) {}   // back to the user partition
    return code;
}

#if defined(XYF_USB_FILE_WRITE)
// A keeps its read-write window across this boot's FILE_WRITEs. B closes it (op 9, bounded)
// once no write came for storage_server::window_settle_ms, and before a command A would close it
// for (install, set default, CONTINUE, REBOOT): A closing it in its own loop (window_idle_ms, the
// fallback) would leave B's probes unanswered while yaffs writes its checkpoint. Then B fetches the
// catalog A scanned again after the close and the menu shows it (no fresh boot).
volatile U32 writes_due __attribute__((section(".data"))) = 0;
volatile U32 writes_ms __attribute__((section(".data"))) = 0;
// 1 while an op 9 started by settle_begin is out (file commands answer busy: job_blocks).
volatile U32 closing __attribute__((section(".data"))) = 0;
// #21: the next close ends a settings.txt save: no catalog fetch (the library is unchanged and the menu keeps its
// row). Taken by that close's settle_end.
U32 settle_quiet __attribute__((section(".data"))) = 0;
void settle_end(U32 v, bool catalog);
void settle_writes(bool catalog = true) {
    if (!writes_due) return;
    writes_due = 0;
    U32 v = ~0u;
    if (menu_link::usb_command(storage_server::command_item)) {        // A in RPC mode for op 9
        read_enabled = 1;
        const file_client::Reply u = file_client::unmount();             // op 9: 1, or 3 = closed the window
        if (!u.outcome) v = U32(u.value);
    }
    settle_end(v, catalog);
}
// The same close at the mod job's end without waiting in op 9: yaffs's
// checkpoint after megabytes took 0.3 s to about 17 s on the device, and B's loop answered no USB
// meanwhile (not even HELLO). settle_turn, every pass, ends it once A answered or the bound passed (then
// no catalog fetch: it would find the request still out and end the link; a late answer frees it).
void settle_begin() {
    if (!writes_due) return;
    writes_due = 0;
    if (menu_link::usb_command(storage_server::command_item)) {        // A in RPC mode for op 9
        read_enabled = 1;
        if (!file_client::unmount_start().outcome) { closing = 1; return; }
    }
    settle_end(~0u, true);
}
void settle_turn() {
    if (!closing) return;
    const file_client::Reply u = file_client::unmount_poll();
    if (u.outcome == file_client::busy) return;
    closing = 0;
    settle_end(u.outcome ? ~0u : U32(u.value), !u.outcome);              // past its bound: no catalog request
}
void settle_end(U32 v, bool catalog) {
    storage_server::Result& s = storage_server::result;
    mounted = v == ~0u ? file_client::mounted_stale : 0;
    s.b_window = v;
    if (v == 3) ++s.b_windows;
    const bool quiet = settle_quiet != 0;
    settle_quiet = 0;
    if (!catalog || quiet) return;
    if (xyf_boot::refresh_catalog()) ++s.b_catalogs;
    key_ui::refresh_catalog();
}
#else
void settle_writes(bool = true) {}
#endif

// The catalog item B's menu lists for a library id (n bytes), or 0.
const xyf::catalog::Item* catalog_item(const char* id, U32 n) {
    const xyf::catalog::Catalog* catalog = xyf_boot::catalog();
    for (U32 i = 0; catalog && i != catalog->count; ++i) {
        const char* e = catalog->items[i].id;
        U32 k = 0;
        while (k != n && e[k] == id[k]) ++k;
        if (k == n && !e[n]) return &catalog->items[i];
    }
    return 0;
}

#if defined(XYF_USB_SET_DEFAULT)
// Test 042: compiled out (module space). Test 048: back in the product build
// (wide layout); the first storage write run on hardware.
// SET_DEFAULT: payload = library id NUL. b passes CRC-32(id) to a as eight
// tagged nibble commands (low three bits 0) and a commit; a looks up the unique usable entry and
// writes the next A/B state slot (the only storage write). Reply: u32 status
// (ok = a received all nine commands), u32 hash. The write result is in a's
// report (a_state_write); the host verifies by READ_FILE of the slot.
#if defined(XYF_CONTENT_SWITCH)
U32 content_begin(const char* id, bool menu, bool write_default = true);
bool content_pending(const char* id);
#else
bool content_pending(const char*) { return false; }
#endif
// The A/B state write of the default (the entry with this id hash): eight tagged nibble commands and the commit,
// the barrier, B's catalog fetched again and checked. Also the content switch's last step.
U32 default_commit(U32 hash) {
    U32 code = P::s_ok;
    for (U32 k = 0; k != 8 && code == P::s_ok; ++k)
        if (!menu_link::usb_command_value(xyf::state_write::id_nibble_command(hash, k))) code = P::s_link;
    if (code == P::s_ok && !menu_link::usb_command(xyf::state_write::item_set_default_by_id)) code = P::s_link;
    if (code == P::s_ok) {
        // a writes right after it answers the commit. One more command is the barrier:
        // a's loop answers it only once the write has returned (bounded).
        storage_used = 1;
        menu_link::usb_patience(write_patience_ms);
        if (!menu_link::usb_command(storage_server::usb_mode_item)) code = P::s_link;
        menu_link::usb_patience(0);
        // The barrier covers A's write and rescan. Refresh B and the displayed
        // default before reporting success; disk read-back alone misses stale UI.
        if (!xyf_boot::refresh_catalog()) code = P::s_link;
        key_ui::refresh_catalog();
        const xyf::catalog::Catalog* catalog = xyf_boot::catalog();
        if (code == P::s_ok && (!catalog || catalog->default_index >= catalog->count ||
            xyf::state_write::id_hash(catalog->items[catalog->default_index].id) != hash)) code = P::s_storage;
    }
    return code;
}
void set_default(uint8_t seq, const uint8_t* payload, U32 length) {
    U32 code = P::s_ok;
    const char* id = reinterpret_cast<const char*>(payload);
    if (length < 2 || length > xyf::store::max_id + 1 || payload[length - 1] || !xyf::store::valid_id(id))
        code = P::s_bad_request;
    const U32 hash = code == P::s_ok ? xyf::state_write::id_hash(id) : 0;
    if (code == P::s_ok) settle_writes();
    // An entry whose content is not installed never becomes the default as it is (nothing sent to A);
    // The content switch installs it first (a job) and writes the default when it succeeded.
    const xyf::catalog::Item* item = code == P::s_ok ? catalog_item(id, length - 1) : 0;
    if (item && ((item->flags & xyf::catalog::flag_content_missing) || content_pending(id))) {
#if defined(XYF_CONTENT_SWITCH)
        code = content_begin(id, false);
        if (code == P::s_ok) code = P::s_started;
#else
        code = P::s_no_content;
#endif
    }
    if (code == P::s_ok) code = default_commit(hash);
    uint8_t p[8]; P::put32(p, code); P::put32(p + 4, hash);
    result.last_status = code;
    send(P::t_set_default, seq, p, 8);
}
#endif

// LIBRARY_BOOT: payload = library id NUL + 32-byte confirmation (the SHA-256 of the id
// bytes). A boot once of that entry (key_ui.cpp: xyf_boot::choose of its catalog index, as home_pump
// leaves the menu): no boot over a lost link (link); the entry must be in B's catalog (not_found;
// written entries are fetched first: settle_writes) and usable, i.e. have an os.ldr, as xyf_menu's
// usable() (state), and its content installed (no_content). Nothing is written: the default and the state slots stay. Returns the catalog
// index + 1 to boot after the ok reply, else 0.
U32 library_boot(uint8_t seq, const uint8_t* payload, U32 length) {
    U32 n = 0;
    while (n < length && payload[n]) ++n;
    const char* id = reinterpret_cast<const char*>(payload);
    U32 code = P::s_ok, chosen = 0;
    xyf_core::hash::Sha256 h;
    xyf_core::hash::Digest d;
    if (n + 1 + 32 != length || n > xyf::store::max_id || !xyf::store::valid_id(id)) code = P::s_bad_request;
    else if (!h.update(payload, n) || !h.finish(d) || !sha_equal(d.bytes, payload + n + 1)) code = P::s_sha;
    else if (!menu_link::usb_link_up()) code = P::s_link;
    if (code == P::s_ok) {
        settle_writes();                               // A's window closed, the catalog fetched again
        const xyf::catalog::Item* item = catalog_item(id, n);
        code = !menu_link::usb_link_up() ? P::s_link : !item ? P::s_not_found : (item->flags & 1u) ? P::s_ok :
               (item->flags & xyf::catalog::flag_content_missing) ? P::s_no_content : P::s_state;
        if (item) chosen = U32(item - xyf_boot::catalog()->items) + 1;
        if (code == P::s_no_content) say("Its content is not installed:", "make it the default to install its content.");
    }
    result.last_status = code;
    status_only(P::t_library_boot, seq, code);
    return code == P::s_ok ? chosen : 0;
}


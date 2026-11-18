// Part of file_boot.cpp: B's menu boot (the checks and catalog requests, stage 2's load, role1_menu_boot),
// included in place (in file_boot's anonymous namespace).
#if defined(XYF_STAGE2)
// b: fetch, accept, copy and prepare stage 2 (bounded by stage2_ms).
__attribute__((noinline)) void b_load_stage2(Stock121PeerSession& session) {
    xyf_boot::Stage2Info& s = result.stage2;
    s.status = xyf_boot::s2_link;
    if (!b_send(session, xyf_boot::item_stage2)) return;          // b_lost already set
    if (session.transfer(reinterpret_cast<Byte*>(staging), staging_bytes, true) != SessionResult::waiting) {
        b_lost = 1;                                     // a holds a frozen blob nobody fetches: give up
        return;
    }
    const key_ui::Where where(xyf::where::stage2_blob);
    const U32 start = now();
    while (!session.blob_done() && now() - start < xyf_boot::stage2_ms && b_step(session)) where.wait();
    if (!session.blob_done()) { b_lost = 1; return; }
    b_last_send = now();
    const U32 size = session.image_size();
    s.file_bytes = size;
    xyf::stage2::Header header;
    uint32_t detail = 0;
    const xyf::stage2::Blob blob = xyf::stage2::classify(reinterpret_cast<const Byte*>(staging), size,
                                                         xyf::stage2::this_stage1, header, detail);
    s.check = detail;                                   // a's note (refused) or b's Check
    if (blob == xyf::stage2::Blob::refused) { s.status = xyf_boot::s2_refused; return; }
    if (blob != xyf::stage2::Blob::ready) { s.status = xyf_boot::s2_check; return; }
    const Byte* file = reinterpret_cast<const Byte*>(staging);
    s.version = header.version; s.entry = header.entry;
    volatile Byte* to = reinterpret_cast<volatile Byte*>(header.load_address);
    for (U32 i = 0; i != header.image_bytes; ++i) to[i] = file[xyf::stage2::header_bytes + i];
    if (crc32(to, header.image_bytes) != header.image_crc32) { s.status = xyf_boot::s2_copy; return; }
    prepare_code(header.load_address, header.load_address + header.image_bytes);
    s.status = xyf_boot::s2_ready;
    s.load_ms = now();
    b_stage2_entry = header.entry;
}
#endif
#if defined(XYF_MENU_ON_FAILURE)
// Test 059 (b): menu-on-failure state and helpers.
volatile U32 b_failed __attribute__((section(".data"))) = 0;   // a check failed: the menu waits (no idle timeout)
char b_notice[64] __attribute__((section(".data"))) = {0};
const xyf_boot::CheckStatus& b_status() { return *reinterpret_cast<const xyf_boot::CheckStatus*>(xyf_boot::b_status_buffer); }

const char* reason_text(U32 reason) {
    return reason == reason_no_library ? "not in the library" : reason == reason_open ? "file missing" :
           reason == reason_size ? "too large" : reason == reason_read ? "read error" :
           reason == reason_mismatch ? "size/CRC differ from info.txt" :
           reason == reason_policy ? "image rejected" : reason == reason_unmount ? "storage error" :
           reason == reason_storage ? "storage error" : reason == reason_content ? "content not installed" :
           reason == reason_content_repair ? "content needs repair" : "can't boot";
}
void b_append(U32& n, const char* s) { while (*s && n + 1 < sizeof(b_notice)) b_notice[n++] = *s++; b_notice[n] = 0; }
// "Default opxy-125: file missing" / "opxy-125: image rejected".
void b_note(bool is_default, U32 reason) {
    U32 n = 0;
    if (is_default) b_append(n, "Default ");
    const char* id = b_status().magic == xyf_boot::check_magic && b_status().id[0] ? b_status().id : "entry";
    char safe[32];
    U32 k = 0;
    for (; k != 31 && id[k]; ++k) safe[k] = id[k];
    safe[k] = 0;
    b_append(n, safe);
    b_append(n, ": ");
    b_append(n, reason_text(reason));
}

// CHECK item: a answers a CheckStatus blob. Returns its reason (reason_storage and
// b_lost on a link failure, reason_read for a malformed answer).
U32 b_check(Stock121PeerSession& session, U32 item) {
    const U32 check_start = now();
    result.timing.valid = 0;                         // a failed new CHECK must not show old timings
    Byte* status = reinterpret_cast<Byte*>(xyf_boot::b_status_buffer);
    for (U32 i = 0; i != sizeof(xyf_boot::CheckStatus); ++i) status[i] = 0;
    if (!b_send(session, item) ||
        session.transfer(status, xyf_boot::b_status_capacity, true) != SessionResult::waiting) { b_lost = 1; return reason_storage; }
    const key_ui::Where where(xyf::where::check_blob);
    const U32 start = now();
    while (!session.blob_done() && now() - start < xyf_boot::check_ms && b_step(session)) where.wait();
    if (!session.blob_done()) { b_lost = 1; return reason_storage; }
    if (session.image_size() < sizeof(xyf_boot::CheckStatus) || b_status().magic != xyf_boot::check_magic)   // Test 062: >=
        return reason_read;
    if (b_status().timing.magic == xyf_boot::timing_magic && b_status().timing.version == 1)
        result.timing = b_status().timing;
    result.timing.b_check_ms = now() - check_start;
    result.timing.valid |= xyf_boot::timing_check;
    return b_status().reason;
}

// b: decode the catalog blob that arrived.
bool b_take_catalog(Stock121PeerSession& session) {
    Result& r = result;
    r.menu.catalog_bytes = session.image_size();
    b_catalog_ok = xyf::catalog::decode(reinterpret_cast<const Byte*>(xyf_boot::catalog_buffer),
                                        session.image_size(), b_catalog());
    r.menu.catalog_ok = b_catalog_ok;
    r.menu.catalog_entries = b_catalog_ok ? b_catalog().count : 0;
    r.menu.catalog_ms = now();
    return b_catalog_ok != 0;
}
bool b_fetch_catalog(Stock121PeerSession& session) {
    Result& r = result;
    if (!b_send(session, xyf_boot::item_catalog) ||
        session.transfer(reinterpret_cast<Byte*>(xyf_boot::catalog_buffer), xyf_boot::catalog_capacity, true) !=
            SessionResult::waiting) { b_lost = 1; return false; }
    const key_ui::Where where(xyf::where::catalog_blob);
    const U32 start = now();
    while (!session.blob_done() && now() - start < xyf_boot::blob_ms && b_step(session)) where.wait();
    if (!session.blob_done()) { b_lost = 1; return false; }
    return b_take_catalog(session);
}
#if defined(XYF_ONE_SESSION)
// 191: the catalog request without blocking the menu while A mounts (tick): asked, A answers after
// its first mount; then the blob in one loop (a retry's mount inside it), start_ms in all. The storage word tells B whether A's mount worked.
void b_fetch_begin(Stock121PeerSession& session) {
    xyf_boot::StorageStart& st = xyf_boot::storage_start;
    if (!b_settle(session)) return;
    usb_service::rpc_reset();
    b_fetch_echoes = session.echoes();
    if (session.ask(Stock121PeerSession::command_tag << 16 | (xyf_boot::item_catalog & 0xffu) << 8 |
                    (++b_sequence << 3 & 0xf8u)) != SessionResult::waiting) { b_lost = 1; st.phase = 4; return; }
    ++result.menu.commands; ++st.asks;
    b_fetching = 1; b_fetch_at = b_last_send = now();
    if (st.phase != 3) st.phase = 1;
}
// One step of it; true when it ended (b_catalog_ok, storage_start tell how).
bool b_fetch_step(Stock121PeerSession& session) {
    xyf_boot::StorageStart& st = xyf_boot::storage_start;
    const bool alive = b_step(session);
    const U32 t = now(), waited = t - b_fetch_at;
    st.waited_ms = waited;
    if (waited > st.longest_ms) st.longest_ms = waited;
    if (alive && b_fetching == 1 && !session.busy()) {
        if (session.echoes() != b_fetch_echoes + 1 ||
            session.transfer(reinterpret_cast<Byte*>(xyf_boot::catalog_buffer), xyf_boot::catalog_capacity, true) !=
                SessionResult::waiting) b_lost = 1;
        else {
            // The blob in one bounded loop, as b_fetch_catalog (sim: polls spread over the menu loop
            // could end it with a protocol error). A retry's mount runs inside it (A answered first).
            b_fetching = 2;
            const key_ui::Where where(xyf::where::catalog_blob);
            while (!session.blob_done() && now() - b_fetch_at <= xyf_boot::start_ms && b_step(session)) where.wait();
        }
    }
    if (alive && !b_lost && b_fetching == 2 && session.blob_done()) {
        b_fetching = 0; st.waited_ms = 0;
        b_last_send = t;
        if (!result.storage_ms) result.storage_ms = t;
        if (!st.answered_ms) st.answered_ms = t;
        b_take_catalog(session);
        const U32 word = b_catalog_ok ? xyf::catalog::reserved(reinterpret_cast<const Byte*>(xyf_boot::catalog_buffer)) : 0u;
        st.a_word = word;
        const bool failed = !b_catalog_ok || xyf_boot::storage_failed(word);
        st.phase = failed ? 3 : 2;
        // 192: a failed repair is final this boot (A mounts no more): B stops asking.
        b_failed_at = failed && xyf_boot::storage_repair(word) != xyf_boot::repair_failed ? (t ? t : 1u) : 0u;
        // 192: A repairs now: ask again at once, so nothing else reaches A meanwhile (as at its first mount).
        if (failed && xyf_boot::storage_repair(word) == xyf_boot::repair_pending) b_fetch_begin(session);
        return true;
    }
    if (b_lost || waited > xyf_boot::start_ms) { b_lost = 1; b_fetching = 0; st.phase = 4; return true; }
    return false;
}
#endif
#endif

// b (role 1). Returns the commit decision; bytes is the received image size.
bool role1_menu_boot(U32& bytes) {
    Result& r = result;
    const U32 frame = 0x8807ce7eu;                     // stock key status reply (Test017)
    r.menu.play = byte(frame + 2) == 0x42 && byte(frame + 3) == 0x0a && (byte(frame + 4 + 51 / 8) >> (51 % 8) & 1u);
#if defined(XYF_USB_MENU)
    if (usb_service::menu_requested()) r.menu.play = 1;    // Test 058: the host asked for the menu
#endif
#if defined(XYF_ONE_SESSION)
    if (usb_service::home_begin()) r.menu.play = 1;    // Test 065: our REBOOT / dry-run return: home
#endif
    Stock121PeerSession session(r.diagnostic);
    session.stage(role0_idle_ms, handshake_ms);
    SessionResult status = session.begin(services::Phase::cold_after_teardown,
        reinterpret_cast<Byte*>(scratch), scratch_bytes, reinterpret_cast<Byte*>(staging), staging_bytes,
#if defined(XYF_MENU_ON_FAILURE)
        now(), xyf_boot::role1_menu_cap_ms);           // Test 059: a failed default opens the menu
#else
        now(), r.menu.play ? xyf_boot::role1_menu_cap_ms : xyf_boot::role1_boot_cap_ms);
#endif
    r.begin_result = U32(status);
    b_lost = running(status) ? 0 : 1;
    {
        const key_ui::Where where(xyf::where::handshake);
        while (!b_lost && !session.link_ready()) { b_step(session); where.wait(); }   // bounded by handshake_ms
    }
    if (!b_lost) r.link_ms = now();
#if defined(XYF_MENU_ON_FAILURE)
    // Test 059: check the default first; if it can't boot, open the menu (like a
    // BIOS) with the reason; every menu choice is checked before it boots, and a
    // failed choice returns to the menu with a message. Only a checked image boots.
    U32 boot_item = 0;
    if (!b_lost && !r.menu.play) {
#if defined(XYF_SERVICE_ON_MARKER_ONLY)
        usb_service::progress("Checking the default firmware...", "");
#endif
        const U32 reason = b_check(session, xyf_boot::item_check_default);
        if (reason == reason_ok) boot_item = xyf_boot::item_boot_default;
        else if (!b_lost) { b_failed = 1; b_note(true, reason); r.menu.play = 1; }
#if defined(XYF_SERVICE_ON_MARKER_ONLY)
        usb_service::progress(reason == reason_ok ? "Default firmware checked." : b_lost ? "Storage link lost." : b_notice,
                              reason == reason_ok ? "Booting it..." : "Opening the menu...");
#endif
        r.menu.refused = reason | b_status().index << 8 | (reason == reason_ok ? 0u : 0x10000u);
    }
#if defined(XYF_ONE_SESSION)
    if (!b_lost && !boot_item) {                    // Test 065: home (USB on, marker)
        b_session = &session;                       // home_start: USB on, the marker written
        usb_service::home_start();
        b_session = 0;
    }
#endif
    while (!b_lost && !boot_item) {
#if defined(XYF_ONE_SESSION)
        if (!b_catalog_ok && !b_fetching) b_fetch_begin(session);   // 191: the menu opens at once (tick)
        if (b_lost) break;
#else
        if (!b_catalog_ok && !b_fetch_catalog(session)) break;
#endif
        b_session = &session;
        b_last_send = now();
        b_choice = 0;
        xy_forever_key_ui();                        // XY Forever menu; ticks keepalives
        b_session = 0;
        r.menu.menu_ms = now();
        if (b_lost || !b_choice) break;                 // link lost, or the menu ended without a choice
#if defined(XYF_ONE_SESSION)
        while (b_fetching && !b_lost) b_fetch_step(session);   // 191: a choice before A answered: its answer first
                                                         // (192: and the repair's, when that answer asked again)
        if (b_lost) break;
#endif
        r.menu.choice = b_choice; r.menu.choice_entry = b_entry;
        const U32 reason = b_check(session, b_choice == 2 ? xyf_boot::item_check_entry + b_entry :
                                                            xyf_boot::item_check_default);
        if (reason == reason_ok)
            boot_item = b_choice == 2 ? xyf_boot::item_boot_entry + b_entry : xyf_boot::item_boot_default;
        else if (!b_lost) { b_failed = 1; b_note(b_choice != 2, reason); }
    }
#if defined(XYF_USB_MENU)
    usb_service::menu_release();                       // Test 058: USB off before any boot
#endif
    if (!b_lost && boot_item) {
        if (b_send(session, boot_item) &&
            session.transfer(reinterpret_cast<Byte*>(staging), staging_bytes, false) == SessionResult::waiting) {
            status = SessionResult::waiting;
            r.menu.boot_ms = now();
            xyf_boot::TimingDetail& d = r.detail;
            U32 poll_start = r.menu.boot_ms, admitted_at = 0;
            const key_ui::Where where(xyf::where::boot_image);
            while ((status == SessionResult::waiting || status == SessionResult::choose_image ||
                    status == SessionResult::admit_image) &&
                   now() - r.menu.boot_ms < xyf_boot::boot_transfer_ms) {   // Test 061: own bound
                if (!d.b_metadata_ms && session.image_size()) d.b_metadata_ms = now() - r.menu.boot_ms;
                if (status == SessionResult::admit_image) {
                    r.received_bytes = session.image_size();
                    r.image_crc = session.received_crc();          // checked over the received bytes
                    r.admit_ms = now();
                    d.b_data_ms = poll_start - r.menu.boot_ms;
                    d.b_last_poll_ms = r.admit_ms - poll_start;
                    r.timing.b_receive_ms = r.admit_ms - r.menu.boot_ms;
                    r.timing.valid |= xyf_boot::timing_receive;
                    r.admit_reason = admit(reinterpret_cast<Byte*>(staging), r.received_bytes);
                    d.b_policy_ms = now() - r.admit_ms;
                    if (r.admit_reason != reason_ok) break;            // not admitted: never release
                    const xyf_core::hash::Digest& received = session.received_digest();
                    for (U32 i = 0; i != 8; ++i)
                        r.digest[i] = received.bytes[4 * i] | U32(received.bytes[4 * i + 1]) << 8 |
                                      U32(received.bytes[4 * i + 2]) << 16 | U32(received.bytes[4 * i + 3]) << 24;
                    const U32 hash_start = now();
                    if (session.admit(received) != SessionResult::ok) break;
                    admitted_at = now();
                    d.b_admit_hash_ms = admitted_at - hash_start;
                    r.timing.b_admit_ms = admitted_at - r.admit_ms;
                    r.timing.valid |= xyf_boot::timing_b_admit;
                }
                poll_start = now();
                status = session.poll(poll_start);
                ++r.polls;
                where.wait();
            }
            if (admitted_at) d.b_release_ms = now() - admitted_at;
        } else status = SessionResult::unavailable;
    } else status = SessionResult::unavailable;
#else
    if (!b_lost && r.menu.play) {
        if (b_send(session, xyf_boot::item_catalog) &&
            session.transfer(reinterpret_cast<Byte*>(xyf_boot::catalog_buffer), xyf_boot::catalog_capacity, true) ==
                SessionResult::waiting) {
            const key_ui::Where where(xyf::where::catalog_blob);
            const U32 start = now();
            while (!session.blob_done() && now() - start < xyf_boot::blob_ms && b_step(session)) where.wait();
            if (session.blob_done()) {
                r.menu.catalog_bytes = session.image_size();
                b_catalog_ok = xyf::catalog::decode(reinterpret_cast<const Byte*>(xyf_boot::catalog_buffer),
                                                    session.image_size(), b_catalog());
                r.menu.catalog_ok = b_catalog_ok;
                r.menu.catalog_entries = b_catalog_ok ? b_catalog().count : 0;
            } else b_lost = 1;                          // a request still outstanding: give up
        }
        r.menu.catalog_ms = now();
#if defined(XYF_STAGE2)
        if (!b_lost) b_load_stage2(session);
#endif
        if (!b_lost) {
            b_session = &session;
            b_last_send = now();
            xy_forever_key_ui();                    // XY Forever menu; ticks keepalives
            b_session = 0;
        }
        r.menu.menu_ms = now();
    }
    if (!b_lost) {
        r.menu.choice = b_choice ? b_choice : 1;
        r.menu.choice_entry = b_entry;
        const U32 item = b_choice == 2 ? xyf_boot::item_boot_entry + b_entry : xyf_boot::item_boot_default;
        if (b_send(session, item) &&
            session.transfer(reinterpret_cast<Byte*>(staging), staging_bytes, false) == SessionResult::waiting) {
            status = SessionResult::waiting;
            r.menu.boot_ms = now();
            const key_ui::Where where(xyf::where::boot_image);
            while (status == SessionResult::waiting || status == SessionResult::choose_image ||
                   status == SessionResult::admit_image) {
                if (status == SessionResult::admit_image) {
                    r.received_bytes = session.image_size();
                    r.image_crc = session.received_crc();          // checked over the received bytes
                    r.admit_ms = now();
                    r.admit_reason = admit(reinterpret_cast<Byte*>(staging), r.received_bytes);
                    if (r.admit_reason != reason_ok) break;            // not admitted: never release
                    const xyf_core::hash::Digest& received = session.received_digest();
                    for (U32 i = 0; i != 8; ++i)
                        r.digest[i] = received.bytes[4 * i] | U32(received.bytes[4 * i + 1]) << 8 |
                                      U32(received.bytes[4 * i + 2]) << 16 | U32(received.bytes[4 * i + 3]) << 24;
                    if (session.admit(received) != SessionResult::ok) break;
                }
                status = session.poll(now());
                ++r.polls;
                where.wait();
            }
        } else status = SessionResult::unavailable;
    } else status = SessionResult::unavailable;
#endif
    r.poll_result = U32(status);
    if (status != SessionResult::ready) r.end_result = U32(session.end());
    r.declined = session.declined();
    r.menu.blobs = session.blobs();
    bytes = session.image_size();
    return session.committed();
}

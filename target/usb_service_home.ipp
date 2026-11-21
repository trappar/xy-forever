// Part of usb_service.cpp, included there at this point: not compiled alone.
#if defined(XYF_MTP_USB)
// The MTP mark: a transfer runs or a command came within mtp_seen_ms (an open session alone does not light it).
bool mtp_seen() { return composite_on && (mtp_busy() || (last_commands && now() - mtp_ms < mtp_seen_ms)); }
// #144: the menu loop around MTP traffic (mtp_traffic): its pause after a pass is mtp_pause_us, not 5 ms (a host's
// next command waited for it: about 4 ms of each small file's 15). loop_from: when home_pump's pass ended while
// MTP traffic was active (0: it was not); the time to the next pass and the redraws in it count in MTP's timing.
const U32 mtp_pause_us = 500;
uint64_t loop_from __attribute__((section(".data"))) = 0;
#endif
uint32_t menu_pause_us() {
#if defined(XYF_MTP_USB)
    if (loop_from) return mtp_pause_us;
#endif
    return 5000u;
}
void drawn(uint64_t start) {
#if defined(XYF_MTP_USB)
    if (loop_from && mtp_live()) { ms::Timing& t = mslot().files.timing; ++t.draws; t.draw_units += units_since(start); }
#else
    (void)start;
#endif
}
bool host_seen() { return host_framed && now() - host_frame_ms < host_seen_ms; }
uint32_t host_marks() {
#if defined(XYF_MTP_USB)
    return (host_seen() ? 1u : 0u) | (mtp_seen() ? 2u : 0u);
#else
    return host_seen() ? 1u : 0u;
#endif
}
const char* host_activity() {
#if defined(XYF_MTP_USB)
    // #144: "transfer" also between a batch's operations (mtp_traffic): not two redraws per small file.
    if (mtp_seen()) return mtp_busy() || mtp_traffic() ? "MTP: transfer" : "MTP: active";   // redraws the MTP mark
#endif
#if defined(XYF_USB_FILE_WRITE)
    if (job_running() && job_usb) return host_line(job_kind == 1 ? line_content : line_build);
#endif
    return host_change && now() - host_change_ms < host_activity_ms ? host_line(host_change) : nullptr;
}
const char* version_label() { return "v" XYF_VERSION; }
// A separate text until the hash settles: the pointer changes when it does, and the Info page redraws.
const char* image_label() {
    return result.image_state == image_known || result.image_state == image_unknown ? image_text : "image ...";
}
bool home_hold() { return home != 0 || home_greeted != 0; }

// b, at the start of the file boot: a valid marker (our REBOOT, a dry-run return, or a reset
// while home) makes this boot go home; at most max_crash_restarts such resets in a row.
bool home_begin() {
    Result& r = result;
    r.version = 5; r.role = 1;
    r.start_cycles = cycles_div1024();
    start_ms = xyf::clock::ms();
    r.power_status = word(power_status_address);
    r.boot_reason = half(0x88a35938u);
    r.present = presence() == 3 ? 3u : 0u;
    if (!r.present) return false;
    const U32 reason = word(reboot_marker + 8);
    r.crash_restarts = (reason & 0xffu) == marker_active ? word(reboot_marker + 12) + 1 : 0;
    marker_clear();
    // No reset loop: at most max_crash_restarts in a row.
    if (r.crash_restarts >= max_crash_restarts) {
        r.present = 0; return false;
    }
    home = 1; held = 1;
    r.present |= 1u << 4;
    return true;
}

// b, right before the menu loop (home, PLAY or a failed default): USB on.
void home_start() {
    menu_start_usb();
    if (home) marker_write(marker_active | marker_menu | flags(), result.crash_restarts);
}

// REBOOT from home: a resets on the reboot item, b 100 ms later; target OS = no marker,
// anything else = home again (held carried).
void home_reboot(U32 target) {
    say("Restarting...");
    menu_link::usb_command(storage_server::reboot_item);
    const U32 t = now();
    while (now() - t < 100u) {}
    const U32 carry = flags();
    if (!b_link_closed(true)) return;                  // refused: home stays, the link lost
    menu_release();
    if (target == reboot_os) marker_clear();
    else marker_write(marker_reboot | marker_menu | carry, 0);
    reset_self();
}


#if defined(XYF_MTP_USB)
// #144: one home_pump pass: the menu loop since the last one counted (MTP traffic active then), its end marked.
struct LoopMark {
    LoopMark() {
        if (loop_from && mtp_live()) { ms::Timing& t = mslot().files.timing; ++t.loops; t.loop_units += units_since(loop_from); }
        loop_from = 0;
    }
    ~LoopMark() { loop_from = mtp_traffic() ? xyf::clock::cycles() | 1u : 0u; }
};
#endif
// Every pass of the menu loop: 0 = stay home, 1 = leave home and boot the default, 2 + i = leave
// home and boot catalog entry i once (library.boot).
uint32_t home_pump() {
#if defined(XYF_MTP_USB)
    const LoopMark loop;                               // #144: the menu loop between passes
#endif
    Result& r = result;
    image_step();                                      // the image's hash, a slice per pass
    if (!menu_usb) return 0;
#if defined(XYF_MTP_USB)
#if defined(XYF_SETTINGS)
    // #21: the settings first (USB mode, #65's question: settings.txt through A, which serves within a pass or two); a
    // slow storage start (a full scan) or no answer: the defaults after usb_settings_ms. USB starts the same either way.
    if (usb_deferred && !settings_load() && now() - usb_deferred_ms < usb_settings_ms) return 0;
#endif
    if (usb_deferred) { usb_deferred = 0; usb_begin(); return 0; }   // the flag, then the boot's only start
#endif
    stage2_turn();
#if defined(XYF_USB_FILE_WRITE)
    rehearsal_pump();
    if (!boot_probe_active() && !rehearsal_active()) {
    recovery_turn();
    report_turn();
    package_turn();
    job_pump();                                        // a slice of the mod job
    boot_sweep_turn();                                 // #3: leftover tmp/job-* folders, a step per pass
    stream_turn();                                     // #4: a streamed write left without data
    settle_turn();                                     // a window close still out
    if (writes_due && !job_running() XYF_AND_NOT_MTP_BUSY && now() - writes_ms > storage_server::window_settle_ms) settle_writes();
    }
#endif
#if defined(XYF_MTP_USB)
    composite_turn();                                  // the driver's pass, the HELLO bound (fallback restart)
#endif
#if defined(XYF_USB_FILE_WRITE) && defined(XYF_MTP_SESSION)
    mtp_pump();                                        // responder steps for a slice (one file request each)
#endif
#if defined(XYF_MTP_USB)
    mtp_burst();                                       // speed: more passes of both while MTP traffic is active
#endif
#if defined(XYF_SETTINGS)
    trust_turn();                                      // #65: the question's answer (the keys came this pass)
#endif
    const U32 avail = cdc_available();
    if (!avail) {
        // A partial frame followed by idle_reset_ms of silence (nothing left in the receive
        // ring) is dropped, so it cannot swallow the start of the next request.
        if (parser.have && now() - rx_quiet_from > P::idle_reset_ms) {
            parser.dropped += parser.have; parser.have = 0; r.dropped = parser.dropped;
        }
        return 0;
    }
    uint8_t* const chunk = reinterpret_cast<uint8_t*>(rx_chunk);
    const U32 got = cdc_read(chunk, avail < rx_chunk_bytes ? avail : rx_chunk_bytes);
    ++r.rx_events; r.rx_bytes += got;
#if defined(XYF_MTP_USB)
    if (got) composite_byte();                         // mark 10 once
#endif
    for (U32 i = 0; i != got; ++i) {
        if (!P::feed(parser, chunk[i])) continue;
        const uint8_t type = P::frame_type(parser), seq = P::frame_seq(parser);
        const uint8_t* payload = P::frame_payload(parser);
        const U32 length = P::frame_length(parser);
        ++r.frames; r.last_type = type;
        host_framed = 1; host_frame_ms = now();                         // the USB mark
        bool off = false;                                              // #191: USB mode MTP refuses it
#if defined(XYF_SETTINGS)
        off = type != P::t_hello && serial_refuses(type);
#endif
        if (const U32 line = off ? 0u : change_line(type)) {
            host_change = line; host_change_ms = host_frame_ms;
#if defined(XYF_USB_FILE_WRITE) && defined(XYF_MTP_SESSION)
            if (mtp_on) mslot().responder.invalidate();    // speed: MTP's cached object facts (mtp_responder.hpp)
#endif
        }
#if defined(XYF_SERVICE_SCREEN)
        key_ui::host_view();                                           // drawn before a long request runs
#endif
        U32 leave = 0;
        if (type == P::t_hello) {
            if (!home_greeted) { home_greeted = 1; if (!r.hello_at_ms) r.hello_at_ms = now(); }
            if (!s2_tried) stage2_load();                  // once per boot, before the reply
            hello(seq);
#if defined(XYF_MTP_USB)
            composite_hello();                         // the composite stays this boot; mark 11, marks end
#endif
        }
        else if (!home_greeted) status_only(type, seq, P::s_state);
        else if (off) status_only(type, seq, P::s_serial_off);         // #191: answered at once, nothing done
#if defined(XYF_SETTINGS)
        else if (const U32 refused = trust_refuses(type)) status_only(type, seq, refused);   // #65: asks, or not allowed
#endif
#if defined(XYF_MTP_USB)
        else if (type == t_mtp_usb) mtp_usb_command(seq, payload, length);   // the probe (test build only)
#endif
#if defined(XYF_CRASH_TRIGGER) && defined(XYF_USB_FILE_WRITE)
        else if (type == t_crash) crash_command(seq, payload, length);       // a fault on purpose (test build only)
#endif
#if defined(XYF_USB_FILE_WRITE)
#if defined(XYF_TFW_CHECK)
        else if (type == P::t_a_rehearsal) rehearsal_command(seq, payload, length);
        else if (rehearsal_active() && type != P::t_status && type != P::t_screen && type != P::t_peek)
            status_only(type, seq, P::s_busy);
#endif
        else if (type == xy_forever::boot_partition_probe::command) boot_probe_command(seq, payload, length);
        else if (boot_probe_active() && type != P::t_status && type != P::t_screen && type != P::t_peek)
            status_only(type, seq, P::s_busy);
#if defined(XYF_MTP_SESSION)
        else if (type == t_mtp_tunnel) mtp_tunnel(seq, payload, length);   // the test transport
#endif
        else if (type == P::t_package_session) package_session(seq, payload, length);
        else if (job_blocks(type)) status_only(type, seq, P::s_busy);   // one request slot
        else if (type == P::t_package_list) package_list(seq, payload, length);
        else if (type == P::t_state_get) state_get(seq, payload, length);
        else if (type == P::t_storage_space) storage_space(seq, payload, length);
        else if (type == P::t_mods_apply) mods_apply(seq, payload, length);
        else if (type == P::t_job_status) job_status(seq, payload, length);
        else if (type == P::t_job_cancel) job_cancel(seq, payload, length);
        else if (type == P::t_config_get) config_get(seq, payload, length);
        else if (type == P::t_config_set) config_set(seq, payload, length);
#endif
        else if (type == P::t_status) status(seq);
        else if (type == P::t_read_file) read_file(seq, payload, length);
        else if (type == P::t_screen) screen(seq, payload, length);
        else if (type == P::t_peek) peek(seq, payload, length);
#if defined(XYF_MTP)
        else if (type == P::t_mtp_test) mtp_test(seq, payload, length);
#endif
        else if (type == P::t_put_begin) put_begin(seq, payload, length);
        else if (type == P::t_put_data) put_data(seq, payload, length);
        else if (type == P::t_put_end) put_end(seq);
        else if (type == P::t_install) install(seq, payload, length, false);
        else if (type == P::t_uninstall) install(seq, payload, length, true);
#if defined(XYF_USB_SET_DEFAULT)
        else if (type == P::t_set_default) set_default(seq, payload, length);
#endif
#if defined(XYF_USB_FILE_WRITE)
        else if (type == P::t_file_write) file_write(seq, payload, length);
        else if (type == P::t_list_dir) list_dir(seq, payload, length);
        else if (type == P::t_delete_file || type == P::t_remove_dir || type == P::t_make_dir)
            file_change(type, seq, payload, length);
        else if (type == P::t_rename_file) file_rename(seq, payload, length);
        else if (type == P::t_copy_file) file_copy(seq, payload, length);
        else if (type == P::t_write_begin) write_begin(seq, payload, length);
        else if (type == P::t_write_data) write_data(seq, payload, length);
        else if (type == P::t_write_end) write_end(seq);
#endif
#if defined(XYF_TFW_CHECK)
        else if (type == P::t_tfw_check) tfw_check(seq, payload, length);
        else if (type == P::t_tfw_check_role) tfw_check_role(seq, payload, length);
#endif
        else if (type == P::t_key) {
            const bool ok = length == 8 && P::get32(payload) < 64u && P::get32(payload + 4) <= 1u;
            if (ok) key_ui::inject_key(P::get32(payload), P::get32(payload + 4));
            status_only(type, seq, ok ? P::s_ok : P::s_bad_request);
        }
        else if (type == P::t_library_boot) {         // boot a named entry once (as PLAY on it)
            const U32 chosen = library_boot(seq, payload, length);
            if (chosen) { dry_run = 0; carried_flags = flags(); held = 0; leave = 1 + chosen; }
        }
        else if (type == P::t_continue) {              // payload u32: 0 boot the default, 1 dry run
            const U32 mode = length == 4 ? P::get32(payload) : 0;
            bool ok = length == 0 || (length == 4 && mode <= 1);
            if (ok && !menu_link::usb_link_up()) ok = false;   // no boot over a lost link (menu stays)
            if (ok) settle_writes(false);                  // A's window closed before the boot
            if (ok && mode == 1) ok = menu_link::usb_command(storage_server::dry_run_item);
            if (ok) { dry_run = mode; carried_flags = flags(); held = 0; leave = 1; }
            status_only(type, seq, ok ? P::s_ok : P::s_link);
        }
        else if (type == P::t_hold) {
            const U32 on = length == 4 ? P::get32(payload) : 2;
            if (on <= 1) {
                held = on; home = on;
                if (home) marker_write(marker_active | marker_menu | flags(), r.crash_restarts);
            }
            status_only(type, seq, on <= 1 ? P::s_ok : P::s_bad_request);
        }
        else if (type == P::t_reboot) {
            const U32 target = length == 4 ? P::get32(payload) : reboot_menu;
            if (target > reboot_menu) status_only(type, seq, P::s_bad_request);
            else { status_only(type, seq, P::s_ok); settle_writes(false); const U32 t = now(); while (now() - t < 50u) {} home_reboot(target); }
        }
        else if (stage2_command(type, seq, payload, length)) {}   // stage 2's types (stage2.info)
        else status_only(type, seq, P::s_unsupported);
#if defined(XYF_USB_FILE_WRITE)
        if (package_file) { package_ms = now(); package_file = 0; }
#endif
        P::next(parser);
        if (leave) { r.exit = exit_continue; return leave; }
    }
    r.bad_crc = parser.bad_crc; r.dropped = parser.dropped;
    rx_quiet_from = now();                             // after any command: its run time is not silence
    return 0;
}
void menu_release() {
    if (!menu_usb) return;
    menu_usb = 0;
#if defined(XYF_MTP_USB)
    if (usb_deferred) { usb_deferred = 0; return; }    // USB never started this boot: nothing to stop
    if (composite_on) {                                // our driver leaves (stop(0), unregister, frees)
        composite_on = 0;
        mu::leave();
        mu::set_mark_hook(0);
    }                                                  // then stop(1) as every boot (teardown skipped: flag 0)
#endif
    usb_stop();
    set_cdc_callback(result.stock_callback);
}

void reset_self() {
    key_ui::crash_disarm();                            // the stock exception vector, before the reset
    stock<0x11a03518u, void>(U32(*reinterpret_cast<volatile uint16_t*>(0x88a35938u)), 0u);   // stock "6. reset"
    for (;;) {}
}

// USB snapshots (diag, Tests 031-033) were removed in Test 041 for module space:
// the CDC receive cause is known; diag_count stays 0 (layout unchanged).

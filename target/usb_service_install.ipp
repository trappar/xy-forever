// Part of usb_service.cpp, included there at this point: not compiled alone.
#if defined(XYF_TFW_CHECK)
// TFW_CHECK: path NUL. Is the file an official package that this device's own keys
// open (native/tfw_check)? Reads the file through a's read-only server into a window of the file
// buffer, and the device's keys from b's OTP (otp_keys). Reply: u32 status, then (ok) u32 verdict
// (0 pass, 1 fail), u32 reason (tfw_check::Reason), the SHA-256 of the 56-byte public key read
// (zero when none was read); nothing derived from the AES key. Nothing is written or extracted.
struct CheckFile {
    const char* path;
    U32 base, bytes;
};
const U32 check_window = 0x4000u;
int check_read(void* context, U32 offset, uint8_t* out, U32 count) {
    CheckFile& f = *static_cast<CheckFile*>(context);
    U32 done = 0;
    while (done != count) {
        const U32 at = offset + done;
        if (at < f.base || at - f.base >= f.bytes) {
            tick();
            uint32_t got = 0;
            f.base = at & ~0x1ffu;
            f.bytes = 0;
            if (file_client::read_file(f.path, f.base, true, files, check_window, got, mounted) != P::s_ok) return -1;
            f.bytes = got;
            if (at - f.base >= f.bytes) break;            // the end of the file
        }
        const U32 n = count - done < f.bytes - (at - f.base) ? count - done : f.bytes - (at - f.base);
        for (U32 i = 0; i != n; ++i) out[done + i] = files[at - f.base + i];
        done += n;
    }
    return int(done);
}
void tfw_check(uint8_t seq, const uint8_t* payload, U32 length) {
    uint8_t p[4 + 8 + 32];
    for (U32 i = 0; i != sizeof(p); ++i) p[i] = 0;
    U32 path_bytes = 0;
    while (path_bytes != length && payload[path_bytes]) ++path_bytes;
    U32 code = P::s_ok;
    if (length < 10 || path_bytes + 1 != length || length > storage_server::max_path) code = P::s_bad_request;
    else if (!a_serving()) code = P::s_link;
    else {
        CheckFile f = {reinterpret_cast<const char*>(payload), 0, 0};
        uint32_t got = 0;
        code = file_client::read_file(f.path, 0, true, files, check_window, got, mounted);
        f.bytes = got;
        const xyf_core::tfw_check::File file = {&f, check_read};
        if (!code) code = tfw_device_check::run(f.path, p + 4, file);
    }
    P::put32(p, code);
    result.last_status = code;
    send(P::t_tfw_check, seq, p, code == P::s_ok ? sizeof(p) : 4);
}
void tfw_check_role(uint8_t seq, const uint8_t* payload, U32 length) {
    uint8_t reply[52] = {};
    U32 code = P::s_bad_request;
    const U32 role = length >= 8 ? P::get32(payload + 4) : ~0u;
    U32 path_bytes = 8;
    while (path_bytes < length && payload[path_bytes]) ++path_bytes;
    if (length >= 8 && P::get32(payload) != tfw_role_probe::version) code = P::s_unsupported;
    else if (length >= 18 && length <= 248 && role <= 1 && path_bytes + 1 == length) {
        const char* path = reinterpret_cast<const char*>(payload + 8);
#if defined(XYF_USB_FILE_WRITE)
        stop_idle_gc();
        if (boot_probe_active() || rehearsal_active() || job_running() || uploading() || closing XYF_OR_MTP_BUSY)
            code = P::s_busy;
        else if (writes_due) {
            settle_begin();
            code = P::s_busy;
        } else
#endif
            if (!storage_server::valid_path(path))
            code = P::s_bad_request;
        else if (!a_serving()) code = P::s_link;
        else {
            // Close B's file handles before A takes exclusive ownership of its read-only session.
            const file_client::Reply closed = file_client::unmount();
            code = closed.outcome                         ? file_client::status(closed.outcome) :
                   closed.value != 1 && closed.value != 3 ? U32(P::s_storage) :
                                                            U32(P::s_ok);
            if (!code) {
                mounted = 0;
                const file_client::Reply version = file_client::tfw_probe(path, 0);
                code = version.outcome ? file_client::status(version.outcome) :
                       version.value != int32_t(tfw_role_probe::version) || version.error ? U32(P::s_unsupported) :
                                                                                            U32(P::s_ok);
            }
            if (!code && !role) {
                const file_client::Reply checked = file_client::tfw_probe(path, 1);
                code = checked.outcome                          ? file_client::status(checked.outcome) :
                       checked.error != tfw_role_probe::version ? U32(P::s_unsupported) :
                                                                  U32(checked.value);
                for (U32 k = 0; !code && k != 5; ++k) {
                    const file_client::Reply word = file_client::tfw_probe(path, 2 + k);
                    if (word.outcome) code = file_client::status(word.outcome);
                    else {
                        P::put32(reply + 12 + 8 * k, U32(word.value));
                        P::put32(reply + 16 + 8 * k, word.error);
                    }
                }
            } else if (!code) {
                CheckFile f = {path, 0, 0};
                const xyf_core::tfw_check::File file = {&f, check_read};
                code = tfw_device_check::run(path, reply + 12, file);
            }
        }
    }
    P::put32(reply, code);
    P::put32(reply + 4, tfw_role_probe::version);
    P::put32(reply + 8, role);
    result.last_status = code;
    send(P::t_tfw_check_role, seq, reply, code == P::s_ok ? sizeof(reply) : 4);
}

#endif

// INSTALL / UNINSTALL: payload = 32-byte confirm token = SHA-256 of the
// staged image. UNINSTALL differs only in the image acceptance (the exact
// stock 1.2.1 os.ldr instead of an XY Forever build) and the reply type.
U32 write_staged(const uint8_t* payload, U32 length, bool uninstall) {
    U32 code = P::s_ok;
    result.install_written = result.install_ms = 0;
    result.verify_partition = result.verify_sectors = result.verify_mismatch = 0;
    log_open = 1;                                   // step lines and the writer's log go to the host
    if (length != 32 || !result.put_ok) code = P::s_state;
    else if (!sha_equal(payload, put_sha)) code = P::s_sha;
    else code = validate_image(put_expected, uninstall);
    if (code == P::s_ok) beacon(step_checked, put_expected);
    if (code == P::s_ok) settle_writes();                 // A's window closed before install mode
    if (code == P::s_ok && !menu_link::usb_command(storage_server::install_item)) code = P::s_link;
    const bool a_install = code == P::s_ok;         // a answered install_item: its exemption is on
    if (code == P::s_ok) {
        beacon(step_a_install, menu_link::result.answers);
        read_enabled = 1;                           // install mode also serves reads
        result.capacity = boot_capacity();
        beacon(step_capacity, result.capacity);
        if (put_expected + 512 > result.capacity) code = P::s_too_large;
    }
    if (code == P::s_ok) {
        const U32 start = now();
        lines(image, put_expected, false);          // the stock writer moves data by SPI DMA
        say(uninstall ? "Restoring stock OS 1.2.1..." : "Installing XY Forever...", "Do not unplug or power off.");
        beacon(step_writer, put_expected);
        hook_logs();
        result.install_written = write_boot(staged, put_expected);
        unhook_logs();                              // before any session step (verify ticks)
        result.install_ms = now() - start;
        // The writer may have encrypted the staging in place: never write it again.
        result.put_ok = 0;
        beacon(step_writer_done, result.install_written);
        code = result.install_written ? verify(put_expected, result.put_crc) : P::s_install;
        // A failed writer stops before its own select 0 (verify ends with one): back to the
        // user partition here, or a's engine refuses all storage until a restarts.
        if (!result.install_written)
            for (U32 tries = 0; tries != 8 && select_partition(0) != 0; ++tries) {}
        say(code == P::s_ok ? "Install finished and verified." : "Install FAILED.",
            code == P::s_ok ? "Restart to use it." : "Use the dev menu to recover if needed.");
        put_expected = 0;
        beacon(step_verify_done, code | result.verify_partition << 8 | result.verify_mismatch << 16);
    }
    log_open = 0;
    // The writer's turn is over: one more command ends a's install exemption (F1 again; a boot
    // partition still selected keeps it). A lost link here is found by the next command anyway.
    if (a_install) menu_link::usb_command(storage_server::command_item);
    result.install_status = code;
    return code;
}
void install(uint8_t seq, const uint8_t* payload, U32 length, bool uninstall) {
    const U32 code = write_staged(payload, length, uninstall);
    uint8_t p[16];
    P::put32(p, code);
    P::put32(p + 4, result.install_written);
    P::put32(p + 8, result.verify_partition);
    P::put32(p + 12, result.verify_sectors);
    result.last_status = code;
    send(uninstall ? P::t_uninstall : P::t_install, seq, p, 16);
}

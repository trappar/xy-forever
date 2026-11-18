// Part of usb_service.cpp, included there at this point: not compiled alone.
#if defined(XYF_TFW_CHECK)
// TFW_CHECK: path NUL. Is the file an official package that this device's own keys
// open (native/tfw_check)? Reads the file through a's read-only server into a window of the file
// buffer, and the device's keys from b's OTP (otp_keys). Reply: u32 status, then (ok) u32 verdict
// (0 pass, 1 fail), u32 reason (tfw_check::Reason), the SHA-256 of the 56-byte public key read
// (zero when none was read); nothing derived from the AES key. Nothing is written or extracted.
struct CheckFile { const char* path; U32 base, bytes; };
const U32 check_window = 0x4000u;
int check_read(void* context, U32 offset, uint8_t* out, U32 count) {
    CheckFile& f = *static_cast<CheckFile*>(context);
    U32 done = 0;
    while (done != count) {
        const U32 at = offset + done;
        if (at < f.base || at - f.base >= f.bytes) {
            tick();
            uint32_t got = 0;
            f.base = at & ~0x1ffu; f.bytes = 0;
            if (file_client::read_file(f.path, f.base, true, files, check_window, got, mounted) != P::s_ok)
                return -1;
            f.bytes = got;
            if (at - f.base >= f.bytes) break;            // the end of the file
        }
        const U32 n = count - done < f.bytes - (at - f.base) ? count - done : f.bytes - (at - f.base);
        for (U32 i = 0; i != n; ++i) out[done + i] = files[at - f.base + i];
        done += n;
    }
    return int(done);
}
__attribute__((noinline)) U32 tfw_check_run(const char* path, uint8_t* reply) {
    CheckFile f = {path, 0, 0};
    uint32_t got = 0;
    const U32 code = file_client::read_file(path, 0, true, files, check_window, got, mounted);
    if (code != P::s_ok) return code;                     // bad_request, refused, not_found, storage
    f.bytes = got;
    say("Checking a firmware package...", "Reads the device keys.", "If this stays, power-cycle.");
    xyf_core::tfw_check::Workspace work;
    xyf_core::tfw_check::Result r;
    const xyf_core::tfw_check::File file = {&f, check_read};
    const xyf_core::tfw_check::Keys keys = {0, otp_keys::public_key, {0, otp_keys::aes_key}};
    const xyf_core::tfw_check::Reason reason = xyf_core::tfw_check::check(file, keys, work, r);
    P::put32(reply, reason == xyf_core::tfw_check::Reason::pass ? 0u : 1u);
    P::put32(reply + 4, U32(reason));
    for (U32 i = 0; i != 32; ++i) reply[8 + i] = r.public_key_read ? r.public_key_sha256.bytes[i] : 0;
    say(reason == xyf_core::tfw_check::Reason::pass ? "Package check: pass." : "Package check: fail.");
    return P::s_ok;
}
void tfw_check(uint8_t seq, const uint8_t* payload, U32 length) {
    uint8_t p[4 + 8 + 32];
    for (U32 i = 0; i != sizeof(p); ++i) p[i] = 0;
    U32 path_bytes = 0;
    while (path_bytes != length && payload[path_bytes]) ++path_bytes;
    U32 code = P::s_ok;
    if (length < 10 || path_bytes + 1 != length || length > storage_server::max_path) code = P::s_bad_request;
    else if (!a_serving()) code = P::s_link;
    else code = tfw_check_run(reinterpret_cast<const char*>(payload), p + 4);
    P::put32(p, code);
    result.last_status = code;
    send(P::t_tfw_check, seq, p, code == P::s_ok ? sizeof(p) : 4);
}
#endif

// INSTALL / UNINSTALL: payload = 32-byte confirm token = SHA-256 of the
// staged image. UNINSTALL differs only in the image acceptance (the exact
// stock 1.2.1 os.ldr instead of an XY Forever build) and the reply type.
void install(uint8_t seq, const uint8_t* payload, U32 length, bool uninstall) {
    U32 code = P::s_ok;
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
        say("Installing XY Forever...", "Do not unplug or power off.");
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
        if (!result.install_written) for (U32 tries = 0; tries != 8 && select_partition(0) != 0; ++tries) {}
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
    uint8_t p[16];
    P::put32(p, code); P::put32(p + 4, result.install_written);
    P::put32(p + 8, result.verify_partition); P::put32(p + 12, result.verify_sectors);
    result.last_status = code;
    send(uninstall ? P::t_uninstall : P::t_install, seq, p, 16);
}

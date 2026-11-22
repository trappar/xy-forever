// A host package transaction uses the existing file RPCs, uploads and rename commit.
// Hold the existing single-job exclusion while it stages/validates/collects references:
// MTP, content jobs, boots and the startup sweep cannot race its snapshot or commit.
// No new storage operation or on-disk journal. A lost host releases after 10 s; the
// next idle sweep removes pkg-* scratch folders, never anything published.
void package_turn() {
    if (package_active && now() - package_ms > 10000u && !uploading()) {
        package_active = 0;
        boot_sweep = 0;
        settle_begin();
    }
}
void package_session(uint8_t seq, const uint8_t* payload, U32 length) {
    U32 code = P::s_ok;
    const U32 action = length == 40 ? P::get32(payload + 4) : ~0u;
    if (length != 40) code = P::s_bad_request;
    else if (P::get32(payload) != 1) code = P::s_unsupported;
    else if (action > 2) code = P::s_bad_request;
    else if (action == 0) {
        stop_idle_gc();
        if (recovery_pending || job_running() || closing || uploading() XYF_OR_MTP_BUSY) code = P::s_busy;
        else if (!a_serving()) code = P::s_link;
        else {
            for (U32 i = 0; i != 32; ++i) package_token[i] = payload[8 + i];
            package_active = 1;
            package_ms = now();
            boot_sweep = 2;
            job_usb = 0;
        }
    } else if (!package_active || !sha_equal(package_token, payload + 8)) code = P::s_state;
    else if (action == 2) {
        if (uploading()) code = P::s_busy;
        else {
            package_active = 0;
            boot_sweep = 0;
            settle_begin();
        }
    }
    if (code == P::s_ok && package_active) package_ms = now();
    status_only(P::t_package_session, seq, code);
}

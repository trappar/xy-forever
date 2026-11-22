// One quarantine seam, before probes, engine entry, mounts or FD dispatch. Product only.
#if defined(XYF_TFW_CHECK)
namespace ais = xy_forever::a_install_stage;
ais::State a_stage __attribute__((section(".data"))) = {};
void stage_refuse(U32 request, Stock121PeerSession& session) {
    const U32 cls = word(request), op = byte(request + 5);
    const U32 bytes = cls == file_engine::query_class ? (op == 0x23 ? 12u :
                                                         op == 0x31 ? 8u :
                                                                      52u) :
                      cls == sector_class             ? (op == 0x2f ? 0x10cu :
                                                         op == 0x11 ? 512u :
                                                                      0u) :
                                                        0u;
    if (bytes) {
        // Stock query/readdir/one-sector receivers require their body even on refusal.
        // Reuse the probe's existing zero sector, never staging/default scratch.
        stock<0x889f4f10u, void>(reinterpret_cast<U32>(probe_zero), bytes);
        word(request + 0x20) = reinterpret_cast<U32>(probe_zero);
        word(request + 0x24) = bytes;
    }
    session.reply(request, 0xffffffffu, error_busy);
}
bool stage_query(U32 request, Stock121PeerSession& session) {
    const U32 now = xyf::clock::ms();
    if (a_stage.expired(now)) a_stage.clear();
    const bool sentinel = word(request + 8) == ais::handle;
    if (!sentinel && !a_stage.active) return false;
    if (!sentinel &&
        peer_services::boot_request(word(request), byte(request + 5), word(request + 8), word(request + 0x18))) {
        a_stage.clear();
        return false; // preserve the existing automatic bootloader-follow path
    }
    // Even a malformed or inactive sentinel is never a file handle, in ANY class/op.
    if (!sentinel || word(request) != write_class || byte(request + 5) != 0x2b) {
        a_stage.clear();
        stage_refuse(request, session);
        return true;
    }
    // Stock 889f87f2 stores write count at +0x18; +0xc belongs to the request queue.
    if (word(request + 0x24) < ais::header_bytes || word(request + 0x24) > file_client::piece_max ||
        word(request + 0x18) != word(request + 0x24) || !word(request + 0x20)) {
        a_stage.clear();
        session.reply(request, xyf::usbp::s_bad_request, 0);
        return true;
    }
    const uint8_t* const p = reinterpret_cast<const uint8_t*>(word(request + 0x20));
    const U32 op = xyf::usbp::get32(p + 8);
    if ((op == ais::begin || op == ais::preflight) && xyf::usbp::get32(p) == ais::magic &&
        xyf::usbp::get32(p + 4) == ais::version) {
        // Invalidate the normal boot lease BEFORE the first staging mutation.
        if (op == ais::begin) file_boot::discard_prepared();
        if (probe_mode || boot_selected() || window_open || engine != entry_read ||
            (op == ais::begin && held_files())) {
            a_stage.clear();
            session.reply(request, xyf::usbp::s_busy, 0);
            return true;
        }
    }
    U32 value = 0;
    U32 code = a_stage.apply(p, word(request + 0x24), reinterpret_cast<uint8_t*>(file_boot::staging),
                             file_boot::staging_bytes, now, value);
    // Digest words occupy both reply words; all other operations reply status then progress/CRC.
    if (xyf::usbp::get32(p + 8) == ais::digest && a_stage.admitted) session.reply(request, value, code);
    else session.reply(request, code, value);
    return true;
}
#else
bool stage_query(U32, Stock121PeerSession&) { return false; }
#endif

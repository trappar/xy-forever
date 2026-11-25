// Part of usb_service.cpp, included there at this point: not compiled alone.
// #26: the saved inputs over USB, the Mods page's own (xyf_mods_io config_view, config_set); a build without the page:
// unsupported. CONFIG_GET: u32 version. Reply: u32 status, version, record, count, flags, saving, base NUL,
// generation NUL, the mods= line. CONFIG_SET: u32 version, u32 flags, mods NUL, SHA-256 of every byte before it.
// Reply: u32 status, u32 version, the reason (refused).
void config_get(uint8_t seq, const uint8_t* payload, U32 length) {
    U32 code = length != 4 ? P::s_bad_request : P::get32(payload) != 1 ? P::s_unsupported : P::s_ok;
#if defined(XYF_MODS_PAGE)
    xy_forever::mods_io::ConfigView v;
    if (code == P::s_ok) code = xy_forever::mods_io::config_view(v);
#else
    if (code == P::s_ok) code = P::s_unsupported;
#endif
    result.last_status = code;
    if (code != P::s_ok) {
        status_only(P::t_config_get, seq, code);
        return;
    }
#if defined(XYF_MODS_PAGE)
    uint8_t* const p = files;
    const U32 words[6] = {code, 1, v.record, v.count, v.flags, v.saving};
    for (U32 i = 0; i != 6; ++i) P::put32(p + 4 * i, words[i]);
    U32 at = 24;
    const char* const texts[2] = {v.base, v.generation};
    for (U32 k = 0; k != 2; ++k) {
        for (U32 i = 0; texts[k][i] && i != 31; ++i) p[at++] = uint8_t(texts[k][i]);
        p[at++] = 0;
    }
    for (U32 i = 0; i != v.mods_bytes && i != 2048; ++i) p[at++] = uint8_t(v.mods[i]);
    send(P::t_config_get, seq, p, at);
#endif
}
void config_set(uint8_t seq, const uint8_t* payload, U32 length) {
    U32 code = length < 41 || length > 2088 ? P::s_bad_request :
               P::get32(payload) != 1       ? P::s_unsupported :
               P::get32(payload + 4) > 7    ? P::s_bad_request :
                                              P::s_ok;
    U32 end = 8;
    if (code == P::s_ok) {
        while (end < length && payload[end] && payload[end] < 0x80) ++end;
        if (end + 1 + 32 != length || payload[end]) code = P::s_bad_request;
    }
    if (code == P::s_ok) {
        xyf_core::hash::Sha256 h;
        xyf_core::hash::Digest d;
        if (!h.update(payload, end + 1) || !h.finish(d) || !sha_equal(d.bytes, payload + end + 1)) code = P::s_sha;
    }
    const char* reason = "";
#if defined(XYF_MODS_PAGE)
    if (code == P::s_ok)
        code = xy_forever::mods_io::config_set(P::get32(payload + 4), reinterpret_cast<const char*>(payload + 8),
                                               xyf::crc::update(0, payload, length), reason);
#else
    if (code == P::s_ok) code = P::s_unsupported;
#endif
    result.last_status = code;
    if (code == P::s_busy) {
        status_only(P::t_config_set, seq, code);
        return;
    }   // as any command's busy
    uint8_t* const p = files;
    P::put32(p, code);
    P::put32(p + 4, 1);
    U32 at = 8;
    for (U32 i = 0; reason[i] && i != 80; ++i) p[at++] = uint8_t(reason[i]);
    send(P::t_config_set, seq, p, at);
}
// #204 MENU_CONVERGE: u32 version, u32 mode (0 report, 1 converge to the saved inputs then boot, 2 boot library entry
// id once with its own content), id NUL (mode 2; else empty), SHA-256 of every byte before it. Reply: u32 status,
// u32 version, state, job, the reason (state 2). Modes 1 and 2 answer at once; the sequence runs on the menu's progress
// screen, its jobs followed with job.status, and the boot ends USB.
void menu_converge(uint8_t seq, const uint8_t* payload, U32 length) {
    U32 code = length < 41 || length > 72 ? P::s_bad_request :
               P::get32(payload) != 1     ? P::s_unsupported :
               P::get32(payload + 4) > 2  ? P::s_bad_request :
                                            P::s_ok;
    U32 end = 8;
    const char* id = reinterpret_cast<const char*>(payload + 8);
    if (code == P::s_ok) {
        while (end < length && payload[end]) ++end;
        const bool named = end != 8;
        if (end + 1 + 32 != length || named != (P::get32(payload + 4) == 2) ||
            (named && (end - 8 > xyf::store::max_id || !xyf::store::valid_id(id))))
            code = P::s_bad_request;
    }
    if (code == P::s_ok) {
        xyf_core::hash::Sha256 h;
        xyf_core::hash::Digest d;
        if (!h.update(payload, end + 1) || !h.finish(d) || !sha_equal(d.bytes, payload + end + 1)) code = P::s_sha;
    }
#if defined(XYF_MODS_PAGE)
    if (code == P::s_ok && P::get32(payload + 4)) {
        if (!menu_link::usb_link_up()) code = P::s_link;
        else if (page_busy()) code = P::s_busy;
        else {
            if (P::get32(payload + 4) == 2) settle_writes(); // A's window closed, the catalog fetched again
            code = xy_forever::mods_io::converge_request(P::get32(payload + 4), id);
        }
    }
#else
    if (code == P::s_ok) code = P::s_unsupported;
#endif
    result.last_status = code;
    if (code != P::s_ok) {
        status_only(P::t_menu_converge, seq, code);
        return;
    }
#if defined(XYF_MODS_PAGE)
    xy_forever::mods_io::ConvergeView v;
    xy_forever::mods_io::converge_report(v);
    uint8_t* const p = files;
    const U32 words[4] = {code, 1, v.state, v.job};
    for (U32 i = 0; i != 4; ++i) P::put32(p + 4 * i, words[i]);
    U32 at = 16;
    for (U32 i = 0; v.reason[i] && i != 80; ++i) p[at++] = uint8_t(v.reason[i]);
    send(P::t_menu_converge, seq, p, at);
#endif
}

// Part of xyf_mods_io.cpp: config.get and config.set (#26, usb_service_config.ipp), the saved inputs over USB, included
// in place (in its anonymous namespace, after xyf_mods_save.ipp). config.set is a flip on the page: the toggles, Mod
// memory and safe mode set, then the page's own write (pick_step) at its quiet moment. It never builds or converges.
// Remote: the package list read again for one set: stage 1 reading for the request whose CRC-32 is crc; 2, read
// (since: units() then), kept for that request 5 s.
struct Remote {
    uint32_t stage, crc, since;
};
Remote remote XYF_DATA = {};
char remote_reason[80] XYF_DATA = {};
static const uint32_t remote_units = 5000u * 390u;
// A refusal: the toggles back to the saved marks (the list was just read: nothing else was on), reason said.
uint32_t refuse(const char*& reason, const char* a, const char* b = "") {
    m::Page& p = the_page;
    for (uint32_t r = 0; r != p.shown; ++r) {
        m::Package& k = p.packages[p.rows[r]];
        k.flags = uint8_t((k.flags & ~m::flag_on) | (k.flags & m::flag_saved ? m::flag_on : 0));
    }
    p.expanded = p.expanded_saved;
    p.unknown_ok = false;
    xg::Out o = {remote_reason, remote_reason + sizeof(remote_reason) - 1, true};
    xg::put(o, a);
    xg::put(o, b);
    *o.at = 0;
    reason = remote_reason;
    return xyf::usbp::s_refused;
}
// The shown package named name[0..n) (an extension as ext-<id>), or ~0u.
uint32_t package_named(const char* name, uint32_t n) {
    const m::Page& p = the_page;
    const bool ext = n > 4 && xg::same_n(name, "ext-", 4);
    for (uint32_t r = 0; r != p.shown; ++r) {
        const m::Package& k = p.packages[p.rows[r]];
        if ((k.kind == m::kind_extension) != ext) continue;
        const char* id = m::text_at(p, k.id);
        uint32_t i = ext ? 4u : 0u, j = 0;
        while (i != n && id[j] && id[j] == name[i]) ++i, ++j;
        if (i == n && !id[j]) return p.rows[r];
    }
    return ~0u;
}
uint32_t remote_set(uint32_t flags, const char* mods, uint32_t crc, const char*& reason) {
    m::Page& p = the_page;
    reason = "";
    if (sv.stage != sv_idle || pick.flipped == 1 || pick.stage == 1 || usb_service::page_busy())
        return xyf::usbp::s_busy;
    if (usb_service::page_serving() != s_ok) return xyf::usbp::s_link;
    if (remote.stage == 1 && !reading()) {
        remote.stage = 2;
        remote.since = units();
    }
    if (remote.stage == 1) return xyf::usbp::s_busy;
    if (remote.stage != 2 || remote.crc != crc || units() - remote.since > remote_units) { // read the list again
        if (io.folder) {
            fc::closedir(io.folder);
            io.folder = 0;
        }
        fc::close_name_scan(io.metadata_scan, usb_service::page_mount());
        restart_config_lookup();
        m::reset(p);
        io.step = s_serving;
        io.candidate_count = io.package_at = io.newer = io.broken = io.development = 0;
        io.dirty = 1;
        io.waiting = 0;
        m::say(p, m::line_info, "Reading mod packages...");
        remote.stage = 1;
        remote.crc = crc;
        return xyf::usbp::s_busy;
    }
    remote.stage = 0;
    if (io.incomplete && io.why == xyf::usbp::s_link) return xyf::usbp::s_link;
    if (io.incomplete) return refuse(reason, p.text);
    for (uint32_t r = 0; r != p.shown; ++r) p.packages[p.rows[r]].flags &= uint8_t(~m::flag_on);
    for (uint32_t at = 0; mods[at];) { // every name a package of the list
        uint32_t end = at;
        while (mods[end] && mods[end] != ',') ++end;
        const uint32_t k = package_named(mods + at, end - at);
        if (k == ~0u) {
            char name[32];
            uint32_t n = 0;
            for (; n != end - at && n + 1 < sizeof(name); ++n) name[n] = mods[at + n];
            name[n] = 0;
            return refuse(reason, "Not in the mod packages: ", name);
        }
        p.packages[k].flags |= m::flag_on;
        at = mods[end] ? end + 1 : end;
    }
    p.expanded = (flags & 1u) != 0;
    p.unknown_ok = (flags & 4u) != 0;
    if (const char* why = m::save_refusal(p)) return refuse(reason, why);
    if (!config_base()) return refuse(reason, "No stock OP-XY firmware in the library.");
    if (m::chosen(p, false) +
        m::chosen(p, true)) { // #183: a build needs the base's content set, or the means to make it
        const char* why = 0;
        const uint32_t code = usb_service::content_set_check(config_base(), why);
        if (why) return refuse(reason, why);
        if (code != s_ok) {
            refuse(reason, ""); // the toggles back
            reason = "";
            return code;
        }
    }
    if ((flags & 2u) && m::chosen(p, false) + m::chosen(p, true) && !(rec.state == xg::present && rec.slot[0]))
        return refuse(reason, "Nothing is built yet to boot without: set no mods instead.");
    pick.safe = flags & 2u ? 2u : 1u;
    pick.flipped = 1;
    pick.since = units(); // written at the quiet moment, as a flip on the page
    return s_ok;
}
uint32_t remote_view(ConfigView& v) {
    if (sv.stage != sv_idle || usb_service::page_busy()) return xyf::usbp::s_busy;
    if (usb_service::page_serving() != s_ok) return xyf::usbp::s_link;
    uint32_t bytes = 0, n = 0;
    const uint32_t code = read_record(false, bytes);
    if (code != s_ok) return code;
    const bool have = rec.state == xg::present;
    const char* base = config_base();
    v.record = have ? 1u : 0u;
    v.count = have ? rec.count : 0u;
    v.flags = have ? (rec.expanded ? 1u : 0u) | (rec.safe ? 2u : 0u) | (rec.unverified ? 4u : 0u) : 0u;
    v.saving = pick.flipped == 2 ? 2u : pick.flipped || pick.stage == 1 ? 1u : 0u;
    v.base = base ? base : "";
    v.generation = have ? rec.generation : "";
    v.mods = have ? xg::value(reinterpret_cast<const char*>(io.buffer), bytes, "mods", n) : 0;
    v.mods_bytes = v.mods ? n : 0u;
    return s_ok;
}

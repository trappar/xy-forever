void restart_config_lookup() { io.config_scan.skip = io.config_scan.choice = 0; }

uint32_t read_record(bool toggles, uint32_t& bytes) {
    namespace P = xyf::usbp;
    auto& work = *static_cast<MetadataWork*>(usb_service::mods_metadata_work());
    bool native = false;
    const uint32_t code = config_io::read(io.config_scan, work.raw, bytes, native, usb_service::page_mount());
    if (code == P::s_busy) return code;
    io.config_error = nullptr;
    if (code == P::s_not_found) {
        xg::clear(rec, xg::none);
        bytes = 0;
    } else if (code != s_ok) {
        io.config_error = "Could not read the saved configuration.";
        xg::clear(rec, xg::failed);
        return code;
    } else {
        if (native) {
            const json::Document doc =
                json::parse(reinterpret_cast<const char*>(work.raw), bytes, work.parse.json, json::config_schema);
            const json::Status status = json::config_text(doc, reinterpret_cast<char*>(io.buffer), read_bytes, bytes);
            if (status != json::ok) {
                io.config_error = status == json::version ? "Saved configuration needs a newer XY Forever." :
                                                            "Invalid saved configuration in config.json or its backup.";
                xg::clear(rec, xg::failed);
                return status == json::version ? P::s_unsupported : P::s_refused;
            }
        } else if (!xg::supported_format(reinterpret_cast<const char*>(work.raw), bytes)) {
            io.config_error = "Saved configuration uses an unsupported legacy format.";
            xg::clear(rec, xg::failed);
            return P::s_unsupported;
        }
        if (!native)
            for (uint32_t i = 0; i != bytes; ++i) io.buffer[i] = work.raw[i];
        xg::parse(reinterpret_cast<const char*>(io.buffer), bytes, rec);
    }
    uint32_t n = 0;
    const char* list =
        rec.state == xg::present ? xg::value(reinterpret_cast<const char*>(io.buffer), bytes, "mods", n) : 0;
    rec_set = 0;
    for (uint32_t at = 0; list && at < n;) {
        uint32_t end = at;
        while (end < n && list[end] != ',') ++end;
        if (end > at) rec_set += xyf::crc::update(0, reinterpret_cast<const uint8_t*>(list + at), end - at);
        at = end + 1;
    }
    m::Page& p = the_page;
    uint32_t marked = 0;
    for (uint32_t r = 0; r != p.shown; ++r) {
        m::Package& k = p.packages[p.rows[r]];
        const bool on =
            rec.state == xg::present && xg::listed(reinterpret_cast<const char*>(io.buffer), bytes,
                                                   k.kind == m::kind_extension ? "ext-" : "", m::text_at(p, k.id));
        k.flags = uint8_t((k.flags & ~m::flag_saved) | (on ? m::flag_saved : 0));
        if (toggles) k.flags = uint8_t((k.flags & ~m::flag_on) | (on ? m::flag_on : 0));
        marked += on ? 1u : 0u;
    }
    p.saved_missing = rec.count > marked ? rec.count - marked : 0u;
    if (pick.stage == 2) pick.stage = 0;
    // The saved memory layout; the pending choice follows it unless the user changed it (Settings may
    // come before the list is read)
    const bool kept = p.expanded != p.expanded_saved;
    p.expanded_saved = rec.state == xg::present && rec.expanded;
    if (!kept) p.expanded = p.expanded_saved;
    return s_ok;
}

bool config_build(const xg::Record& record, json::ConfigBuild& build) {
    if (!record.slot[0] || !record.stamp[0] || !record.built[0]) return false;
    const char* p = record.built;
    char* const names[] = {build.base, build.generation};
    for (char* name : names) {
        uint32_t n = 0;
        while (*p && *p != ' ' && n != 31) name[n++] = *p++;
        if (!n || *p != ' ') return false;
        name[n] = 0;
        ++p;
    }
    build.set = 0;
    for (uint32_t i = 0; i != 8; ++i) {
        const char c = *p++;
        if (c >= '0' && c <= '9') build.set = (build.set << 4) | uint32_t(c - '0');
        else if (c >= 'a' && c <= 'f') build.set = (build.set << 4) | uint32_t(c - 'a' + 10);
        else return false;
    }
    if (*p++ != ' ' || *p < '0' || *p > '9') return false;
    build.count = 0;
    do {
        build.count = build.count * 10 + uint32_t(*p++ - '0');
        if (build.count > json::list_count) return false;
    } while (*p >= '0' && *p <= '9');
    build.expanded = xg::same(p, " expanded");
    if (*p && !build.expanded) return false;
    build.slot = record.slot;
    build.sha256 = record.stamp;
    return true;
}

uint32_t encode_config(const xg::Record& record, const char* mods, bool require_build) {
    json::ConfigBuild build;
    const bool has_build = config_build(record, build);
    if (require_build && !has_build) return 0;
    const json::Config fields = {
        record.base,      record.profile, record.generation,           record.expanded != 0, record.unverified != 0,
        record.safe != 0, mods,           has_build ? &build : nullptr};
    auto& work = *static_cast<MetadataWork*>(usb_service::mods_metadata_work());
    char* const raw = reinterpret_cast<char*>(work.raw);
    uint32_t n = 0, view_bytes = 0;
    if (json::config_document(fields, raw, sizeof(work.raw), n) != json::ok) return 0;
    const json::Document doc = json::parse(raw, n, work.parse.json, json::config_schema);
    if (json::config_text(doc, work.text, read_bytes, view_bytes) != json::ok) return 0;
    // Convergence keeps the prepared record here while the content job owns the shared workspace.
    for (uint32_t i = 0; i != n; ++i) io.buffer[i] = work.raw[i];
    return n;
}

uint32_t record_text(const xg::Record& record, uint8_t which) {
    const m::Page& page = the_page;
    char* const mods = reinterpret_cast<char*>(io.buffer);
    xg::Out selection = {mods, mods + read_bytes - 1, true};
    for (uint32_t pass = 0; pass != 2; ++pass)
        for (uint32_t k = 0; k != page.shown; ++k) {
            const m::Package& q = page.packages[page.rows[k]];
            if (!(q.flags & which) || (q.kind == m::kind_extension) != (pass == 1)) continue;
            if (selection.at != mods) xg::put(selection, ",");
            if (pass) xg::put(selection, "ext-");
            xg::put(selection, m::text_at(page, q.id));
        }
    if (!selection.ok) return 0;
    *selection.at = 0;
    return encode_config(record, mods, which == m::flag_saved);
}

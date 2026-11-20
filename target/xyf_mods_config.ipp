void restart_config_lookup() {
    io.config_skip = io.config_choice = 0;
}

const char* const config_names[] = {"config.json", "config.json.old", "config.txt", "config.old"};

uint32_t find_config(MetadataWork& work) {
    namespace P = xyf::usbp;
    const char* root = "/yaffs2/xy-forever";
    volatile uint32_t& mount = usb_service::page_mount();
    uint32_t code = fc::mount_ready(mount);
    if (code != s_ok) return code;
    if (!io.config_skip) {
        uint8_t record[fc::record_bytes];
        const fc::Reply r = fc::stat(root, record);
        if (r.outcome) return failed(r);
        if (r.value) return fc::change_status(r.error);
        if ((P::get32(record + 8) & 0xf000u) != 0x4000u) return P::s_storage;
    }
    // The page read closes its directory before returning, so other work can
    // open A's write window between configuration reads.
    uint32_t count = 0, more = 0, bytes = 0;
    code = fc::list(root, io.config_skip, work.raw, sizeof(work.raw), count, more, bytes, mount);
    if (code != s_ok) return code == P::s_not_found ? P::s_storage : code;
    if (more && !count) return P::s_storage;
    for (uint32_t at = 0; at < bytes; at += 6 + work.raw[at + 5]) {
        const uint8_t n = work.raw[at + 5];
        const char* name = reinterpret_cast<const char*>(work.raw + at + 6);
        for (uint32_t k = 0; k != 4 && (io.config_choice == 0 || k + 1u < io.config_choice); ++k) {
            const char* expected = config_names[k];
            uint32_t len = 0;
            while (expected[len]) ++len;
            if (len == n && xg::same_n(name, expected, n)) io.config_choice = k + 1u;
        }
    }
    io.config_skip += count;
    if (more && io.config_choice != 1) return P::s_busy;
    return io.config_choice ? s_ok : P::s_not_found;
}

// Current native record, its rollback, then the two legacy names. A selected
// record's error ends the lookup.
uint32_t read_record(bool toggles) {
    namespace P = xyf::usbp;
    const char* const paths[] = {xg::path, xg::old_path, xg::legacy_path, xg::legacy_old_path};
    uint32_t bytes = 0;
    auto& work = *static_cast<MetadataWork*>(usb_service::mods_metadata_work());
    uint32_t code = find_config(work);
    if (code == P::s_busy) return code;
    const uint32_t chosen = io.config_choice;
    restart_config_lookup();
    const bool native = chosen && chosen <= 2;
    if (code == s_ok) {
        const char* path = paths[chosen - 1u];
        uint8_t stat[fc::record_bytes];
        const fc::Reply r = fc::stat(path, stat);
        if (r.outcome) code = failed(r);
        else if (r.value || (P::get32(stat + 8) & 0xf000u) != 0x8000u) code = P::s_storage;
        else code = read_selected(path, native ? work.raw : io.buffer, native ? sizeof(work.raw) : read_bytes, bytes);
    }
    io.config_error = nullptr;
    if (code == P::s_not_found) { xg::clear(rec, xg::none); bytes = 0; }
    else if (code != s_ok) {
        io.config_error = "Could not read the saved configuration.";
        xg::clear(rec, xg::failed);
        return code;
    } else {
        if (native) {
            const json::Document doc = json::parse(reinterpret_cast<const char*>(work.raw), bytes, work.parse.json, json::config_schema);
            const json::Status status = json::config_text(doc, reinterpret_cast<char*>(io.buffer), read_bytes, bytes);
            if (status != json::ok) {
                io.config_error = status == json::version ? "Saved configuration needs a newer XY Forever." :
                                  "Invalid saved configuration in config.json or its backup.";
                xg::clear(rec, xg::failed);
                return status == json::version ? P::s_unsupported : P::s_refused;
            }
        } else if (!xg::supported_format(reinterpret_cast<const char*>(io.buffer), bytes)) {
            io.config_error = "Saved configuration uses an unsupported legacy format.";
            xg::clear(rec, xg::failed);
            return P::s_unsupported;
        }
        xg::parse(reinterpret_cast<const char*>(io.buffer), bytes, rec);
    }
    m::Page& p = the_page;
    uint32_t marked = 0;
    for (uint32_t r = 0; r != p.shown; ++r) {
        m::Package& k = p.packages[p.rows[r]];
        const bool on = rec.state == xg::present &&
                        xg::listed(reinterpret_cast<const char*>(io.buffer), bytes, k.kind == m::kind_extension ? "ext-" : "",
                                   m::text_at(p, k.id));
        k.flags = uint8_t((k.flags & ~m::flag_saved) | (on ? m::flag_saved : 0));
        if (toggles) k.flags = uint8_t((k.flags & ~m::flag_on) | (on ? m::flag_on : 0));
        marked += on ? 1u : 0u;
    }
    p.saved_missing = rec.count > marked ? rec.count - marked : 0u;
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
    build.slot = record.slot; build.sha256 = record.stamp;
    return true;
}

uint32_t write_config(const xg::Record& record, const m::Page& page, bool built, char*& raw) {
    char* const mods = reinterpret_cast<char*>(io.buffer);
    xg::Out selection = {mods, mods + read_bytes - 1, true};
    for (uint32_t pass = 0; pass != 2; ++pass)
        for (uint32_t k = 0; k != page.shown; ++k) {
            const m::Package& q = page.packages[page.rows[k]];
            if (!(q.flags & m::flag_on) || (q.kind == m::kind_extension) != (pass == 1)) continue;
            if (selection.at != mods) xg::put(selection, ",");
            if (pass) xg::put(selection, "ext-");
            xg::put(selection, m::text_at(page, q.id));
        }
    if (!selection.ok) return 0;
    *selection.at = 0;
    json::ConfigBuild build;
    const bool has_build = config_build(record, build);
    if (built && !has_build) return 0;
    const json::Config fields = {record.base, record.profile, record.generation, record.expanded != 0,
                                 record.unverified != 0, record.safe != 0, mods, has_build ? &build : nullptr};
    auto& work = *static_cast<MetadataWork*>(usb_service::mods_metadata_work());
    raw = reinterpret_cast<char*>(work.raw);
    uint32_t n = 0, view_bytes = 0;
    if (json::config_document(fields, raw, sizeof(work.raw), n) != json::ok) return 0;
    const json::Document doc = json::parse(raw, n, work.parse.json, json::config_schema);
    return json::config_text(doc, work.text, read_bytes, view_bytes) == json::ok ? n : 0;
}

void step() {
    m::Page& p = the_page;
    switch (io.step) {
    case s_serving:
        if (usb_service::page_serving() != s_ok) {
            io.why = xyf::usbp::s_link;
            lost();
            return;
        }
        io.step = s_mount;
        return;
    case s_mount:
        if ((io.why = fc::mount_ready(usb_service::page_mount())) != s_ok) {
            lost();
            return;
        }
        {
            const char* parts[1] = {root};
            path_of(parts, 1);
        }
        io.step = s_generations;
        return;
    case s_generations: {                        // mods/<generation>/ (an older mods/<profile>/ has no base/)
        const Walk w = walk();
        if (w == walk_entry) {
            each_generation(entry_name());
            return;
        }
        if (w == walk_more) return;
        if (w == walk_failed) {
            lost();
            return;
        }
        if (w == walk_missing || !io.candidate_count) {
            finish(0);
            return;
        }   // no mods/ folder: the empty page
        io.candidate_at = 0;
        io.step = s_store;
        return;
    }
    case s_store: {                              // store.txt's version (#124); none: an older layout, skipped
        uint32_t bytes = 0;
        generation_path("", "", xyf::mods_store::file);
        if (!read_text(io.path, bytes)) {
            if (io.why == xyf::usbp::s_link) {
                lost();
                return;
            }
            next_candidate();
            return;
        }
        const uint32_t v = xyf::mods_store::version_of(io.buffer, bytes);
        io.newer |= v > xyf::mods_store::version ? 1u : 0u;
        const bool development = v == xyf::mods_store::version && xyf::mods_store::development(io.buffer, bytes);
        io.development += development ? 1u : 0u;
        if (v == xyf::mods_store::version && !development) io.step = s_base_list;
        else next_candidate();
        return;
    }
    case s_base_list:                            // complete: its files.sha256 is there
        if (!generation_path("", "base", "files.sha256")) {
            next_candidate();
            return;
        }
        if (present(io.path)) {
            io.step = s_base_native;
            return;
        }
        if (io.why != s_ok) {
            lost();
            return;
        }
        next_candidate();
        return;
    case s_base_native:
    case s_base_info: {
        const bool native = io.step == s_base_native;
        uint32_t bytes = 0;
        generation_path("", "base", native ? record::native_name : record::legacy_name);
        const char* text = metadata(native, xyf::manifest::type_mod_base, bytes);
        if (io.why == xyf::usbp::s_busy) return;
        if (native && io.why == xyf::usbp::s_not_found) {
            io.step = s_base_info;
            return;
        }
        if (io.why == xyf::usbp::s_link) {
            lost();
            return;
        }
        if (!text || !m::parse_base(p, text, bytes)) {
            io.broken |= native;
            next_candidate();
            return;
        }
        io.step = s_services;
        return;
    }
    case s_services: {
        uint32_t bytes = 0;
        generation_path("", "base", "services.txt");
        if (!read_text(io.path, bytes)) {
            if (io.why == xyf::usbp::s_link) {
                lost();
                return;
            }
            next_candidate();
            return;
        }
        if (!m::parse_services(p, reinterpret_cast<const char*>(io.buffer), bytes)) {
            next_candidate();
            return;
        }
        const char *g = p.generation, *folder = io.candidates[io.candidate_at].name;   // the same name
        uint32_t k = 0;
        while (g[k] && g[k] == folder[k]) ++k;
        if (g[k] != folder[k]) {
            next_candidate();
            return;
        }
        generation_path("", "", "");                 // base/info.txt's profile; its generation names the folder
        io.step = s_packages;
        io.dirty = 1;
        return;
    }
    case s_packages: {
        const Walk w = walk();
        if (w == walk_entry) {
            m::add_package(p, entry_name());
            return;
        }
        if (w == walk_more) return;
        if (w != walk_done) {
            lost();
            return;
        }
        io.package_at = 0;
        io.step = s_package_list;
        m::reading_line(p, io.package_at);
        return;
    }
    case s_package_list:
        if (io.package_at >= p.count) {
            io.step = s_saved;
            return;
        }
        if (package_path(io.package_at, "files.sha256") && present(io.path)) {
            io.step = s_package_native;
            return;
        }
        if (io.why != s_ok) {
            lost();
            return;
        }
        ++io.package_at;                         // incomplete: not shown
        return;
    case s_package_native:
    case s_package_info: {
        const bool native = io.step == s_package_native;
        uint32_t bytes = 0;
        package_path(io.package_at, native ? record::native_name : record::legacy_name);
        const char* text = metadata(native, xyf::manifest::type_mod, bytes);
        if (io.why == xyf::usbp::s_busy) return;
        if (native && io.why == xyf::usbp::s_not_found) {
            io.step = s_package_info;
            return;
        }
        if (io.why == xyf::usbp::s_link) {
            lost();
            return;
        }
        if (!text || !m::parse_package(p, io.package_at, text, bytes)) io.broken |= native;
        ++io.package_at;
        io.step = s_package_list;
        m::reading_line(p, io.package_at);
        io.dirty = 1;
        return;
    }
    case s_saved: {                              // the saved selection onto the toggles
        uint32_t bytes = 0;
        const uint32_t code = read_record(true, bytes);
        if (code == xyf::usbp::s_busy) return;
        if (code == xyf::usbp::s_link) {
            io.why = code;
            lost();
            return;
        }
        finish(io.config_error);
        return;
    }
    default: return;
    }
}

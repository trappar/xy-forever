// The simulator drives the same Settings and content-pack readers as key_ui.
#if defined(XYF_SETTINGS)
void sim_settings_pass(bool& settings_logged) {
    using namespace xy_forever;
    const char* settings_line = 0;
    const bool settings_moved = usb_service::settings_view(sim_app, settings_line);   // as key_ui.cpp
    if (settings_line) xyf_sim::log("menu: line: %s", settings_line);
    const bool settings_first = !settings_logged && usb_service::settings_load();   // the record read: once
    if (settings_first || (settings_moved && settings_logged && sim_app.screen == xyf::menu::Screen::settings)) {
        const xyf::settings::Record& s = usb_service::settings();
        xyf_sim::log("settings: confirm=%u power_on=%u usb=%u newer=%u; note=%s", s.confirm, s.power_on, s.usb, s.newer,
                     sim_app.settings_note ? sim_app.settings_note : "");
        settings_logged = true;
    }
    if (settings_moved && sim_app.screen == xyf::menu::Screen::packs && sim_app.pack_ready) {
        if (sim_app.pack_result) xyf_sim::log("pack result: %s", sim_app.pack_result);
        for (uint32_t i = 0; i < sim_app.pack_count; ++i) xyf_sim::log("pack id: %s", sim_app.pack_names[i]);
        xyf_sim::log("pack page %u: %u entries, more=%u", sim_app.pack_page, sim_app.pack_count,
                     unsigned(sim_app.pack_more));
    }
}
#endif

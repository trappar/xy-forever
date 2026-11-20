// Part of sim/devices.cpp: the menu's and the Mods page's keys (as key_ui dispatch), included in place.
// The page on screen gets the key (key_ui dispatch): the Mods page or the menu.
void dispatch(uint32_t key, uint32_t pressed) {
#if defined(XYF_MODS_PAGE)
    using namespace xy_forever;
    if (sim_mods) {                                                    // as key_ui's on_key
        const xyf::mods::Effect effect = mods_io::key(key, pressed != 0);
        if (effect == xyf::mods::effect_leave) sim_mods = false;
        if (effect != xyf::mods::effect_none) log_page();
        return;
    }
    if (sim_menu) {
        const xyf::menu::Action action = xyf::menu::key(sim_app, key, pressed != 0);
        if (action == xyf::menu::Action::open_mods) { sim_mods = true; mods_io::open(); log_page(); return; }
        if (action == xyf::menu::Action::continue_boot || action == xyf::menu::Action::discard ||   // as key_ui
            action == xyf::menu::Action::safe_mode || action == xyf::menu::Action::restore ||
            action == xyf::menu::Action::save) {                       // and Save
            if (const char* busy = usb_service::menu_busy()) {        // key_ui: the line, back on the home rows
                sim_app.screen = xyf::menu::Screen::main; xyf_sim::log("menu: line: %s", busy); return;
            }
        }
        if (action == xyf::menu::Action::continue_boot || action == xyf::menu::Action::discard) {   // as key_ui
            xyf_sim::log("menu: %s", action == xyf::menu::Action::discard ? "discard" : "continue");
            sim_leave = true;
            return;
        }
#if defined(XYF_SETTINGS)
        using namespace xyf::settings;
        if (action == xyf::menu::Action::change_setting) {             // as key_ui (#21: the option list)
            if (sim_app.entry != row_memory) sim_app.settings_note = usb_service::settings_open(sim_app, sim_app.entry);
            else if (sim_app.memory == memory_standard || sim_app.memory == memory_expanded)
                xyf::menu::open_options(sim_app, memory_options, 2, sim_app.memory - memory_standard);
            else sim_app.settings_note = mods_io::memory_flip();       // why not (it changes nothing)
            xyf_sim::log("settings: play on row %u: note=%s", sim_app.entry, sim_app.settings_note ? sim_app.settings_note : "");
            if (sim_app.screen == xyf::menu::Screen::options) xyf_sim::log("settings: options current %u", sim_app.option_current);
            return;
        }
        if (action == xyf::menu::Action::pick_setting) {               // as key_ui
            sim_app.settings_note = 0;
            if (sim_app.entry != row_memory) usb_service::settings_pick(sim_app);
            else if (sim_app.option_selected != sim_app.option_current) sim_app.settings_note = mods_io::memory_flip();
            xyf_sim::log("settings: pick on row %u: option %u", sim_app.entry, sim_app.option_selected);
            return;
        }
#endif
        if (action == xyf::menu::Action::save) {                       // as key_ui
            xyf_sim::log("menu: save");
            sim_mods = true; mods_io::save_begin(); log_page();
            return;
        }
        if (action == xyf::menu::Action::safe_mode || action == xyf::menu::Action::restore) {   // as key_ui
            xyf_sim::log("menu: %s", action == xyf::menu::Action::restore ? "restore" : "safe mode");
            sim_mods = true; mods_io::safe_begin(action == xyf::menu::Action::restore); log_page();
            return;
        }
#if defined(XYF_CONTENT_SWITCH)
        if (action == xyf::menu::Action::undo) {                       // as key_ui (the first row's PLAY)
            const char* line = usb_service::content_undo();
            xyf_sim::log("menu: undo: %s", line ? line : "-");
            return;
        }
#endif
        if (action == xyf::menu::Action::redraw) info_drawn();
        if (action == xyf::menu::Action::redraw && sim_app.note)        // 193: a row refused while storage starts
            xyf_sim::log("menu: line: %s", sim_app.note);
        else if (action == xyf::menu::Action::redraw)
            xyf_sim::log("menu: screen %u row %u choice %u", uint32_t(sim_app.screen), sim_app.main_selected, sim_app.save_selected);
        else if (action != xyf::menu::Action::none)
            xyf_sim::log("menu: action %u (not simulated: no effect)", uint32_t(action));
        return;
    }
#endif
    xyf_sim::log("key %u %s (the menu is not simulated: no effect)", key, pressed ? "pressed" : "released");
}

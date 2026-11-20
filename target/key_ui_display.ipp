// Part of key_ui.cpp: B's screen (the screen model, the draw, the synchronous present), included in place.
// Screen model for the next present (set by show()).
volatile uint32_t ui_screen __attribute__((section(".data"))) = 0;
const char* volatile ui_status __attribute__((section(".data"))) = 0;
#if defined(XYF_SERVICE_SCREEN)
// Test 054: the USB service's own screen (b), screen 4.
const char* volatile service_lines[4] __attribute__((section(".data"))) = {"", "", "", ""};
#endif

#if defined(XYF_ONE_SESSION)
// The USB mark, activity line and image line on screen (set by draw_screen), and whether the menu loop has
// the screen (key_ui::host_view redraws only then). The image line counts only on the Info page, the one that
// shows it: it redraws when the image's hash settles (image_label's pointer changes) while it is open.
volatile uint32_t usb_drawn __attribute__((section(".data"))) = 0;
const char* volatile usb_drawn_line __attribute__((section(".data"))) = 0;
const char* volatile image_drawn __attribute__((section(".data"))) = 0;
volatile uint32_t menu_screen __attribute__((section(".data"))) = 0;
#if defined(XYF_SETTINGS)
const char* volatile confirm_drawn __attribute__((section(".data"))) = 0;   // #65: the prompt on screen (0: none)
#endif
bool usb_view_changed() {
#if defined(XYF_SETTINGS)
    if (xy_forever::usb_service::confirm_prompt() != confirm_drawn) return true;
#endif
    return xy_forever::usb_service::host_marks() != usb_drawn ||
           xy_forever::usb_service::host_activity() != usb_drawn_line ||
           (app.screen == xyf::menu::Screen::info && xy_forever::usb_service::image_label() != image_drawn);
}
#endif

void draw_screen(uint16_t* fb) {
    using namespace xyf::ui;
#if defined(XYF_ONE_SESSION)
    const uint32_t marks = xy_forever::usb_service::host_marks();   // 188: bit 0 USB, bit 1 MTP
    const char* const activity = xy_forever::usb_service::host_activity();
    usb_drawn = marks; usb_drawn_line = activity;
    namespace U = xy_forever::usb_service;
    const Chrome chrome = {U::version_label(), (marks & 1u) != 0, activity, (marks & 2u) != 0, U::image_label()};
    image_drawn = chrome.image;
#else
    const Chrome chrome = {"", false, 0, false, ""};
#endif
    if (crash_shown) {                               // key_ui_crash.ipp: the marks off (USB no longer answers)
        const Chrome still = {chrome.version, false, 0, false, chrome.image};
        draw_message(fb, xyf::crash::message(crash_text, &still));
        return;
    }
#if defined(XYF_SETTINGS)
    const char* const asked = confirm_drawn = xy_forever::usb_service::confirm_prompt();
#endif
    if (ui_screen == 2) {
        const Message bye = {"Starting", {ui_status}, 1, "", &chrome};
        draw_message(fb, bye);
#if defined(XYF_SETTINGS)
    } else if (asked) {                              // #65: Confirm on device (the current menu style)
        const Message ask = {"Allow this USB request?", {"A computer asks to:", asked,
                             xy_forever::usb_service::confirm_detail(), "",
                             "STOP, or no answer within 30 s, declines it.",
                             "Settings > Confirm on device turns these questions off.", "", ""}, 6,
                             "PLAY  allow      STOP  decline", &chrome};
        draw_message(fb, ask);
#endif
#if defined(XYF_SERVICE_SCREEN)
    } else if (ui_screen == 4) {
        const Message usb = {"USB service", {service_lines[0], service_lines[1], service_lines[2], service_lines[3]}, 4,
                             "Connected to a computer (xyf_host.py)", &chrome};
        draw_message(fb, usb);
#endif
#if defined(XYF_MODS_PAGE)
    } else if (mods_shown) {
        xyf::mods::draw(fb, xy_forever::mods_io::page(), &chrome, mods_frame);
        return;
#endif
    } else {
        const char* status = ui_status;
        if (!status || !*status || (status[0] == ' ' && !status[1])) status = xy_forever::xyf_boot::status();
        xyf::menu::draw(fb, app, status, &chrome);
    }
#if defined(XYF_MODS_PAGE)
    mods_frame.drawn = 0;                            // anything else drawn: the page's next frame is full
#endif
}

// Same cache maintenance as the stock render (889eea0e -> 8803d000 FLUSH loop).
void flush_framebuffer(uint32_t fb) {
    for (uint32_t a = fb; a < fb + framebuffer_bytes; a += 32) __asm__ volatile("FLUSH [%0];" : : "a"(a) : "memory");
    __asm__ volatile("SSYNC;" : : : "memory");
}

// Bounded: the stock 889eed4c spins until the DMA is idle, so check first.
bool dma_idle(uint32_t limit_ms) {
    const uint32_t start = cycles_div1024();
    while (word(display_dma_stat) & dma_running)
        if (ms_since(start) > limit_ms) return false;
    return true;
}

// Finish the previous frame (stock wait clears the busy flag on DMA done),
// then draw the screen and start the transfer directly.
void present() {
    Result& r = result;
    if (!dma_idle(100)) { ++r.present_skips; return; }
    wait_dma();
    if (byte(smc_dma_active)) {     // DMA idle without a done bit: flag is stale
        *reinterpret_cast<volatile uint8_t*>(smc_dma_active) = 0;
        r.present_skips += 0x10000u;
    }
    draw_screen(reinterpret_cast<uint16_t*>(word(framebuffer)));   // full repaint
    where_draw(reinterpret_cast<uint16_t*>(word(framebuffer)));    // a long wait's code (key_ui_where.ipp)
    flush_framebuffer(word(framebuffer));
    transfer();
    if (byte(smc_dma_active)) ++r.presents;
}

// PLAY (boot once) on an entry whose content is not installed boots nothing: the line says how.
const char* content_note(uint32_t entry) {
    const xyf::catalog::Catalog* catalog = xy_forever::xyf_boot::catalog();
    return catalog && entry < catalog->count && (catalog->items[entry].flags & xyf::catalog::flag_content_missing) ?
           "Make it the default to install its content." : 0;
}
void show(uint32_t screen, const char* status) {
    ui_screen = screen;
    ui_status = status;
#if defined(XYF_ONE_SESSION)
    const uint64_t start = xyf::clock::cycles();   // #144: redraws while MTP traffic runs (usb_service drawn)
    present();
    xy_forever::usb_service::drawn(start);
#else
    present();
#endif
}
// A lost link ends the menu only when nothing can answer (#3): with USB on, B never resets itself; the
// live menu stays (its status line says so, commands that need A answer link) until a power cycle.
bool lost_ends_menu() {
#if defined(XYF_ONE_SESSION)
    return xy_forever::xyf_boot::link_lost() && !xy_forever::usb_service::home_usb();
#else
    return xy_forever::xyf_boot::link_lost();   // both boot built-in
#endif
}

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
volatile uint32_t trust_drawn __attribute__((section(".data"))) = 0;      // #65: the question on screen
#endif
bool usb_view_changed() {
#if defined(XYF_SETTINGS)
    if (xy_forever::usb_service::trust_prompt() != (trust_drawn != 0)) return true;
#endif
    return xy_forever::usb_service::host_marks() != usb_drawn ||
           xy_forever::usb_service::host_activity() != usb_drawn_line ||
           (app.screen == xyf::menu::Screen::info && xy_forever::usb_service::image_label() != image_drawn);
}
#endif

// The framebuffer bytes a present flushes (draw_screen narrows it for a saver frame; present resets it).
uint32_t flush_from __attribute__((section(".data"))) = 0;
uint32_t flush_to __attribute__((section(".data"))) = framebuffer_bytes;
#if defined(XYF_SETTINGS)
uint32_t saver_corner __attribute__((section(".data"))) = 0;   // the last saver frame had a where code in its corner
// #315: a saver frame; after the first, only the mark's old and new boxes are drawn and flushed (their columns, which
// are one run of the column-major framebuffer). A where code in the corner (now or on the last frame): all of it.
void draw_saver(uint16_t* fb) {
#if defined(XYF_USB_SERVICE)
    const uint32_t corner = where_shown;
#else
    const uint32_t corner = 0;
#endif
    const xyf::saver::Span span = xyf::saver::draw(fb, saver, corner || saver_corner);
    saver_corner = corner;
    flush_from = uint32_t(xyf::gfx::width - 1 - span.to) * xyf::gfx::column_stride * 2u;
    flush_to = uint32_t(xyf::gfx::width - span.from) * xyf::gfx::column_stride * 2u;
}
#endif

void draw_screen(uint16_t* fb) {
    using namespace xyf::ui;
#if defined(XYF_ONE_SESSION)
    const uint32_t marks = xy_forever::usb_service::host_marks();   // 188: bit 0 USB, bit 1 MTP
    const char* const activity = xy_forever::usb_service::host_activity();
    usb_drawn = marks;
    usb_drawn_line = activity;
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
    const bool asked = xy_forever::usb_service::trust_prompt();
    trust_drawn = asked;
#endif
    if (ui_screen == 2) {
        const Message bye = {"Starting", {ui_status}, 1, "", &chrome};
        draw_message(fb, bye);
#if defined(XYF_SETTINGS)
    } else if (const char* message = xy_forever::usb_service::uninstall_message()) {
        const Message progress = {"Uninstall XY Forever", {message}, 1, "", &chrome};
        draw_message(fb, progress);
    } else if (asked) {                              // #65: Ask before USB control (the current menu style)
        const Message ask = {"USB control",
                             {"Allow this computer to control your OP-XY?", "",
                              "Allow: until you unplug the cable or restart.", "Don't allow: it is refused until then.",
                              "Settings > Ask before USB control turns this off.", "", "", ""},
                             5,
                             "PLAY  allow      STOP  don't allow",
                             &chrome};
        draw_message(fb, ask);
    } else if (saver.on) {                           // #315: the screen saver (the menu's state stays under it)
        draw_saver(fb);
#endif
#if defined(XYF_SERVICE_SCREEN)
    } else if (ui_screen == 4) {
        const Message usb = {"USB service",
                             {service_lines[0], service_lines[1], service_lines[2], service_lines[3]},
                             4,
                             "Connected to a computer (xyf_host.py)",
                             &chrome};
        draw_message(fb, usb);
#endif
#if defined(XYF_MODS_PAGE)
    } else if (xy_forever::mods_io::converging()) {   // #197: the progress screen, from the home menu
        xyf::mods::draw_progress(fb, xy_forever::mods_io::progress(), &chrome, mods_frame);
        return;
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

// Same cache maintenance as the stock render (889eea0e -> 8803d000 FLUSH loop), over bytes from..to of it.
void flush_framebuffer(uint32_t fb, uint32_t from = 0, uint32_t to = framebuffer_bytes) {
    for (uint32_t a = fb + (from & ~31u); a < fb + to; a += 32) __asm__ volatile("FLUSH [%0];" : : "a"(a) : "memory");
    __asm__ volatile("SSYNC;" : : : "memory");
}

// Bounded: the stock 889eed4c spins until the DMA is idle, so check first. The DMA status is a system MMR: read
// with the anomaly 19000058 workaround (key_ui.cpp smmr_read32, #314).
bool dma_idle(uint32_t limit_ms) {
    const uint32_t start = cycles_div1024();
    while (smmr_read32(display_dma_stat) & dma_running)
        if (ms_since(start) > limit_ms) return false;
    return true;
}

// #288 (key_ui.hpp Frames): one sample into last/min/max (min 0: no sample yet).
void sample(uint32_t value, uint32_t& last, uint32_t& low, uint32_t& high) {
    last = value;
    if (!low || value < low) low = value;
    if (value > high) high = value;
}

// Finish the previous frame (stock wait clears the busy flag on DMA done),
// then draw the screen and send it: the stock transfer returns once its DMA is done.
void present() {
    Result& r = result;
    Frames& f = frames;
    const uint32_t entry = cycles_div1024();
    if (!dma_idle(100)) {
        ++r.present_skips;
        return;
    }
    wait_dma();
    if (byte(smc_dma_active)) {     // DMA idle without a done bit: flag is stale
        *reinterpret_cast<volatile uint8_t*>(smc_dma_active) = 0;
        r.present_skips += 0x10000u;
    }
    const uint32_t drawing = cycles_div1024();
    flush_from = 0;
    flush_to = framebuffer_bytes;
    draw_screen(reinterpret_cast<uint16_t*>(word(framebuffer)));   // full repaint (a saver frame: its boxes)
    where_draw(reinterpret_cast<uint16_t*>(word(framebuffer)));    // a long wait's code (key_ui_where.ipp)
    flush_framebuffer(word(framebuffer), flush_from, flush_to);
    const uint32_t drawn = cycles_div1024();
    transfer();
    const uint32_t sent = cycles_div1024();
    if (byte(smc_dma_active)) ++r.presents;
    ++f.presents;
    sample(drawn - drawing, f.draw_last, f.draw_min, f.draw_max);
    sample(sent - drawn, f.transfer_last, f.transfer_min, f.transfer_max);
    sample(sent - entry, f.present_last, f.present_min, f.present_max);
    f.wait_last = drawing - entry;
    if (f.wait_last > f.wait_max) f.wait_max = f.wait_last;
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

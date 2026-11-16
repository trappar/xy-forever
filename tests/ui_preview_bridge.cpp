// Host bridge: renders XY Forever screens into a framebuffer-shaped array so
// tests can check bounds and write PNG previews. Not linked into the device.
#include "../target/ui_screens.hpp"
#include "../target/ui_gfx.hpp"
using namespace xyf;
// 8.7: the chrome every bridge draws with (ui_pages.hpp): the build, the USB mark, the activity line.
ui::Chrome bridge_chrome = {"build 188", false, nullptr, false};
static char bridge_activity[64];
extern "C" {
void ui_chrome(const char* build, unsigned usb, const char* activity) {
    static char b[32];
    unsigned i = 0;
    for (; build && build[i] && i + 1 < sizeof(b); ++i) b[i] = build[i];
    b[i] = 0;
    for (i = 0; activity && activity[i] && i + 1 < sizeof(bridge_activity); ++i) bridge_activity[i] = activity[i];
    bridge_activity[i] = 0;
    bridge_chrome.build = b; bridge_chrome.usb = (usb & 1u) != 0; bridge_chrome.mtp = (usb & 2u) != 0;   // 188: bit 1 MTP
    bridge_chrome.activity = bridge_activity[0] ? bridge_activity : nullptr;
}
void ui_render_logo(uint16_t* fb, int x, int y) { gfx::clear(fb, gfx::black); gfx::logo(fb, x, y, gfx::accent, gfx::white); }
unsigned ui_framebuffer_halfwords() { return gfx::framebuffer_halfwords; }
void ui_render_menu(uint16_t* fb, unsigned selected, const char* status) {
    ui::Menu menu = {{"Continue: OP-XY 1.2.1", "Firmware library", "Mods", "Uninstall XY Forever", "Settings",
                      "Info"}, 6, selected, status, "- / +  choose      PLAY  select", &bridge_chrome, 0};
    ui::draw_menu(fb, menu);
}
void ui_render_message(uint16_t* fb) {
    ui::Message message = {"Info", {"XY Forever", "build 188", "Real bootloader: 1.2.1 (never updated)",
                                    "Hold PLAY at power-on to open this menu."},
                           4, "PLAY  back", &bridge_chrome};
    ui::draw_message(fb, message);
}
void ui_render_text(uint16_t* fb, int x, int y, const char* s, int scale) {
    gfx::clear(fb, gfx::black);
    gfx::text(fb, x, y, s, gfx::white, scale);
}
}

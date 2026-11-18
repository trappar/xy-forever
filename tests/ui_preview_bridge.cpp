// Host bridge: renders XY Forever screens into a framebuffer-shaped array so
// tests can check bounds and write PNG previews. Not linked into the device.
#include "../target/ui_screens.hpp"
#include "../target/ui_gfx.hpp"
#include "../target/crash_screen.hpp"
using namespace xyf;
// The chrome every bridge draws with (ui_pages.hpp): the version, the USB mark, the activity line, the image.
ui::Chrome bridge_chrome = {"v0.1.0", false, nullptr, false, "image f458e3cc"};
static char bridge_activity[64];
extern "C" {
void ui_chrome(const char* version, unsigned usb, const char* activity) {
    static char b[32];
    unsigned i = 0;
    for (; version && version[i] && i + 1 < sizeof(b); ++i) b[i] = version[i];
    b[i] = 0;
    for (i = 0; activity && activity[i] && i + 1 < sizeof(bridge_activity); ++i) bridge_activity[i] = activity[i];
    bridge_activity[i] = 0;
    bridge_chrome.version = b; bridge_chrome.usb = (usb & 1u) != 0; bridge_chrome.mtp = (usb & 2u) != 0;   // 188: bit 1 MTP
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
    ui::Message message = {"Info", {"XY Forever", "v0.1.0", "image f458e3cc", "Real bootloader: 1.2.1 (never updated)",
                                    "Hold PLAY at power-on to open this menu."},
                           5, "PLAY  back", &bridge_chrome};
    ui::draw_message(fb, message);
}
// B's crash screen (target/key_ui_crash.ipp) for a recorded SEQSTAT, RETX and RETS, and B's where word (#114).
void ui_render_crash(uint16_t* fb, unsigned seqstat, unsigned retx, unsigned rets, unsigned where) {
    static crash::Text text;
    const crash::Record record = {1, seqstat, retx, rets};
    crash::describe(record, where, text);
    const ui::Chrome still = {bridge_chrome.version, false, nullptr, false, bridge_chrome.image};
    ui::draw_message(fb, crash::message(text, &still));
}
// The menu with a long wait's code in the corner (key_ui present: the screen, then where::draw).
void ui_render_where(uint16_t* fb, unsigned code) {
    ui_render_menu(fb, 0, "");
    where::draw(fb, code);
}
void ui_render_text(uint16_t* fb, int x, int y, const char* s, int scale) {
    gfx::clear(fb, gfx::black);
    gfx::text(fb, x, y, s, gfx::white, scale);
}
}

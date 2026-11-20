// Host bridge for XY Forever pages (previews and tests; not linked into the device).
#include "../target/ui_pages.hpp"
using namespace xyf::ui;
extern xyf::ui::Chrome bridge_chrome;                 // ui_preview_bridge.cpp
extern "C" {
void ui_render_library(uint16_t* fb, unsigned selected, unsigned count) {
    static const ListRow rows[] = {
        {"OP-XY 1.2.1", "official", true}, {"OP-XY 1.2.5", "official", false},
        {"open-xy: granular", "6 machines", false}, {"open-xy: braids", "3 machines", false},
        {"My test build", "custom", false}, {"OP-XY 1.2.4", "official", false}, {"open-xy: karplus", "1 machine", false}};
    List list = {"Firmware library", rows, count > 7 ? 7 : count, selected,
                 "- / +  choose      PLAY  boot once      SHIFT+PLAY  set default", &bridge_chrome, false};
    draw_list(fb, list);
}
void ui_render_confirm(uint16_t* fb, unsigned accept) {
    Confirm c = {"Uninstall XY Forever?", {"Puts back the stock OP-XY 1.2.1 os.ldr and deletes",
                 "xy-forever/ from the device. Your projects stay.", "The real bootloader is not changed."},
                 3, "Cancel", "Uninstall", accept != 0, &bridge_chrome};
    draw_confirm(fb, c);
}
void ui_render_progress(uint16_t* fb, unsigned done, unsigned total) {
    Progress p = {"Installing OP-XY 1.2.5", "Keyboard firmware (2 of 4)", done, total,
                  "Do not power off the OP-XY.", &bridge_chrome};
    draw_progress(fb, p);
}
void ui_render_home(uint16_t* fb, unsigned) { draw_home_empty(fb, &bridge_chrome); }   // #21: one row, always selected
unsigned ui_scale(unsigned a, unsigned b, unsigned c) { return scale(a, b, c); }
unsigned ui_list_first(unsigned count, unsigned selected) { return list_first(count, selected); }
}

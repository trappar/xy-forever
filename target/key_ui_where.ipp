// Part of key_ui.cpp: B's where codes (#114, where.hpp, key_ui.hpp Where), included in place (in key_ui's
// anonymous namespace; the public functions at the end leave it and come back).
// One word (where.hpp), when the wait began (cycles_div1024) and the code in the corner now (0: none). The
// corner paints only while where_screen is set: from crash_on to crash_off (key_ui_crash.ipp), the span in which
// the menu has the screen and its present works. A wait polls (where_wait) and paints once, with the menu's own
// present, from the wait's loop: never from an interrupt (IPEND, as usb_service in_interrupt).
#if defined(XYF_USB_SERVICE)
volatile uint32_t where_word_now __attribute__((section(".data"))) = 0;
volatile uint32_t where_since __attribute__((section(".data"))) = 0;
volatile uint32_t where_shown __attribute__((section(".data"))) = 0;
volatile uint32_t where_screen __attribute__((section(".data"))) = 0;
void present();
// From present, after the screen: the label in the top-right corner (where.hpp draw).
void where_draw(uint16_t* fb) {
    if (where_shown) xyf::where::draw(fb, where_shown);
}
}
namespace xy_forever { namespace key_ui {
uint32_t where_enter(uint32_t code) {
    const uint32_t before = where_word_now;
    where_word_now = xyf::where::entered(code);
    where_since = cycles_div1024();
    return before;
}
void where_wait() {
    if (!where_screen || where_shown) return;
    const uint32_t code = xyf::where::current(where_word_now);
    if (!code || ms_since(where_since) < xyf::where::paint_ms || (word(0x1fc02108u) & 0x7fe0u)) return;
    where_shown = code;
    present();
}
void where_leave(uint32_t before) {
    where_word_now = xyf::where::current(before) | (where_word_now & 0xffff0000u);
    where_since = cycles_div1024();
    if (!where_shown) return;
    where_shown = 0;
    if (where_screen) present();
}
}}
namespace {
#else
void where_draw(uint16_t*) {}
#endif

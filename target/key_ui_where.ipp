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
// #314 (key_ui_crash.ipp, the stop screen): the menu loop moved (hang_kick: each pass, each bounded wait's poll),
// which winds the dead man's timer back once its proof interrupt came (hang_live), at most every 100 ms (three core
// register writes; polls come much faster). hang_wind: as Linux's Blackfin core timer sets its next event: stopped,
// counted from the top, started. A long stock call with interrupts of its own (usb_service: the boot writer) stops
// the timer for its length (hang_held, hang_pause).
const uint32_t hang_tcntl = 0x1fc03000u, hang_tcount = 0x1fc0300cu;
const uint32_t hang_cycles = 1200000000u;                              // 3 s at 400 MHz (crash_screen.hpp stop_ms)
const uint32_t hang_wind_units = 39063u;                               // 100 ms in cycles_div1024 units
volatile uint32_t hang_beat __attribute__((section(".data"))) = 0;
volatile uint32_t hang_at __attribute__((section(".data"))) = 0;      // cycles_div1024 at the last hang_kick
volatile uint32_t hang_wound __attribute__((section(".data"))) = 0;   // and at the last hang_wind
volatile uint32_t hang_live __attribute__((section(".data"))) = 0;
volatile uint32_t hang_held __attribute__((section(".data"))) = 0;
__attribute__((noinline)) void hang_wind() {
    *reinterpret_cast<volatile uint32_t*>(hang_tcntl) = 1u;             // TMPWR: stopped
    __asm__ volatile("CSYNC;" : : : "memory");
    *reinterpret_cast<volatile uint32_t*>(hang_tcount) = hang_cycles;
    __asm__ volatile("CSYNC;" : : : "memory");
    *reinterpret_cast<volatile uint32_t*>(hang_tcntl) = 7u;             // TMPWR | TMREN | TAUTORLD
}
__attribute__((always_inline)) inline void hang_kick() {
    const uint32_t now = cycles_div1024();
    hang_beat = hang_beat + 1;
    hang_at = now;
    if (!hang_live || hang_held || now - hang_wound < hang_wind_units) return;
    hang_wound = now;
    hang_wind();
}
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
    hang_kick();
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

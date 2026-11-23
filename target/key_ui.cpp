#include "xyf_clock.hpp"
#include "cold_layout.hpp"
#include "key_ui.hpp"
#include "cold_heap.hpp"
// The menu runs inside file boot's link session (every build mode has file boot).
#include "xyf_menu_view.hpp"   // XY Forever menu state machine + pages
#include "xyf_boot.hpp"        // Test030: link hooks, library catalog and boot choice
#include "ui_screens.hpp"  // XY Forever screens (own font/logo; no stock console)
#include "xyf_mods_io.hpp"     // the Mods page (product build: XYF_MODS_PAGE)
#include "xyf_mods_view.hpp"
#include "key_repeat.hpp"        // - / + repeat while held
#include "knob_input.hpp"        // knob turns move the selection
#include "usb_service.hpp"
#include "crash_screen.hpp"      // the crash screen's lines (key_ui_crash.ipp)
// B's exception vector while the menu has the screen (key_ui_crash.ipp, asm).
extern "C" void xyf_crash_vector() __asm__("xyf_crash_vector");
#if defined(XYF_STAGE2)
#include "xyf_stage2.hpp"      // stage-2 ABI; the call goes through xyf_stage2_enter (asm)
extern "C" uint32_t xyf_stage2_enter(uint32_t entry, const xyf::stage2::Abi* abi) __asm__("xyf_stage2_enter");
#endif
// Test018 (see key_ui.hpp). Addresses are the authenticated 1.2.1 bootloader,
// mapped offline in private/xy-forever/analysis/bootloader-121-listing/FINDINGS.md.
namespace xy_forever { namespace key_ui {
// Initialized data, not BSS: the cold link has no zero-initialization.
Result result __attribute__((section(".data"))) = {magic};
Frames frames __attribute__((section(".data"))) = {frames_magic, 1};
}}

namespace {
using xy_forever::key_ui::Result;
using namespace xy_forever::key_ui;

const uint32_t role_byte = 0x8807cac4u;      // 0 = storage processor a, 1 = b
const uint32_t snapshot = 0x8807ce7eu;       // key status reply (request 0x41)
const uint32_t psoc = 0x88a522c8u;           // stock PSoC command handler
const uint32_t psoc_open = psoc + 0x7f61u;   // comm open flag (cleared by boot teardown)
const uint32_t psoc_greeted = psoc + 0x80f4u;
const uint32_t framebuffer = 0x8803dc58u;    // current display framebuffer pointer
const uint32_t frame_pending = 0x11905f30u;  // display mailbox flag (vsync ISR clears)
// Test018/019: the vsync (PINT) callback never runs at cold time and the
// display DMA busy flag is already stale (1) at entry, so frames were drawn
// but never sent. Present synchronously like the stock 88a1de7c instead.
const uint32_t smc_dma_active = 0x11905cc0u; // display DMA busy (DMA ISR or 889eed4c clears)
const uint32_t display_dma_stat = 0x200a3030u;
const uint32_t dma_running = 0x700u;
// Framebuffer: 480 rows x 512 bytes (16-bit pixels), the range the stock
// render's cache flush 889eea0e covers. Every frame is a full repaint.
const uint32_t framebuffer_bytes = 0x3c000u;
const uint32_t play_key = 51;
const uint32_t idle_timeout_ms = 60000;      // no key for 60 s -> Continue

// Stock entry points, CRC-gated below (DDR code only; never L1 instruction).
typedef void SetKeyCallback(uint32_t, void (*)(uint32_t, uint32_t));  // 88a245dc
typedef uint32_t Poll(uint32_t);                   // 88a24922 open/greet + dispatch
typedef void CloseLinks(uint32_t);                 // 88a35e82 stock "shut down for boot" links
typedef void Delay(uint32_t);                      // 88a3426a microseconds (busy after teardown)
typedef void WaitDma();                            // 889eed4c poll display DMA done, stop it, clear busy
typedef void Transfer(uint32_t, uint32_t);         // 889efa6a start framebuffer transfer (1 = wait start)

struct Span {
    uint32_t address, bytes, crc;
};
// Parsed and cross-checked against the bootloader by xyf_build.py.
const Span spans[] = {
    {0x889ee800u, 0x1600u, 0x46ad94d9u},  // text console, glyphs, display post/vsync path
    {0x88a24500u, 0x63cu, 0xa8d90843u},   // PSoC handler: set callback, greet, key diff, open, poll
    {0x88a24c94u, 0x50u, 0x85f79bb1u},    // poll frame-type switch table (0x42 -> key diff)
    {0x88a35e82u, 0x4au, 0xb7d8d12eu},    // link teardown
    {0x88a3426au, 0xdcu, 0x81cb4889u},    // microsecond delay
};

uint32_t crc32(uint32_t address, uint32_t bytes) {
    uint32_t crc = 0xffffffffu;
    const volatile uint8_t* p = reinterpret_cast<const volatile uint8_t*>(address);
    for (uint32_t i = 0; i != bytes; ++i) {
        crc ^= p[i];
        for (uint32_t b = 0; b != 8; ++b) crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}
uint8_t byte(uint32_t address) { return *reinterpret_cast<const volatile uint8_t*>(address); }
uint32_t word(uint32_t address) { return *reinterpret_cast<const volatile uint32_t*>(address); }
uint32_t cycles_div1024() {
    uint32_t low, high;
    __asm__ volatile("%0 = CYCLES; %1 = CYCLES2;" : "=d"(low), "=d"(high));
    return (low >> 10) | (high << 22);
}
// 400 MHz core clock (stock timer rate word 0x88a2ef28 = 400000000, Test017):
// 390625 units of 1024 cycles per second, so ms = units / 390.625 = units *
// 0.00256 ~= units * 21 / 8192 (+0.14%; no division; xyf_clock: no overflow for ~3 h).
// Test018 used *41/16, which is microseconds: its "20 s" timeout was 20 ms.
uint32_t ms_since(uint32_t start) { return xyf::clock::ms_from_units(cycles_div1024() - start); }

extern "C" char xy_forever_module_end[] __asm__("__end");
const uint32_t paint = 0x5ca1ab1eu, stack_top = XYF_STACK_END;
uint32_t stack_pointer() {
    uint32_t sp;
    __asm__ volatile("%0 = SP;" : "=d"(sp));
    return sp;
}
// Paint the unused stack below the live frame (from module end) so the
// report shows the deepest stack the stock PSoC/display code reached.
uint32_t paint_stack() {
    uint32_t begin = (reinterpret_cast<uint32_t>(xy_forever_module_end) + 3u) & ~3u;
#if defined(XYF_STACK_PAINT_FLOOR)
    if (begin < XYF_STACK_PAINT_FLOOR) begin = XYF_STACK_PAINT_FLOOR;
#endif
    const uint32_t end = (stack_pointer() - 256u) & ~3u;
    for (uint32_t a = begin; a < end; a += 4) *reinterpret_cast<volatile uint32_t*>(a) = paint;
    return begin;
}
uint32_t stack_used(uint32_t begin) {
    uint32_t a = begin;
    while (a < stack_top && *reinterpret_cast<volatile uint32_t*>(a) == paint) a += 4;
    return stack_top - a;
}

// Callback state (written from the stock poll, in this thread).
volatile uint32_t play_released __attribute__((section(".data"))) = 0;
volatile uint32_t dirty __attribute__((section(".data"))) = 0;
volatile uint32_t link_ms __attribute__((section(".data"))) = 0;
volatile uint32_t last_key_ms __attribute__((section(".data"))) = 0;
// XY Forever menu (xyf_menu): keys go to the state machine; the loop runs
// the action it returns. No catalog yet (built-in copy only).
xyf::menu::State app __attribute__((section(".data"))) = {};
volatile uint32_t app_action __attribute__((section(".data"))) = 0;
#if defined(XYF_MODS_PAGE)
volatile uint32_t mods_shown __attribute__((section(".data"))) = 0;   // the Mods page is on screen
xyf::mods::Frame mods_frame __attribute__((section(".data"))) = {};    // what the framebuffer holds of it
#endif
xyf::key_repeat::Held held __attribute__((section(".data"))) = {xyf::key_repeat::none, 0};
#if defined(XYF_STAGE2)
// Key events for stage 2 (filled by on_key while stage 2 runs).
const uint32_t stage2_queue = 8;
volatile uint32_t stage2_active __attribute__((section(".data"))) = 0;
volatile uint32_t stage2_head __attribute__((section(".data"))) = 0;
volatile uint32_t stage2_tail __attribute__((section(".data"))) = 0;
volatile uint32_t stage2_events[stage2_queue] __attribute__((section(".data"))) = {};
#endif

void dispatch(uint32_t key, uint32_t pressed);
// Set while inject_key runs (the host's KEY command): an injected key never answers a confirm prompt.
volatile uint32_t key_injected __attribute__((section(".data"))) = 0;
void on_key(uint32_t key, uint32_t pressed) {
    Result& r = result;
    const uint32_t ms = ms_since(r.start_cycles);
    const uint32_t entry = (key & 0xffu) | ((pressed & 1u) << 8) | ((ms > 0xffffu ? 0xffffu : ms) << 16);
    if (r.events < max_events) r.event[r.events] = entry;
    ++r.events;
    r.last_event = entry;
    last_key_ms = ms;
    xy_forever::knob_input::log_key(key, pressed != 0, key_injected != 0, ms);   // #288: the raw input log
#if defined(XYF_STAGE2)
    if (stage2_active) {
        if (key == play_key) {
            if (!pressed) play_released = 1;
            else if (!(play_released || ms > link_ms + 1000u)) return;   // the held boot key
        }
        if (stage2_head - stage2_tail < stage2_queue)     // full: drop the newest
            stage2_events[stage2_head++ & (stage2_queue - 1)] =
                (key & 0xffu) | (pressed & 1u) << 8 | xyf::stage2::event_key;
        return;
    }
#endif
    if (key == play_key) {
        if (!pressed) play_released = 1;
        // The held boot key's report must not select: a press counts once PLAY was
        // seen released, or if it arrives well after the link opened (the initial
        // state report reflects the held boot key).
        else if (!(play_released || ms > link_ms + 1000u)) return;
    }
#if defined(XYF_SETTINGS)
    if (xy_forever::usb_service::confirm_key(key, pressed, key_injected != 0)) {   // a prompt has the keys
        if (!pressed) xyf::key_repeat::event(held, key, false, ms);              // a held - / + stops repeating
        return;
    }
#endif
    xyf::key_repeat::event(held, key, pressed != 0, ms);
    dispatch(key, pressed);
}

// A key to the page on screen: the Mods page or the menu (on_key, and the repeat of a held - / +).
void dispatch(uint32_t key, uint32_t pressed) {
#if defined(XYF_MODS_PAGE)
    if (xy_forever::mods_io::converging()) {         // #197: the progress screen (STOP cancels a build)
        if (xy_forever::mods_io::progress_key(key, pressed != 0) != xyf::mods::effect_none) dirty = 1;
        return;
    }
    if (mods_shown) {                                // the page has the keys (STOP on its list: back)
        const xyf::mods::Effect effect = xy_forever::mods_io::key(key, pressed != 0);
        if (effect == xyf::mods::effect_leave) mods_shown = 0;
        if (effect != xyf::mods::effect_none) dirty = 1;
        return;
    }
#endif
    const xyf::menu::Action action = xyf::menu::key(app, key, pressed != 0);
    if (action == xyf::menu::Action::redraw) dirty = 1;
    else if (action != xyf::menu::Action::none) app_action = uint32_t(action);
}

// Knob turns (knob_input.hpp) move the selection as - / + do, not on the progress screen. On the Mods page with
// key_turn (SHIFT + turn: its filter).
void turn_knobs(uint32_t now_ms) {
#if defined(XYF_MODS_PAGE)
    const bool job = xy_forever::mods_io::converging(), page = mods_shown != 0 && !job;
#else
    const bool page = false, job = false;
#endif
    const int32_t rows = xy_forever::knob_input::pass(now_ms, !job);
    const uint32_t step = (rows > 0 ? xyf::menu::key_plus : xyf::menu::key_minus) | (page ? xyf::menu::key_turn : 0u);
    for (int32_t i = rows < 0 ? -rows : rows; i; --i) dispatch(step, 1);   // the Mods page knows a turn
}

// One noinline wrapper per stock entry: the address load stays next to its
// call, which keeps the builder's call-target audit straight-line and exact.
__attribute__((noinline)) void delay_us(uint32_t us) { reinterpret_cast<Delay*>(0x88a3426au)(us); }
__attribute__((noinline)) void poll() { reinterpret_cast<Poll*>(0x88a24922u)(psoc); }
__attribute__((noinline)) void set_key_callback(void (*cb)(uint32_t, uint32_t)) {
    reinterpret_cast<SetKeyCallback*>(0x88a245dcu)(psoc, cb);
}
__attribute__((noinline)) void wait_dma() { reinterpret_cast<WaitDma*>(0x889eed4cu)(); }
__attribute__((noinline)) void transfer() { reinterpret_cast<Transfer*>(0x889efa6au)(word(framebuffer), 1); }
__attribute__((noinline)) void close_links() { reinterpret_cast<CloseLinks*>(0x88a35e82u)(1); }

#include "key_ui_where.ipp"     // where B waits (#114): the corner code
#include "key_ui_crash.ipp"     // the crash screen: its record and text, before the draw
#include "key_ui_display.ipp"   // the screen model, the draw, the present

#if defined(XYF_SERVICE_SCREEN)
// Test 054: 0 unchecked, 1 display code matches its CRC, 2 refused (never draws).
volatile uint32_t service_checked __attribute__((section(".data"))) = 0;
#endif
#include "key_ui_stage2.ipp"     // the stage-2 services and run_stage2 (XYF_STAGE2 modes)
}

extern "C" void xy_forever_key_ui() {
    Result& r = result;
    r.version = 4;                                   // 4: Test021+ menu (layout as 3)
    r.start_cycles = cycles_div1024();
    r.role = byte(role_byte);
    // 0x8807ce7e is only 2-aligned: read bytewise (a 32-bit load would fault).
    r.frame_header =
        byte(snapshot) | byte(snapshot + 1) << 8 | byte(snapshot + 2) << 16 | uint32_t(byte(snapshot + 3)) << 24;
    for (uint32_t i = 0; i != 3; ++i)
        r.bitmap[i] = byte(snapshot + 4 + i * 4) | byte(snapshot + 5 + i * 4) << 8 | byte(snapshot + 6 + i * 4) << 16 |
                      uint32_t(byte(snapshot + 7 + i * 4)) << 24;
    r.stages = stage_snapshot;
    if (r.role != 1) {
        r.exit_reason = exit_role;
        return;
    }
    if (byte(snapshot + 2) != 0x42 || byte(snapshot + 3) != 0x0a) {
        r.exit_reason = exit_snapshot;
        return;
    }
    r.play_snapshot = (byte(snapshot + 4 + play_key / 8) >> (play_key % 8)) & 1u;
#if defined(XYF_USB_MENU)
    // Test 058: the host asked for the menu (CONTINUE mode 2/3) without PLAY.
    if (!r.play_snapshot && !xy_forever::usb_service::menu_requested() && !xy_forever::xyf_boot::menu_forced() &&
        !xy_forever::usb_service::home_active()) {
        r.exit_reason = exit_no_play;
        return;
    }
#else
    if (!r.play_snapshot) {
        r.exit_reason = exit_no_play;
        return;
    }
#endif
    r.stages |= stage_play;

    for (uint32_t i = 0; i != sizeof(spans) / sizeof(spans[0]); ++i)
        if (crc32(spans[i].address, spans[i].bytes) != spans[i].crc) r.identity_mismatch |= 1u << i;
    if (r.identity_mismatch) {
        r.exit_reason = exit_identity;
        return;
    }
    r.stages |= stage_identity;

    {
        xy_forever::cold_heap::Snapshot heap;
        xy_forever::cold_heap::Result result_code;
        const bool ok = xy_forever::cold_heap::guard(heap, result_code);
        r.heap_results = uint32_t(ok) | uint32_t(result_code) << 8;
        if (ok) r.heap_brk_before = heap.brk;
    }
    const uint32_t painted = paint_stack();
    xy_forever::usb_service::stack_watch(painted);   // STATUS reports the mark live
    r.framebuffer = word(framebuffer);
    r.dma_stat_entry = word(display_dma_stat);
    r.display_entry = byte(smc_dma_active) | uint32_t(byte(frame_pending)) << 8;
    show(0, "Starting key link...");
    r.stages |= stage_display;
    if (r.presents && dma_idle(200)) r.stages |= stage_displayed;   // first frame finished
    if (r.stages & stage_displayed) crash_on();     // from here a fault shows the crash screen
    r.display_ms = ms_since(r.start_cycles);

    set_key_callback(on_key);
    poll();       // re-open + greet (stock path)
    r.polls = 1;
    r.link_ms = link_ms = ms_since(r.start_cycles);
    r.link_state = byte(psoc_open) | uint32_t(byte(psoc_greeted)) << 8;
    if (byte(psoc_open)) r.stages |= stage_link;
    if (byte(psoc_greeted)) r.stages |= stage_greeted;
    if (!byte(psoc_open)) {
        r.exit_reason = exit_link;
        show(2, "No key link. Booting normal OP-XY...");
    } else {
        const char* status = " ";
        last_key_ms = r.link_ms;
#if defined(XYF_STAGE2)
        if (run_stage2()) {         // false: stage 2 chose to boot (or the link was lost)
            last_key_ms = ms_since(r.start_cycles);
#endif
            xyf::menu::start(app, xy_forever::xyf_boot::catalog(), true);
#if defined(XYF_ONE_SESSION)
            app.storage = xy_forever::xyf_boot::storage_view();   // 193: the first row waits for the library
#endif
#if defined(XYF_MODS_PAGE)
            // #197: A doubted the saved inputs at a plain power-on: the progress screen from the first frame, then the boot
            if (xy_forever::xyf_boot::take_converge())
                xy_forever::mods_io::converge_begin(xy_forever::mods_io::why_power_on);
#endif
            show(0, status);
            xy_forever::knob_input::start(!r.identity_mismatch && (r.stages & stage_identity));
#if defined(XYF_ONE_SESSION)
            menu_screen = 1;                             // key_ui::host_view may redraw from here
            bool shown_lost = false;                     // the status line already shows the lost link
#endif
            for (;;) {
                poll();
                ++r.polls;
#if defined(XYF_SETTINGS)
                { // once A serves; the rows, marks, the save on leaving
                    const char* line = 0;
                    if (xy_forever::usb_service::settings_view(app, line)) dirty = 1;
                    if (line) {
                        status = line;
                        dirty = 1;
                    }
                }
#endif
                turn_knobs(ms_since(r.start_cycles)); // after the stock poll
                if (xyf::key_repeat::due(held, ms_since(r.start_cycles)) && xyf::menu::repeat_keys(app))
                    dispatch(held.key, 1); // a held - / + repeats
#if defined(XYF_ONE_SESSION)
                // Test 065: home answers every USB command; CONTINUE leaves home to boot the default.
                if (const uint32_t leave = xy_forever::usb_service::home_pump()) {
                    // 2 + i = library.boot of catalog entry i, the PLAY-on-an-entry path below.
                    if (leave == 1) xy_forever::xyf_boot::choose_default();
                    else xy_forever::xyf_boot::choose(leave - 2);
                    r.exit_reason = exit_play;
                    break;
                }
#endif
                if (xy_forever::xyf_boot::tick()) dirty = 1;
#if defined(XYF_ONE_SESSION)
                if (const uint32_t view =
                        xy_forever::xyf_boot::storage_view()) { // 193: the first row, the rows that need it
                    if (view != app.storage) {
                        app.storage = view;
                        dirty = 1;
                    }
                }
#endif
#if defined(XYF_ONE_SESSION)
                const bool lost = xy_forever::xyf_boot::link_lost();
                if (lost && !shown_lost) {
                    shown_lost = true;
                    status = " ";
                    dirty = 1;
                } // #3: shown at once
#else
            const bool lost = false;
#endif
                if (lost_ends_menu()) {
                    r.exit_reason = exit_link;
                    break;
                }
#if defined(XYF_MODS_PAGE)
                // The Settings page reads the mod packages too (its Mod memory row needs the base's layouts)
                const bool settings_shown = !mods_shown && app.screen == xyf::menu::Screen::settings;
                if (settings_shown) xy_forever::mods_io::prepare();
                if (xy_forever::mods_io::pump(mods_shown || settings_shown) &&
                    (mods_shown || xy_forever::mods_io::converging()))
                    dirty = 1; // the page's reading, the progress screen
                if (xy_forever::mods_io::menu_state(app) && !mods_shown) dirty = 1; // pending, the first row
#endif
#if defined(XYF_CONTENT_SWITCH)
                if (const char* line = xy_forever::usb_service::content_line()) {
                    status = line;
                    dirty = 1;
                }
                app.undo_offer = xy_forever::usb_service::content_offer(); // PLAY on the first row undoes
#endif
#if defined(XYF_MODS_PAGE)
                // The converge sequence ended (#197): boot the default (the desired state), or back to the menu with
                // the reason (the changes stay pending).
                if (const uint32_t saved = xy_forever::mods_io::converge_state()) {
                    if (saved == xy_forever::mods_io::converge_boot) {
#if defined(XYF_TFW_CHECK)
                        // A finished converge can still have its boot deferred when a USB rehearsal starts.
                        if (lost || !xy_forever::xyf_boot::link_idle() || xy_forever::usb_service::page_busy())
                            continue;
#endif
                        xy_forever::mods_io::converge_done();
                        xy_forever::xyf_boot::choose_default();
                        r.exit_reason = exit_play;
                        break;
                    }
                    if (saved == xy_forever::mods_io::converge_failed || saved == xy_forever::mods_io::converge_note) {
                        status = xy_forever::mods_io::converge_reason();
                        xy_forever::mods_io::converge_done();
                        dirty = 1;
                    }
                }
#endif
                if (const uint32_t action = app_action) {
                    using xyf::menu::Action;
                    app_action = 0;
                    dirty = 1;
#if defined(XYF_MODS_PAGE)
                    // The converge sequence runs (the progress screen): no other action.
                    if (action != uint32_t(Action::redraw) && xy_forever::mods_io::converging()) {
                        app.screen = xyf::menu::Screen::main;
                        continue;
                    }
#endif
                    const bool boots = action == uint32_t(Action::continue_boot) ||
                                       action == uint32_t(Action::safe_mode) || action == uint32_t(Action::restore) ||
                                       action == uint32_t(Action::apply);
                    if (lost && boots) {
                        app.screen = xyf::menu::Screen::main;
                        status = " "; // the status line shows the lost link
                        continue;
                    }
#if defined(XYF_USB_FILE_WRITE)
#if defined(XYF_MODS_PAGE)
                    const bool own =
                        xy_forever::mods_io::saving(); // the Mods page's own write: converging waits for it
#else
                    const bool own = false;
#endif
                    if (boots && !own) { // not while a USB job runs
                        if (const char* busy = xy_forever::usb_service::menu_busy()) {
                            status = busy;
                            app.screen = xyf::menu::Screen::main;
                            continue;
                        }
                    }
#endif
                    if (action == uint32_t(Action::continue_boot)) {
                        xy_forever::xyf_boot::choose_default();
                        r.exit_reason = exit_play;
                        break;
                    }
#if defined(XYF_CONTENT_SWITCH)
                    if (action == uint32_t(Action::undo)) { // the source-missing line's offer: the previous entry back
                        if (const char* line = xy_forever::usb_service::content_undo()) status = line;
                        continue;
                    }
#endif
#if defined(XYF_MODS_PAGE)
                    if (boots) { // #197: the inputs set, then converge (the progress screen)
                        using namespace xy_forever::mods_io;
                        converge_begin(action == uint32_t(Action::safe_mode) ? why_safe :
                                       action == uint32_t(Action::restore)   ? why_restore :
                                                                               why_apply);
                        continue;
                    }
#endif
#if defined(XYF_SETTINGS)
                    // #21: PLAY on a setting opens its option list; the list's PLAY picks: #191, in effect at once and
                    // saved on its own at a quiet moment (Mod memory as the Mods page, built at the next boot).
                    if (action == uint32_t(Action::change_setting)) {
                        using namespace xyf::settings;
                        if (app.entry != row_memory)
                            app.settings_note = xy_forever::usb_service::settings_open(app, app.entry);
#if defined(XYF_MODS_PAGE)
                        else if (app.memory == memory_standard || app.memory == memory_expanded)
                            xyf::menu::open_options(app, memory_options, 2, app.memory - memory_standard);
                        else app.settings_note = xy_forever::mods_io::memory_flip(); // why not (it changes nothing)
#else
                        else app.settings_note = "Mods: not available in this build.";
#endif
                        continue;
                    }
                    if (action == uint32_t(Action::pick_setting)) {
                        app.settings_note = 0;
                        if (app.entry != xyf::settings::row_memory) xy_forever::usb_service::settings_pick(app);
#if defined(XYF_MODS_PAGE)
                        else if (app.option_selected != app.option_current)
                            app.settings_note = xy_forever::mods_io::memory_flip();
#endif
                        continue;
                    }
#endif
                    if (action == uint32_t(Action::open_mods)) {
#if defined(XYF_MODS_PAGE)
                        mods_shown = 1;
                        xy_forever::mods_io::open();
#else
                    status = "Mods: not available in this build.";
#endif
                        continue;
                    }
                    app.screen = xyf::menu::Screen::main; // show the result line
                    status = "Not available in this build.";
                }
#if defined(XYF_ONE_SESSION)
                if (usb_view_changed()) dirty = 1; // the USB mark, the activity line or the Info page's image changed
#endif
                if (dirty) {
                    dirty = 0;
                    show(0, status);
                }
                // Test 059: after a failed check the menu waits for a choice (never the broken default).
#if defined(XYF_ONE_SESSION)
                // Test 066 (project rule): the boot menu never times out.
                if (false) {
#else
            if (!xy_forever::xyf_boot::menu_forced() && ms_since(r.start_cycles) > last_key_ms + idle_timeout_ms) {
#endif
                    r.exit_reason = exit_timeout;
                    break;
                }
#if defined(XYF_ONE_SESSION)
                delay_us(xy_forever::usb_service::menu_pause_us()); // #144: shorter while MTP traffic runs
#else
            delay_us(5000);
#endif
            }
#if defined(XYF_ONE_SESSION)
            menu_screen = 0;
#endif
#if defined(XYF_STAGE2)
        }
#endif
        show(2, r.exit_reason == exit_play ? "Booting normal OP-XY..." : "No keys for 60 s. Booting normal OP-XY...");
    }
    crash_off(); // the vector the stock code left, before any hand-off
    set_key_callback(0);
    // Same stock teardown the bootloader ran before handoff; closes are
    // guarded by each transport's open flag, so repeating it is idempotent.
    close_links();
    if (!byte(psoc_open)) r.stages |= stage_closed;
    // Leave the display DMA finished and its busy flag clear for the OS.
    if (dma_idle(200)) wait_dma();
    {
        xy_forever::cold_heap::Snapshot heap;
        xy_forever::cold_heap::Result result_code;
        const bool ok = xy_forever::cold_heap::guard(heap, result_code);
        r.heap_results |= (uint32_t(ok) | uint32_t(result_code) << 8) << 16;
        if (ok) r.heap_brk_after = heap.brk;
    }
    r.stack_used = stack_used(painted);
    r.end_ms = ms_since(r.start_cycles);
}

namespace xy_forever { namespace key_ui {
void crash_disarm() { crash_off(); }
#if defined(XYF_CRASH_TRIGGER)
bool crash_armed_now() { return crash_armed != 0; }
void crash_fault(uint32_t kind) {
    if (crash_armed) xyf_crash_fault(kind);
}
#endif
}}

#if defined(XYF_SERVICE_SCREEN)
namespace xy_forever { namespace key_ui {
// Test 054 (b, USB service): XY Forever header + "USB service" + four lines.
// Same present path as the menu (stock wait_dma/transfer), gated by the CRC of
// the display span; never touches the PSoC link. Returns false if not drawn.
bool service_screen(const char* a, const char* b, const char* c, const char* d) {
    if (!service_checked) service_checked = crc32(spans[0].address, spans[0].bytes) == spans[0].crc ? 1u : 2u;
    if (service_checked != 1u) return false;
    service_lines[0] = a ? a : "";
    service_lines[1] = b ? b : "";
    service_lines[2] = c ? c : "";
    service_lines[3] = d ? d : "";
    show(4, "");
    return true;
}
// Test 058: a key event from the host (menu over USB), as if from the PSoC.
void inject_key(uint32_t key, uint32_t pressed) {
    key_injected = 1;
    on_key(key, pressed);
    key_injected = 0;
}
#if defined(XYF_ONE_SESSION)
void refresh_catalog() {
    const uint32_t row = app.main_selected; // #197: a rescan refreshes the data, never the home row
#if defined(XYF_SETTINGS)
    // A rescan (a USB job's end) keeps the Settings page, its row and line, and an open option list.
    const xyf::menu::Screen kept_screen = app.screen;
    const bool keep = kept_screen == xyf::menu::Screen::settings || kept_screen == xyf::menu::Screen::options ||
                      kept_screen == xyf::menu::Screen::packs;
    const uint32_t kept_row = app.main_selected, kept_setting = app.settings_selected, kept_entry = app.entry;
    const char* const kept_note = app.settings_note;
    const char* const* const kept_options = app.options;
    const uint32_t kept_count = app.option_count, kept_current = app.option_current, kept_option = app.option_selected;
#endif
    const uint32_t storage = app.storage; // 193: the view stays (the loop updates it)
    xyf::menu::start(app, xy_forever::xyf_boot::catalog(), true);
    app.storage = storage;
#if defined(XYF_SETTINGS)
    if (keep) {
        app.screen = kept_screen;
        app.main_selected = kept_row;
        app.entry = kept_entry;
        app.settings_selected = kept_setting;
        app.settings_note = kept_note;
        app.options = kept_options;
        app.option_count = kept_count;
        app.option_current = kept_current;
        app.option_selected = kept_option;
        if (kept_screen == xyf::menu::Screen::packs)
            xy_forever::usb_service::settings_open(app, xyf::settings::row_packs);
    }
#endif
    app.main_selected = row; // (a job's end, also from the home menu or the Mods page)
    app_action = 0;
    dirty = 1;
}
void host_view() {
    if (menu_screen && usb_view_changed()) present();
}
#endif
}}
#endif

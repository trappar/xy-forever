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
#if defined(XYF_STAGE2)
#include "xyf_stage2.hpp"      // stage-2 ABI; the call goes through xyf_stage2_enter (asm)
extern "C" uint32_t xyf_stage2_enter(uint32_t entry, const xyf::stage2::Abi* abi) __asm__("xyf_stage2_enter");
#endif
// Test018 (see key_ui.hpp). Addresses are the authenticated 1.2.1 bootloader,
// mapped offline in private/xy-forever/analysis/bootloader-121-listing/FINDINGS.md.
namespace xy_forever { namespace key_ui {
// Initialized data, not BSS: the cold link has no zero-initialization.
Result result __attribute__((section(".data"))) = {magic};
} }

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

struct Span { uint32_t address, bytes, crc; };
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
uint32_t stack_pointer() { uint32_t sp; __asm__ volatile("%0 = SP;" : "=d"(sp)); return sp; }
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
    if (xy_forever::usb_service::confirm_key(key, pressed, key_injected != 0)) return;   // a prompt has the keys
#endif
    xyf::key_repeat::event(held, key, pressed != 0, ms);
    dispatch(key, pressed);
}

// A key to the page on screen: the Mods page or the menu (on_key, and the repeat of a held - / +).
void dispatch(uint32_t key, uint32_t pressed) {
#if defined(XYF_MODS_PAGE)
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

// Knob turns (knob_input.hpp) move the selection as - / + do: not in the Uninstall dialog (its - / +
// flip two buttons), not while the Mods page shows its job. On the Mods page with key_turn (SHIFT + turn: its filter).
void turn_knobs(uint32_t now_ms) {
#if defined(XYF_MODS_PAGE)
    const bool page = mods_shown != 0, job = page && xy_forever::mods_io::page().view == xyf::mods::View::building;
#else
    const bool page = false, job = false;
#endif
    const bool selection = !job && (page || app.screen != xyf::menu::Screen::confirm_uninstall);
    const int32_t rows = xy_forever::knob_input::pass(now_ms, selection);
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

// Screen model for the next present (set by show()).
volatile uint32_t ui_screen __attribute__((section(".data"))) = 0;
const char* volatile ui_status __attribute__((section(".data"))) = 0;
#if defined(XYF_SERVICE_SCREEN)
// Test 054: the USB service's own screen (b), screen 4.
const char* volatile service_lines[4] __attribute__((section(".data"))) = {"", "", "", ""};
#endif

#if defined(XYF_ONE_SESSION)
// The USB mark and activity line on screen (set by draw_screen), and whether the menu loop has the screen
// (key_ui::host_view redraws only then).
volatile uint32_t usb_drawn __attribute__((section(".data"))) = 0;
const char* volatile usb_drawn_line __attribute__((section(".data"))) = 0;
volatile uint32_t menu_screen __attribute__((section(".data"))) = 0;
bool usb_view_changed() {
    return xy_forever::usb_service::host_marks() != usb_drawn ||
           xy_forever::usb_service::host_activity() != usb_drawn_line;
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
#else
    const Chrome chrome = {"", false, 0, false, ""};
#endif
    if (ui_screen == 2) {
        const Message bye = {"Starting", {ui_status}, 1, "", &chrome};
        draw_message(fb, bye);
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
    present();
}

#if defined(XYF_SERVICE_SCREEN)
// Test 054: 0 unchecked, 1 display code matches its CRC, 2 refused (never draws).
volatile uint32_t service_checked __attribute__((section(".data"))) = 0;
#endif
#if defined(XYF_STAGE2)
// ---- Stage-2 services (ABI 1; called by stage 2 through the table) ----------
volatile uint32_t stage2_frame __attribute__((section(".data"))) = 0;
uint16_t* stage2_begin_frame() {
    Result& r = result;
    stage2_frame = 0;
    if (!dma_idle(100)) { ++r.present_skips; return 0; }
    wait_dma();
    if (byte(smc_dma_active)) {     // as present(): DMA idle without a done bit
        *reinterpret_cast<volatile uint8_t*>(smc_dma_active) = 0;
        r.present_skips += 0x10000u;
    }
    stage2_frame = 1;
    return reinterpret_cast<uint16_t*>(word(framebuffer));
}
void stage2_end_frame() {
    if (!stage2_frame) return;      // only after a successful begin_frame
    stage2_frame = 0;
    flush_framebuffer(word(framebuffer));
    transfer();
    if (byte(smc_dma_active)) ++result.presents;
}
uint32_t stage2_pop() {
    if (stage2_head == stage2_tail) return 0;
    return stage2_events[stage2_tail++ & (stage2_queue - 1)];
}
uint32_t stage2_next_event() {
    poll();                          // stock PSoC dispatch -> on_key -> queue
    ++result.polls;
    xy_forever::xyf_boot::tick();
    if (xy_forever::xyf_boot::link_lost()) return xyf::stage2::event_link_lost;
    if (const uint32_t event = stage2_pop()) return event;
    delay_us(5000);
    return 0;
}
uint32_t stage2_now_ms() { return ms_since(result.start_cycles); }
const xyf::stage2::Services stage2_services = {4, stage2_begin_frame, stage2_end_frame, stage2_next_event,
                                              stage2_now_ms};

#if defined(XYF_STAGE2_MENU)
// ABI 2 (main XY Forever, --xyf-stage2-menu): stage 2 runs the menu and
// returns requests (xyf::stage2::decide). Stage 1 performs each one with the
// same calls as its own menu, or answers with a note and calls stage 2 again
// (at most max_rounds times). True: the stage-1 menu (stage 2 asked for it,
// none is loaded, or too many rounds). False: boot now (default or entry, or
// the link was lost -> both built-in).
__attribute__((noinline)) bool run_stage2() {
    using namespace xyf::stage2;
    Result& r = result;
    const uint32_t entry = xy_forever::xyf_boot::stage2_entry();
    if (!entry) return true;
    Abi2 abi2;
    abi2.v1.abi = abi; abi2.v1.bytes = sizeof(abi2); abi2.v1.role = 1;
    abi2.v1.stage1_version = this_stage1;
    for (uint32_t i = 0; i != 3; ++i) abi2.v1.key_bitmap[i] = r.bitmap[i];
    abi2.v1.services = &stage2_services;
    abi2.note = 0;
    stage2_head = stage2_tail = 0;
    stage2_active = 1;
    const uint32_t start = ms_since(r.start_cycles);
    Do next = Do::menu;
    uint32_t chosen = 0;
    for (uint32_t round = 0; round != max_rounds; ++round) {
        uint32_t blob = 0;
        abi2.catalog = xy_forever::xyf_boot::catalog_blob(blob);
        abi2.catalog_bytes = blob;
        abi2.features = abi2.catalog ? uint32_t(feature_catalog) : 0u;
#if defined(XYF_STATE_WRITE)
        abi2.features |= feature_set_default;
#endif
        const uint32_t exit = xyf_stage2_enter(entry, &abi2.v1);
        xy_forever::xyf_boot::stage2_done(exit, ms_since(r.start_cycles) - start);
        const xyf::catalog::Catalog* catalog = xy_forever::xyf_boot::catalog();
        next = decide(exit, xy_forever::xyf_boot::link_lost(), catalog ? catalog->count : 0u, abi2.features,
                      chosen);
        if (next == Do::note_unavailable) { abi2.note = "Not available in this build."; continue; }
        if (next == Do::note_no_entry) { abi2.note = "No such library entry."; continue; }
#if defined(XYF_STATE_WRITE)
        if (next == Do::set_default) {                 // the only write path (a writes the state slot)
            abi2.note = xy_forever::xyf_boot::set_default(chosen);
            if (xy_forever::xyf_boot::link_lost()) { next = Do::boot_builtin; break; }
            continue;
        }
#endif
        break;
    }
    stage2_active = 0;
    dirty = 1;
    if (next == Do::boot_builtin) { r.exit_reason = exit_link; return false; }
    if (next == Do::boot_default) { xy_forever::xyf_boot::choose_default(); r.exit_reason = exit_play; return false; }
    if (next == Do::boot_entry) { xy_forever::xyf_boot::choose(chosen); r.exit_reason = exit_play; return false; }
    return true;                                       // menu (also mtp/set_default when not built in)
}
#else
// Runs a loaded stage 2 once. True: continue into the stage-1 menu (no stage
// 2, or it asked for the menu). False: boot now (exit_continue -> the default,
// or the link was lost -> both built-in).
__attribute__((noinline)) bool run_stage2() {
    Result& r = result;
    const uint32_t entry = xy_forever::xyf_boot::stage2_entry();
    if (!entry) return true;
    xyf::stage2::Abi abi;
    abi.abi = xyf::stage2::abi; abi.bytes = sizeof(abi); abi.role = 1;
    abi.stage1_version = xyf::stage2::this_stage1;
    for (uint32_t i = 0; i != 3; ++i) abi.key_bitmap[i] = r.bitmap[i];
    abi.services = &stage2_services;
    stage2_head = stage2_tail = 0;
    stage2_active = 1;
    const uint32_t start = ms_since(r.start_cycles);
    const uint32_t exit = xyf_stage2_enter(entry, &abi);
    stage2_active = 0;
    xy_forever::xyf_boot::stage2_done(exit, ms_since(r.start_cycles) - start);
    dirty = 1;
    const xyf::stage2::Next next = xyf::stage2::after(exit, xy_forever::xyf_boot::link_lost());
    if (next == xyf::stage2::Next::menu) return true;
    if (next == xyf::stage2::Next::boot_builtin) { r.exit_reason = exit_link; return false; }
    xy_forever::xyf_boot::choose_default();
    r.exit_reason = exit_play;
    return false;
}
#endif
#endif
}

extern "C" void xy_forever_key_ui() {
    Result& r = result;
    r.version = 4;                                   // 4: Test021+ menu (layout as 3)
    r.start_cycles = cycles_div1024();
    r.role = byte(role_byte);
    // 0x8807ce7e is only 2-aligned: read bytewise (a 32-bit load would fault).
    r.frame_header = byte(snapshot) | byte(snapshot + 1) << 8 | byte(snapshot + 2) << 16 |
                     uint32_t(byte(snapshot + 3)) << 24;
    for (uint32_t i = 0; i != 3; ++i)
        r.bitmap[i] = byte(snapshot + 4 + i * 4) | byte(snapshot + 5 + i * 4) << 8 |
                      byte(snapshot + 6 + i * 4) << 16 | uint32_t(byte(snapshot + 7 + i * 4)) << 24;
    r.stages = stage_snapshot;
    if (r.role != 1) { r.exit_reason = exit_role; return; }
    if (byte(snapshot + 2) != 0x42 || byte(snapshot + 3) != 0x0a) { r.exit_reason = exit_snapshot; return; }
    r.play_snapshot = (byte(snapshot + 4 + play_key / 8) >> (play_key % 8)) & 1u;
#if defined(XYF_USB_MENU)
    // Test 058: the host asked for the menu (CONTINUE mode 2/3) without PLAY.
    if (!r.play_snapshot && !xy_forever::usb_service::menu_requested() && !xy_forever::xyf_boot::menu_forced() &&
        !xy_forever::usb_service::home_active()) {
        r.exit_reason = exit_no_play; return;
    }
#else
    if (!r.play_snapshot) { r.exit_reason = exit_no_play; return; }
#endif
    r.stages |= stage_play;

    for (uint32_t i = 0; i != sizeof(spans) / sizeof(spans[0]); ++i)
        if (crc32(spans[i].address, spans[i].bytes) != spans[i].crc) r.identity_mismatch |= 1u << i;
    if (r.identity_mismatch) { r.exit_reason = exit_identity; return; }
    r.stages |= stage_identity;

    {
        xy_forever::cold_heap::Snapshot heap; xy_forever::cold_heap::Result result_code;
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
        show(0, status);
        xy_forever::knob_input::start(!r.identity_mismatch && (r.stages & stage_identity));
#if defined(XYF_ONE_SESSION)
        menu_screen = 1;                             // key_ui::host_view may redraw from here
#endif
        for (;;) {
            poll();
            ++r.polls;
#if defined(XYF_SETTINGS)
            xy_forever::usb_service::settings_load();       // once A serves (one bounded read)
#endif
            turn_knobs(ms_since(r.start_cycles));            // after the stock poll
            // A held - / + repeats (not on the Uninstall dialog, whose - / + flips its two buttons).
            if (xyf::key_repeat::due(held, ms_since(r.start_cycles)) && app.screen != xyf::menu::Screen::confirm_uninstall &&
                app.screen != xyf::menu::Screen::confirm_save)                // no repeat in dialogs
                dispatch(held.key, 1);
#if defined(XYF_ONE_SESSION)
            // Test 065: home answers every USB command; CONTINUE leaves home to boot the default.
            if (const uint32_t leave = xy_forever::usb_service::home_pump()) {
                // 2 + i = library.boot of catalog entry i, the PLAY-on-an-entry path below.
                if (leave == 1) xy_forever::xyf_boot::choose_default();
                else xy_forever::xyf_boot::choose(leave - 2);
                r.exit_reason = exit_play; break;
            }
#endif
            if (xy_forever::xyf_boot::tick()) dirty = 1;
#if defined(XYF_ONE_SESSION)
            if (const uint32_t view = xy_forever::xyf_boot::storage_view()) {   // 193: the first row, the rows that need it
                if (view != app.storage) { app.storage = view; dirty = 1; }
            }
#endif
#if defined(XYF_ONE_SESSION)
            // b never resets itself on a lost link. With USB on, the menu
            // stays (USB still answers; commands that need a get link) until a power cycle.
            // Without USB nothing can answer: stop as before.
            const bool lost = xy_forever::xyf_boot::link_lost();
            if (lost && !xy_forever::usb_service::home_usb()) { r.exit_reason = exit_link; break; }
#else
            const bool lost = false;
            if (xy_forever::xyf_boot::link_lost()) { r.exit_reason = exit_link; break; }   // both boot built-in
#endif
#if defined(XYF_MODS_PAGE)
            // The Settings page reads the mod packages too (its Mod memory row needs the base's layouts)
            const bool settings_shown = !mods_shown && app.screen == xyf::menu::Screen::settings;
            if (settings_shown) xy_forever::mods_io::prepare();
            if (xy_forever::mods_io::pump(mods_shown || settings_shown) && mods_shown) dirty = 1;   // the page's reading and job
            if (xy_forever::mods_io::menu_state(app) && !mods_shown) dirty = 1;   // pending, the first row
#endif
#if defined(XYF_CONTENT_SWITCH)
            if (const char* line = xy_forever::usb_service::content_line()) { status = line; dirty = 1; }
#endif
#if defined(XYF_MODS_PAGE)
            // The save sequence ended: boot the saved configuration (the default), or back to the menu with
            // the reason (the changes stay pending).
            if (const uint32_t saved = xy_forever::mods_io::save_state()) {
                if (saved == xy_forever::mods_io::save_boot) {
                    xy_forever::mods_io::save_done();
                    xy_forever::xyf_boot::choose_default(); r.exit_reason = exit_play; break;
                }
                if (saved == xy_forever::mods_io::save_failed) {
                    status = xy_forever::mods_io::save_reason();
                    xy_forever::mods_io::save_done();
                    mods_shown = 0; app.screen = xyf::menu::Screen::main; dirty = 1;
                }
            }
#endif
            if (const uint32_t action = app_action) {
                using xyf::menu::Action;
                app_action = 0;
                dirty = 1;
                if (lost && (action == uint32_t(Action::continue_boot) || action == uint32_t(Action::boot_once) ||
                             action == uint32_t(Action::save) || action == uint32_t(Action::discard))) {
                    app.screen = xyf::menu::Screen::main;
                    status = " ";                          // the status line shows the lost link
                    continue;
                }
#if defined(XYF_USB_FILE_WRITE)
                if (action == uint32_t(Action::continue_boot) || action == uint32_t(Action::discard) ||
                    action == uint32_t(Action::boot_once) || action == uint32_t(Action::set_default) ||
                    action == uint32_t(Action::save)) {       // and Save while a USB job runs
                    if (const char* busy = xy_forever::usb_service::menu_busy()) {
                        status = busy; app.screen = xyf::menu::Screen::main; continue;
                    }
                }
#endif
                if (action == uint32_t(Action::continue_boot) || action == uint32_t(Action::discard)) {   // Discard
                    xy_forever::xyf_boot::choose_default(); r.exit_reason = exit_play; break;
                }
                if (action == uint32_t(Action::boot_once)) {
                    if (const char* note = content_note(app.entry)) { status = note; continue; }
                    xy_forever::xyf_boot::choose(app.entry); r.exit_reason = exit_play; break;
                }
#if defined(XYF_STATE_WRITE)
                if (action == uint32_t(Action::set_default)) {
#if defined(XYF_CONTENT_SWITCH)
                    if (const char* note = xy_forever::usb_service::content_default(app.entry)) {   // content first
                        status = note; app.screen = xyf::menu::Screen::main; continue;
                    }
#endif
                    status = xy_forever::xyf_boot::set_default(app.entry);   // the only write path
                    if (xy_forever::xyf_boot::link_lost()) { r.exit_reason = exit_link; break; }
                    app.screen = xyf::menu::Screen::main;      // first row = the (new) default's name
                    continue;
                }
#endif
#if defined(XYF_MODS_PAGE)
                if (action == uint32_t(Action::save)) {       // the page shows the save sequence
                    mods_shown = 1;
                    xy_forever::mods_io::save_begin();
                    continue;
                }
#endif
#if defined(XYF_SETTINGS)
                if (action == uint32_t(Action::change_setting)) {   // Mod memory, pending until boot (as the Mods page)
#if defined(XYF_MODS_PAGE)
                    app.settings_note = xy_forever::mods_io::memory_flip();
#else
                    app.settings_note = "Mods: not available in this build.";
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
                app.screen = xyf::menu::Screen::main;          // show the result line
                status = action == uint32_t(Action::boot_once) ? "Boot once: not available yet." :
                         action == uint32_t(Action::set_default) ? "Set default: needs storage writes (not yet)." :
                         "Uninstall: not available yet.";
            }
#if defined(XYF_ONE_SESSION)
            if (usb_view_changed()) dirty = 1;     // the USB mark went on or out, or the activity line changed
#endif
            if (dirty) { dirty = 0; show(0, status); }
            // Test 059: after a failed check the menu waits for a choice (never the broken default).
#if defined(XYF_ONE_SESSION)
            // Test 066 (project rule): the boot menu never times out.
            if (false) {
#else
            if (!xy_forever::xyf_boot::menu_forced() && ms_since(r.start_cycles) > last_key_ms + idle_timeout_ms) {
#endif
                r.exit_reason = exit_timeout; break;
            }
            delay_us(5000);
        }
#if defined(XYF_ONE_SESSION)
        menu_screen = 0;
#endif
#if defined(XYF_STAGE2)
        }
#endif
        show(2, r.exit_reason == exit_play ? "Booting normal OP-XY..." : "No keys for 60 s. Booting normal OP-XY...");
    }
    set_key_callback(0);
    // Same stock teardown the bootloader ran before handoff; closes are
    // guarded by each transport's open flag, so repeating it is idempotent.
    close_links();
    if (!byte(psoc_open)) r.stages |= stage_closed;
    // Leave the display DMA finished and its busy flag clear for the OS.
    if (dma_idle(200)) wait_dma();
    {
        xy_forever::cold_heap::Snapshot heap; xy_forever::cold_heap::Result result_code;
        const bool ok = xy_forever::cold_heap::guard(heap, result_code);
        r.heap_results |= (uint32_t(ok) | uint32_t(result_code) << 8) << 16;
        if (ok) r.heap_brk_after = heap.brk;
    }
    r.stack_used = stack_used(painted);
    r.end_ms = ms_since(r.start_cycles);
}

#if defined(XYF_SERVICE_SCREEN)
namespace xy_forever { namespace key_ui {
// Test 054 (b, USB service): XY Forever header + "USB service" + four lines.
// Same present path as the menu (stock wait_dma/transfer), gated by the CRC of
// the display span; never touches the PSoC link. Returns false if not drawn.
bool service_screen(const char* a, const char* b, const char* c, const char* d) {
    if (!service_checked) service_checked = crc32(spans[0].address, spans[0].bytes) == spans[0].crc ? 1u : 2u;
    if (service_checked != 1u) return false;
    service_lines[0] = a ? a : ""; service_lines[1] = b ? b : "";
    service_lines[2] = c ? c : ""; service_lines[3] = d ? d : "";
    show(4, "");
    return true;
}
// Test 058: a key event from the host (menu over USB), as if from the PSoC.
void inject_key(uint32_t key, uint32_t pressed) { key_injected = 1; on_key(key, pressed); key_injected = 0; }
#if defined(XYF_ONE_SESSION)
void refresh_catalog() {
#if defined(XYF_MODS_PAGE)
    const uint32_t row = app.main_selected;
#endif
#if defined(XYF_SETTINGS)
    const bool keep = app.screen == xyf::menu::Screen::settings;   // a save's rescan keeps the Settings page, its row and line
    const uint32_t kept_row = app.main_selected, kept_setting = app.settings_selected;
    const char* const kept_note = app.settings_note;
#endif
    const uint32_t storage = app.storage;          // 193: the view stays (the loop updates it)
    xyf::menu::start(app, xy_forever::xyf_boot::catalog(), true);
    app.storage = storage;
#if defined(XYF_SETTINGS)
    if (keep) {
        app.screen = xyf::menu::Screen::settings; app.main_selected = kept_row;
        app.settings_selected = kept_setting; app.settings_note = kept_note;
    }
#endif
#if defined(XYF_MODS_PAGE)
    // A build's commit rescans the library while the Mods page is shown: STOP must come back to its
    // row, not to Continue (whose PLAY would boot the default).
    if (mods_shown) app.main_selected = row;
#endif
    app_action = 0;
    dirty = 1;
}
void host_view() { if (menu_screen && usb_view_changed()) present(); }
#endif
} }
#endif

// What the simulator puts in place of the parts it does not host: the stock 1.2.1 image in
// memory and the retained state the stock bootloader leaves at XY Forever's cold entry;
// cold.cpp's policy and report fixture; and B's menu (key_ui.cpp), whose screen and keys are
// not hosted: home_pump runs in the same loop and order as key_ui's menu loop. The menu's
// state machine (xyf_menu) and the Mods page (xyf_mods_io) are: injected keys drive them as key_ui's
// on_key does, the page's pump runs in the loop as in key_ui's, and each change of the page is logged
// ("mods page: ..."); the other menu actions are logged, not performed.
#include "runtime.hpp"
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <vector>
#include "../native/ldr.hpp"
#include "key_ui.hpp"
#include "usb_service.hpp"
#include "xyf_boot.hpp"
#include "warm_snapshot.hpp"
#include "cold_layout.hpp"
#include "xyf_menu.hpp"
#include "xyf_mods_io.hpp"
#include "key_repeat.hpp"
#include "knob_input.hpp"
#include "xyf_crc32.hpp"

namespace xyf_sim {
const char* stock_path = 0;
void cdc_wait(int ms);
namespace {
std::vector<uint8_t> stock_image;    // {u32 address, u32 bytes, bytes...} records (xy_forever/sim.py)
}
uint32_t driver() { return role ? b_driver : a_driver; }
void load_stock() {
    if (stock_image.empty()) {
        FILE* f = fopen(stock_path, "rb");
        if (!f) crash("stock memory image %s missing", stock_path);
        uint8_t chunk[65536];
        size_t n;
        while ((n = fread(chunk, 1, sizeof(chunk), f)) > 0) stock_image.insert(stock_image.end(), chunk, chunk + n);
        fclose(f);
    }
    for (size_t at = 0; at + 8 <= stock_image.size();) {
        uint32_t address, bytes;
        memcpy(&address, &stock_image[at], 4); memcpy(&bytes, &stock_image[at + 4], 4);
        at += 8;
        if (at + bytes > stock_image.size()) crash("stock memory image truncated");
        if (mapped(address, bytes)) memcpy(host(address), &stock_image[at], bytes);
        at += bytes;
    }
}
// The retained state at cold entry that the hosted code checks (cold_storage capture_state,
// peer_services capture_state, cold_heap inspect, retained_lookup), as Tests 008-024 saw it.
void cold_state(int which, bool power_on, bool play) {
    otp_start();                                      // stock may have initialized B's OTP before XY Forever
    w8(0x8807cac4u, uint8_t(which));                    // role
    w8(0x88a1f3bcu, 0); w8(0x8804f6e4u, 0); w8(0x11907ab4u, 1);
    w32(0x11b0029cu, 1); w32(0x88073000u, 0x11b00200u);  // storage controller open, its reference held
    w32(0x11b00298u, 0x88072d8cu); w8(0x88072d8cu, 1);
    w32(0x1fc02104u, 1u << 11);                          // IMASK: IVG11
    const uint32_t rsi = 0x48;                           // storage controller interrupt (SEC source 0x48)
    w8(0x11b00ac4u + rsi, rsi + 1); w32(0x11b008d0u + (rsi + 1) * 4, 0x88a061d6u);
    w32(0x11b005d4u + (rsi + 1) * 8, 0x11b00200u); w32(0x20004800u + rsi * 8, 5);
    for (uint32_t i = 0; i != 4; ++i) w32(0x8803d06cu + 4 * i, 0);   // stock log sinks (muted)
    for (uint32_t i = 0; i != 4; ++i) w32(0x8803f6b8u + 4 * i, 0);   // posix provider registry: empty
    // Stock allocator: one heap, break above the SPI driver records placed below.
    w32(0x889ebaa0u, 0x88a65bf8u); w32(0x889ebaa4u, 1);
    w32(0x88a2a4d0u, 0x88a65bf8u); w32(0x88a2a4d4u, 0x01fffffcu); w32(0x88a2a4d8u, 0); w32(0x88a2a4dcu, 0);
    const uint32_t context = 0x88a65bf8u;
    w32(context, 0x88a65c18u); w32(context + 4, 0x01ffffdcu); w32(context + 8, 0); w32(context + 12, 0x88a68000u);
    w32(context + 16, 0); w32(context + 20, 0); w32(context + 24, 0);
    // SPI link driver (peer_services capture_state / shape): retained, closed.
    const uint32_t d = driver(), spi = which ? 0x20055004u : 0x20057004u;
    memset(host(d), 0, 0x400);
    w32(which ? 0x11b00060u : 0x11b00064u, d);
    w32(d, which ? 0x11906f24u : 0x119072f0u); w32(d + 8, spi);
    w32(d + 0xc, which ? 0x20080000u : 0x20084000u); w32(d + 0x10, which ? 0x20081000u : 0x20085000u);
    for (uint32_t id = 0x14; id <= 0x15; ++id) {        // the two link DMA-error interrupts stay registered
        w8(0x11b00ac4u + id, uint8_t(id + 1));
        w32(0x11b008d0u + (id + 1) * 4, 0x88a303d4u); w32(0x11b005d4u + (id + 1) * 8, 0x88a306e8u + (id - 0x14) * 0x34u);
        w32(0x20004800u + id * 8, 5);
    }
    const uint32_t gpio = d + 0x800;                     // the retained GPIO callback vector
    memset(host(gpio), 0, 0x40);
    w32(0x88a30650u, gpio); w32(0x88a30654u, which ? 2 : 1);
    if (which) {
        w32(gpio, 1); w32(gpio + 4, 0x20); w32(gpio + 8, 0x11a00664u); w32(gpio + 12, d);
        w32(gpio + 16, 0); w32(gpio + 20, 0x400000); w32(gpio + 24, 0x11a00458u); w32(gpio + 28, d);
    } else {
        w32(gpio, 0); w32(gpio + 4, 0x8000); w32(gpio + 8, 0x889f6244u); w32(gpio + 12, d);
        w32(0x8804f6e0u, primary); w32(primary, 0); w32(primary + 4, 0);
    }
    if (which == 1) {
        w32(0x880734c0u, 0x11a03e04u);                   // stock CDC receive callback (Test 032)
        w8(0x88a35934u, 0);                              // stock USB not started
        // Boot-time key snapshot (PSoC reply type 0x42, 10-byte bitmap; KPlay = bit 51).
        const uint32_t frame = 0x8807ce7eu;
        memset(host(frame), 0, 16);
        w8(frame + 2, 0x42); w8(frame + 3, 0x0a);
        if (play) w8(frame + 4 + 51 / 8, uint8_t(1u << (51 % 8)));
        // The stock PSoC receive queue's indexes as the device showed them idle in the menu (25/25).
        w32(xy_forever::knob_input::read_index, 25); w32(xy_forever::knob_input::write_index, 25);
        // Display framebuffer (SCREEN reads it): a plain test pattern; the menu is not drawn.
        const uint32_t fb = 0x89c00000u;
        w32(0x8803dc58u, fb);
        uint16_t* px = static_cast<uint16_t*>(host(fb));
        for (uint32_t x = 0; x != 480; ++x)
            for (uint32_t y = 0; y != 256; ++y) px[x * 256 + y] = uint16_t(((x / 40 + y / 40) & 1) ? 0x2104 : 0x0000);
    }
    (void)power_on;
}
}

// cold.cpp: the cold LDR policy published for file boot's admission check (same ranges,
// extracted from target/cold.cpp by xy_forever/sim.py).
namespace {
using xyf_core::Range;
#include "cold_policy.inc"
const xyf_core::Policy sim_policy = {allowed, sizeof(allowed) / sizeof(allowed[0]),
                                     protected_ranges, sizeof(protected_ranges) / sizeof(protected_ranges[0])};
}
extern "C" { const xyf_core::Policy* volatile xy_forever_cold_policy = &sim_policy; }
// The build's report fixture (xy_forever.probe_report.report_only_fixture).
extern "C" const xyf_core::Byte xy_forever_trace_expected[sizeof(xy_forever::storage_probe::Report)] = {
#include "trace_expected.inc"
};

// key_ui.cpp (B's menu): not hosted. The loop below is its menu loop without the screen
// and the PSoC keys: home_pump, then the session tick, then the link check (a lost link
// restarts B, at most 3 times in a row; refused: with USB on, a lost link keeps the menu and
// USB still answers).
namespace {
xyf::menu::State sim_app;
bool sim_menu = false, sim_mods = false, sim_leave = false;   // sim_leave: Continue / Discard boots
xyf::key_repeat::Held sim_held = {xyf::key_repeat::none, 0};        // as key_ui
int sim_pending = -1;                                               // logged on a change
#include "settings_page.ipp"
uint32_t device_ms() { return uint32_t(xyf_sim::device_cycles() / 400000u); }   // B's clock (advance included)
#if defined(XYF_MODS_PAGE)
void log_page() {                       // the page's state, for tests (tests/test_sim_mods_page.py)
    using namespace xyf::mods;
    const Page& p = xy_forever::mods_io::page();
    static char on[2048];
    uint32_t n = 0;
    for (uint32_t r = 0; r != p.shown; ++r) {
        const Package& m = p.packages[p.rows[r]];
        if (!(m.flags & flag_on)) continue;
        n += snprintf(on + n, sizeof(on) - n, "%s%s%s", n ? "," : "", m.kind == kind_extension ? "ext-" : "",
                      text_at(p, m.id));
        if (n >= sizeof(on)) n = sizeof(on) - 1;
    }
    on[n] = 0;
    const char* selected = p.selected >= p.count ? "none" : text_at(p, p.packages[p.selected].id);   // no other rows
    xyf_sim::log("mods page: shown=%d view=%u count=%u shown_rows=%u filter=%u listed=%u selected=%s on=%s used=%u "
                 "limit=%u line=%u longest=%u ms=%u changed=%u expanded=%u shared=%u text=%s", sim_mods ? 1 : 0,
                 uint32_t(p.view), p.count, p.shown, p.filter, p.listed, selected, on, used(p), memory_limit(p),
                 uint32_t(p.line), xy_forever::mods_io::longest_ms(), device_ms(), changed(p) ? 1u : 0u,
                 p.expanded ? 1u : 0u, shared(p), p.text);   // the memory choice, shared
}
// The progress screen (#197: the converge sequence, key_ui draws it in place of any page): a full redraw, with the
// home row and screen under it (they never change) and the toggles that are on (never changed either).
void log_progress() {
    const xyf::mods::Progress& r = xy_forever::mods_io::progress();
    const xyf::mods::Page& p = xy_forever::mods_io::page();
    uint32_t on = 0;
    for (uint32_t i = 0; i != p.shown; ++i) on += p.packages[p.rows[i]].flags & xyf::mods::flag_on ? 1u : 0u;
    xyf_sim::log("progress: job=%u bar=%u phase=%u done=%u total=%u commit=%u save=%u stage=%u cancel=%u screen=%u "
                 "row=%u mods=%d on=%u ms=%u title=%s note=%s", r.job, r.bar, r.job_phase, r.job_done, r.job_total,
                 r.job_commit, r.save, r.save_stage, r.cancel, uint32_t(sim_app.screen), sim_app.main_selected,
                 sim_mods ? 1 : 0, on, device_ms(), r.title ? r.title : "", r.note);
}
void log_bar() {                        // a progress-only redraw (the bar and its numbers moved)
    const xyf::mods::Progress& r = xy_forever::mods_io::progress();
    xyf_sim::log("mods bar: job=%u bar=%u phase=%u done=%u total=%u commit=%u ms=%u", r.job, r.bar, r.job_phase,
                 r.job_done, r.job_total, r.job_commit, device_ms());
}
#endif
// The USB mark and the activity line key_ui draws (draw_screen), logged on each change ("menu: usb mark on",
// "menu: usb line: USB: writing files") where key_ui redraws them: every menu pass and key_ui::host_view.
bool sim_usb_seen = false;
const char* sim_usb_line = 0;
// The image line key_ui drew last (draw_screen), logged at each Info page redraw: "menu: info image: image ...".
const char* sim_image_drawn = 0;
void info_drawn() {
    sim_image_drawn = xy_forever::usb_service::image_label();
    if (sim_app.screen == xyf::menu::Screen::info) xyf_sim::log("menu: info image: %s at %u ms", sim_image_drawn, device_ms());
}
void usb_view() {
    const bool seen = xy_forever::usb_service::host_seen();
    const char* const line = xy_forever::usb_service::host_activity();
    if (seen != sim_usb_seen) xyf_sim::log("menu: usb mark %s at %u ms", seen ? "on" : "off", device_ms());
    if (line != sim_usb_line) xyf_sim::log("menu: usb line: %s at %u ms", line ? line : "none", device_ms());
    sim_usb_seen = seen; sim_usb_line = line;
    if (sim_app.screen == xyf::menu::Screen::info && xy_forever::usb_service::image_label() != sim_image_drawn)
        info_drawn();                                                  // as key_ui: the hash settled while Info is open
}
// The PSoC frames the board holds for B (runtime.hpp) reach the stock receive queue where the stock poll
// runs in key_ui's loop: each into slot write % 128, the write index advanced (counting on, or wrapping at
// 128), then the read index set to it (the poll pops them all; type 0x45 is dropped).
void knob_deliver() {
    using namespace xyf_sim;
    using xy_forever::knob_input::read_index; using xy_forever::knob_input::write_index;
    const uint32_t head = shared->knob_head.load();
    uint32_t tail = shared->knob_tail.load(), limit = uint32_t(shared->knob_per_pass.load());
    const uint32_t first = tail;
    for (uint32_t taken = 0;; ++tail, ++taken) {
        if (tail == shared->knob_jump_at.load())       // a jump comes after the frames sent before it
            if (const int32_t jump = shared->knob_jump.exchange(0)) {
                w32(write_index, r32(write_index) + uint32_t(jump)); w32(read_index, r32(write_index));
                log("knob: indexes stepped by %d to %u", jump, r32(write_index));
            }
        if (tail == head || (limit && taken == limit)) break;
        const Shared::KnobFrame& f = shared->knob_frame[tail % Shared::knob_frames];
        const uint32_t w = r32(write_index);
        memcpy(host(xy_forever::knob_input::queue + (w % 128) * xy_forever::knob_input::slot_bytes), f.bytes, f.length);
        w32(write_index, shared->knob_wrap ? (w + 1) % 128 : w + 1);
    }
    if (tail == first) return;
    shared->knob_tail.store(tail);
    w32(read_index, r32(write_index));
}
// key_ui's stock identity (its CRC spans over the PSoC handler and the poll's frame-type switch): the knob
// reader depends on it. set knob_identity_fail 1: another image.
bool knob_identity() {
    const struct { uint32_t address, bytes, crc; } spans[] = {{0x88a24500u, 0x63cu, 0xa8d90843u},
                                                              {0x88a24c94u, 0x50u, 0x85f79bb1u}};
    for (const auto& s : spans)
        if (xyf::crc::update(0, static_cast<const uint8_t*>(xyf_sim::host(s.address)), s.bytes) != s.crc) return false;
    return !xyf_sim::shared->knob_identity_fail;
}
#include "menu_keys.ipp"                                               // dispatch: the keys, as key_ui
// A key from the PSoC (real_keys_deliver) or the host's KEY command (inject_key), as key_ui's on_key.
void key_event(uint32_t key, uint32_t pressed, bool injected) {
#if defined(XYF_SETTINGS)
    if (xy_forever::usb_service::confirm_key(key, pressed, injected)) {   // #65: the question has the keys
        xyf_sim::log("trust: key %u %s (%s)", key, pressed ? "pressed" : "released", injected ? "injected" : "real");
        return;
    }
#endif
    if (key == xyf::menu::key_minus || key == xyf::menu::key_plus)   // the repeat tests time from these
        xyf_sim::log("menu: key %u %s at %u ms", key, pressed ? "pressed" : "released", device_ms());
    xyf::key_repeat::event(sim_held, key, pressed != 0, device_ms());
    dispatch(key, pressed);
}
// Control "realkey K P": key events as the PSoC sends them (not injected), taken where the stock poll runs.
void real_keys_deliver() {
    using xyf_sim::shared; using xyf_sim::Shared;
    const uint32_t head = shared->real_head.load();
    uint32_t tail = shared->real_tail.load();
    for (; tail != head; ++tail) {
        const uint32_t e = shared->real_key[tail % Shared::real_keys];
        xyf_sim::log("menu: real key %u %s", e & 0xffu, e >> 8 & 1u ? "pressed" : "released");
        key_event(e & 0xffu, e >> 8 & 1u, false);
    }
    shared->real_tail.store(tail);
}
}
namespace xy_forever { namespace key_ui {
Result result = {magic};
void crash_disarm() {}                                                 // no vector: the screen is not hosted
#if defined(XYF_CRASH_TRIGGER)
bool crash_armed_now() { return sim_menu; }                           // as key_ui: armed while the menu has the screen
void crash_fault(uint32_t kind) {                                     // no exception vector here: B stops answering
    xyf_sim::log("crash trigger: kind %u; the simulator has no fault or crash screen: B halts here", kind);
    xyf_sim::exit_processor(xyf_sim::exit_halt);
}
#endif
bool service_screen(const char* a, const char* b, const char* c, const char* d) {
    xyf_sim::log("screen: %s | %s | %s | %s", a ? a : "", b ? b : "", c ? c : "", d ? d : "");
    return true;
}
void inject_key(uint32_t key, uint32_t pressed) { key_event(key, pressed, true); }
void host_view() { if (sim_menu) usb_view(); }                        // as key_ui (the menu loop has the screen)
// #114, as key_ui_where.ipp: a wait past where::paint_ms while the menu has the screen logs "menu: where W3
// shown" where key_ui paints the code, and "menu: where W3 cleared" where it redraws without it.
uint32_t sim_where = 0, sim_where_since = 0, sim_where_shown = 0;
uint32_t where_enter(uint32_t code) {
    const uint32_t before = sim_where;
    sim_where = xyf::where::entered(code); sim_where_since = device_ms();
    return before;
}
void where_wait() {
    const uint32_t code = xyf::where::current(sim_where);
    if (!sim_menu || sim_where_shown || !code || device_ms() - sim_where_since < xyf::where::paint_ms) return;
    sim_where_shown = code;
    xyf_sim::log("menu: where W%u shown at %u ms", code, device_ms());
}
void where_leave(uint32_t before) {
    sim_where = xyf::where::current(before) | (sim_where & 0xffff0000u); sim_where_since = device_ms();
    if (!sim_where_shown) return;
    xyf_sim::log("menu: where W%u cleared at %u ms", sim_where_shown, device_ms());
    sim_where_shown = 0;
}
void refresh_catalog() {                                               // as key_ui (the page stays, and its row)
    const uint32_t row = sim_app.main_selected;                        // #197: as key_ui, never the home row
#if defined(XYF_SETTINGS)
    const xyf::menu::State kept = sim_app;                             // as key_ui (the Settings page, its list stay)
    const bool keep = kept.screen == xyf::menu::Screen::settings || kept.screen == xyf::menu::Screen::options ||
                      kept.screen == xyf::menu::Screen::packs;
#endif
    const uint32_t storage = sim_app.storage;                         // 193: as key_ui.cpp
    xyf::menu::start(sim_app, xyf_boot::catalog(), true);
    sim_app.storage = storage;
#if defined(XYF_SETTINGS)
    if (keep) {
        sim_app.screen = kept.screen; sim_app.main_selected = kept.main_selected; sim_app.entry = kept.entry;
        sim_app.settings_selected = kept.settings_selected; sim_app.settings_note = kept.settings_note;
        sim_app.options = kept.options; sim_app.option_count = kept.option_count;
        sim_app.option_current = kept.option_current; sim_app.option_selected = kept.option_selected;
        xyf_sim::log("menu: rescan kept the settings page");
        if (kept.screen == xyf::menu::Screen::packs) usb_service::settings_open(sim_app, xyf::settings::row_packs);
    }
#endif
    sim_app.main_selected = row;
}
} }
extern "C" void xy_forever_key_ui() {
    using namespace xy_forever;
    const uint32_t frame = 0x8807ce7eu;
    const bool play = xyf_sim::r8(frame + 2) == 0x42 && xyf_sim::r8(frame + 3) == 0x0a &&
                      (xyf_sim::r8(frame + 4 + 51 / 8) >> (51 % 8) & 1u);
    if (!play && !usb_service::menu_requested() && !xyf_boot::menu_forced() && !usb_service::home_active()) return;
    xyf_sim::log("menu (home): %s", xyf_boot::status());
    xyf::menu::start(sim_app, xyf_boot::catalog(), true);
    sim_app.storage = xyf_boot::storage_view();                        // 193: as key_ui.cpp
    sim_menu = true; sim_mods = false; sim_leave = false; sim_pending = -1; sim_held.key = xyf::key_repeat::none;
    sim_usb_seen = false; sim_usb_line = 0;                            // nothing drawn yet
    knob_input::start(knob_identity());                                // as key_ui.cpp
    xyf_sim::log("knob: reader state %u", knob_input::result.state);
    bool noted = false;
#if defined(XYF_SETTINGS)
    bool settings_logged = false;
#endif
    for (;;) {
#if defined(XYF_SETTINGS)
        sim_settings_pass(settings_logged);
#endif
        knob_deliver();                                                    // where key_ui's stock poll runs
        real_keys_deliver();                                               // the PSoC's key events
        {                                                                  // as key_ui.cpp turn_knobs
            const knob_input::Result& k = knob_input::result;
            const uint32_t before[] = {k.frames, k.dropped, k.resyncs, k.idle, k.pass_read, k.max_read};
#if defined(XYF_MODS_PAGE)
            const bool job = mods_io::converging();
#else
            const bool job = false;
#endif
            const bool selection = !job && (sim_mods || (sim_app.screen != xyf::menu::Screen::confirm_uninstall &&
                                                         sim_app.screen != xyf::menu::Screen::confirm_safe));
            const int32_t rows = knob_input::pass(device_ms(), selection);
            const uint32_t step = (rows > 0 ? xyf::menu::key_plus : xyf::menu::key_minus) | (sim_mods && !job ? xyf::menu::key_turn : 0u);
            for (int32_t i = rows < 0 ? -rows : rows; i; --i) dispatch(step, 1);   // as key_ui (the Mods page knows a turn)
            const uint32_t after[] = {k.frames, k.dropped, k.resyncs, k.idle, k.pass_read, k.max_read};
            if (rows || memcmp(before, after, sizeof(before)))
                xyf_sim::log("knob: rows=%d read=%u screen=%u main=%u accept=%u mods=%d frames=%u "
                             "dropped=%u resyncs=%u skipped=%u idle=%u moves=%u max_read=%u max_moves=%u ms=%u",
                             rows, k.pass_read, uint32_t(sim_app.screen), sim_app.main_selected,
                             sim_app.confirm_accept ? 1u : 0u, sim_mods ? 1 : 0, k.frames,
                             k.dropped, k.resyncs, k.skipped, k.idle, k.moves, k.max_read, k.max_moves, device_ms());
        }
        if (const uint32_t leave = usb_service::home_pump()) {             // as key_ui.cpp
            if (leave == 1) xyf_boot::choose_default();
            else { xyf_sim::log("menu: library.boot: catalog entry %u once", leave - 2); xyf_boot::choose(leave - 2); }
            break;
        }
        xyf_sim::shared->package_gc = usb_service::idle_gc_state();
        if (xyf::key_repeat::due(sim_held, device_ms()) && xyf::menu::repeat_keys(sim_app)) {
            xyf_sim::log("menu: repeat key %u at %u ms", sim_held.key, device_ms());   // as key_ui.cpp
            dispatch(sim_held.key, 1);
        }
        xyf_boot::tick();
        bool storage_moved = false; (void)storage_moved;                 // 193: the home row is logged again
        if (const uint32_t view = xyf_boot::storage_view()) {           // 193: as key_ui.cpp (the first row)
            if (view != sim_app.storage) {
                sim_app.storage = view; storage_moved = true;
                xyf_sim::log("menu: storage view %u: row: %s; line: %s", view, xyf::menu::main_label(sim_app, 0),
                             xyf_boot::status());
            }
        }
#if defined(XYF_MODS_PAGE)
        const bool settings_shown = !sim_mods && sim_app.screen == xyf::menu::Screen::settings;   // as key_ui.cpp
        if (settings_shown) mods_io::prepare();
        if (const uint32_t redraw = mods_io::pump(sim_mods || settings_shown)) {   // as key_ui.cpp
            if (mods_io::converging() && redraw == 1) log_progress();
            else if (mods_io::converging()) log_bar();
            else if (sim_mods && redraw == 1) log_page();
        }
        static const char* sim_note = 0;                                 // and the save note (a list read)
        if (mods_io::menu_state(sim_app) || storage_moved || int(sim_app.pending) != sim_pending || sim_app.save_note != sim_note) {   // the first row and pending
            sim_pending = int(sim_app.pending); sim_note = sim_app.save_note;
            xyf_sim::log("menu: home row: %s pending=%u blocked=%u memory=%s; note=%s; safe=%s",   // memory, the safe row
                         xyf::menu::main_label(sim_app, 0), sim_app.pending ? 1u : 0u, sim_app.save_blocked ? 1u : 0u,
                         xyf::settings::memory_text(sim_app.memory, sim_app.memory_pending),
                         sim_app.save_note ? sim_app.save_note : "", sim_app.safe_label ? sim_app.safe_label : "-");
        }
#endif
#if defined(XYF_CONTENT_SWITCH)
        if (const char* line = usb_service::content_line()) xyf_sim::log("menu: line: %s", line);
        sim_app.undo_offer = usb_service::content_offer();               // as key_ui.cpp
#endif
#if defined(XYF_MODS_PAGE)
        if (const uint32_t saved = mods_io::converge_state()) {           // as key_ui.cpp
            if (saved == mods_io::converge_boot) {
                // Deterministically hold a REAL completed Save at its pending boot boundary.
                if (xyf_sim::shared->defer_save_boot) {
                    if (xyf_sim::shared->defer_save_boot.exchange(2) == 1) xyf_sim::log("menu: deferred Save boot");
                } else if (!xyf_boot::link_lost() && xyf_boot::link_idle() && !usb_service::page_busy()) {
                    mods_io::converge_done(); xyf_sim::log("menu: saved"); sim_leave = true;
                }
            }
            else if (saved == mods_io::converge_failed) {
                xyf_sim::log("menu: save failed: %s", mods_io::converge_reason());
                mods_io::converge_done();
            }
        }
#endif
        if (sim_leave) {
            const xyf::catalog::Catalog* c = xyf_boot::catalog();
            xyf_sim::log("menu: boots the default %s", c && c->default_index < c->count ? c->items[c->default_index].id : "(none)");
            sim_leave = false;
            xyf_boot::choose_default();
            break;
        }
        usb_view();                                                        // as key_ui.cpp (before its redraw)
        if (xyf_boot::link_lost() && !usb_service::home_usb()) { xyf_sim::log("menu: storage link lost"); break; }
        if (xyf_boot::link_lost() && !noted) {             // B never resets itself on it
            noted = true; xyf_sim::log("menu: storage link lost: %s (USB still answers)", xyf_boot::status());
        }
        xyf_sim::cdc_wait(1);
    }
}

namespace xyf_sim {
// Control "knob", "knob_raw", "knob_jump" and "realkey" (runtime.hpp): the PSoC's frames and keys for B.
std::string input_command(const std::string& line, const char* verb) {
    if (!strcmp(verb, "knob") || !strcmp(verb, "knob_raw")) {
        static uint8_t seq = 0xc4;                        // the PSoC's frame counter (as measured)
        Shared::KnobFrame f = {};
        long count = 1;
        if (verb[4] == 0) {                               // knob ID DELTA [COUNT]: one record each
            long id = 0, delta = 0;
            if (sscanf(line.c_str(), "knob %ld %ld %ld", &id, &delta, &count) < 2 || id < 0 || id > 255)
                return "{\"ok\": false, \"error\": \"knob: ID DELTA [COUNT]\"}";
            const uint8_t bytes[] = {0x41, 0, 0x45, 3, uint8_t(id), uint8_t(uint16_t(delta) >> 8), uint8_t(delta)};
            f.length = sizeof(bytes);
            memcpy(f.bytes, bytes, sizeof(bytes));
        } else {                                          // knob_raw HEX [COUNT]: exactly these bytes
            char hex[96] = "";
            sscanf(line.c_str(), "knob_raw %95s %ld", hex, &count);
            for (const char* h = hex; h[0] && h[1] && f.length < sizeof(f.bytes); h += 2) {
                unsigned byte = 0;
                if (sscanf(h, "%2x", &byte) != 1) return "{\"ok\": false, \"error\": \"knob_raw: bad hex\"}";
                f.bytes[f.length++] = uint8_t(byte);
            }
        }
        const uint32_t head = shared->knob_head.load(), tail = shared->knob_tail.load();
        if (count < 1 || head - tail + uint32_t(count) > Shared::knob_frames)
            return "{\"ok\": false, \"error\": \"knob: COUNT 1.., the mailbox holds 512\"}";
        for (long i = 0; i != count; ++i) {
            Shared::KnobFrame& slot = shared->knob_frame[(head + uint32_t(i)) % Shared::knob_frames];
            slot = f;
            if (verb[4] == 0) slot.bytes[1] = seq++;
        }
        shared->knob_head.store(head + uint32_t(count));
        return "{\"ok\": true}";
    }
    if (!strcmp(verb, "realkey")) {                       // realkey K P
        long key = -1, pressed = -1;
        if (sscanf(line.c_str(), "realkey %ld %ld", &key, &pressed) != 2 || key < 0 || key > 255 || pressed < 0 || pressed > 1)
            return "{\"ok\": false, \"error\": \"realkey: K P\"}";
        const uint32_t head = shared->real_head.load();
        if (head - shared->real_tail.load() >= Shared::real_keys) return "{\"ok\": false, \"error\": \"realkey: mailbox full\"}";
        shared->real_key[head % Shared::real_keys] = uint32_t(key) | uint32_t(pressed) << 8;
        shared->real_head.store(head + 1);
        return "{\"ok\": true}";
    }
    long jump = 0;
    if (!strcmp(verb, "knob_jump") && sscanf(line.c_str(), "knob_jump %ld", &jump) == 1) {   // after the frames sent before it
        if (!shared->knob_jump) shared->knob_jump_at = shared->knob_head.load();
        shared->knob_jump += int32_t(jump);
        return "{\"ok\": true}";
    }
    return "{\"ok\": false, \"error\": \"unknown command\"}";
}
}

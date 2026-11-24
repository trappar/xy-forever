// XY Forever's screens on the host (tests and previews; tests/ui_preview.py): the pages, the crash screen, the
// menu state machine driven by keys (target/xyf_menu*.cpp), and the Mods page's model and view (xyf_mods*.cpp).
// Framebuffers are writable buffers of ui_framebuffer_halfwords() uint16 values (array.array('H'), ...).
// C strings the device code keeps a pointer to are copied here first: they outlive the call.
#include <cstring>
#include <string>
#include <vector>
#include <nanobind/nanobind.h>
#include "hosttest/bind/glue.hpp"
#include "target/crash_screen.hpp"
#include "target/ui_gfx.hpp"
#include "target/ui_pages.hpp"
#include "target/ui_screens.hpp"
#include "target/xyf_menu_view.hpp"
#include "target/xyf_mods_view.hpp"
#include "target/xyf_saver.hpp"
#include "target/xyf_settings.hpp"

namespace nb = nanobind;
using namespace xyf;
using xyf_glue::bytes_or_none;
using xyf_glue::CStr;

namespace {

// A framebuffer argument, checked to hold a whole frame.
class Frame : public xyf_glue::Buffer {
public:
    explicit Frame(nb::handle h) : Buffer(h, true) {
        if (bytes() < gfx::framebuffer_halfwords * sizeof(uint16_t)) throw nb::value_error("framebuffer too small");
    }
    operator uint16_t*() const { return data<uint16_t>(); }
};

// A copy that outlives the call (null stays null; "" too unless keep_empty is false: then null).
const char* kept(std::string& store, const char* s, bool keep_empty = true) {
    if (!s || (!keep_empty && !*s)) return nullptr;
    store = s;
    return store.c_str();
}

// The chrome every page draws with (ui_pages.hpp): the version, the USB mark, the activity line, the image.
ui::Chrome bridge_chrome = {"v0.1.0", false, nullptr, false, "image f458e3cc"};
char bridge_version[32];
char bridge_activity[64];

// The menu flow (ui_flow).
catalog::Catalog cat;
menu::State flow_state;
std::string flow_note, flow_label, flow_continue_note, flow_safe_label;

// The Mods page (ui_mods).
mods::Page page;
mods::Progress run;                             // the progress screen (shown while progress_shown)
bool progress_shown;
mods::Frame frame;
std::string progress_title;

void bind_screens(nb::module_ m) {
    m.def("ui_chrome", [](CStr version, unsigned usb, CStr activity) {
        unsigned i = 0;
        for (; version.p && version.p[i] && i + 1 < sizeof(bridge_version); ++i) bridge_version[i] = version.p[i];
        bridge_version[i] = 0;
        for (i = 0; activity.p && activity.p[i] && i + 1 < sizeof(bridge_activity); ++i)
            bridge_activity[i] = activity.p[i];
        bridge_activity[i] = 0;
        bridge_chrome.version = bridge_version;
        bridge_chrome.usb = (usb & 1u) != 0;
        bridge_chrome.mtp = (usb & 2u) != 0;   // 188: bit 1 MTP
        bridge_chrome.activity = bridge_activity[0] ? bridge_activity : nullptr;
    });
    m.def("ui_render_logo", [](nb::handle fb, int x, int y) {
        Frame f(fb);
        gfx::clear(f, gfx::black);
        gfx::logo(f, x, y, gfx::accent, gfx::white);
    });
    m.def("ui_framebuffer_halfwords", [] { return unsigned(gfx::framebuffer_halfwords); });
    m.def("ui_render_menu", [](nb::handle fb, unsigned selected, CStr status) {
        ui::Menu menu = {{"Continue: OP-XY 1.2.1", "Boot without mods", "Mods", "Settings"},
                         4,
                         selected,
                         status,
                         "- / +  choose      PLAY  select",
                         &bridge_chrome,
                         0};
        ui::draw_menu(Frame(fb), menu);
    });
    m.def("ui_render_message", [](nb::handle fb) {
        ui::Message message = {"About",
                               {"XY Forever", "v0.1.0", "image f458e3cc", "Real bootloader: 1.2.1 (never updated)",
                                "Hold PLAY at power-on to open this menu."},
                               5,
                               "PLAY  back",
                               &bridge_chrome};
        ui::draw_message(Frame(fb), message);
    });
    // B's crash screen (target/key_ui_crash.ipp) for a recorded SEQSTAT, RETX and RETS, and B's where word (#114).
    m.def("ui_render_crash", [](nb::handle fb, unsigned seqstat, unsigned retx, unsigned rets, unsigned where) {
        static crash::Text text;
        const crash::Record record = {1, seqstat, retx, rets};
        crash::describe(record, where, text);
        const ui::Chrome still = {bridge_chrome.version, false, nullptr, false, bridge_chrome.image};
        ui::draw_message(Frame(fb), crash::message(text, &still));
    });
    // The menu with a long wait's code in the corner (key_ui present: the screen, then where::draw).
    m.def("ui_render_where", [](nb::handle fb, unsigned code) {
        Frame f(fb);
        ui::Menu menu = {{"Continue: OP-XY 1.2.1", "Boot without mods", "Mods", "Settings"},
                         4,
                         0,
                         "",
                         "- / +  choose      PLAY  select",
                         &bridge_chrome,
                         0};
        ui::draw_menu(f, menu);
        where::draw(f, code);
    });
    m.def("ui_render_text", [](nb::handle fb, int x, int y, CStr s, int scale) {
        Frame f(fb);
        gfx::clear(f, gfx::black);
        gfx::text(f, x, y, s, gfx::white, scale);
    });
    // The pages (ui_pages.hpp).
    m.def("ui_render_library", [](nb::handle fb, unsigned selected, unsigned count) {
        static const ui::ListRow rows[] = {
            {"OP-XY 1.2.1", "official", true},          {"OP-XY 1.2.5", "official", false},
            {"open-xy: granular", "6 machines", false}, {"open-xy: braids", "3 machines", false},
            {"My test build", "custom", false},         {"OP-XY 1.2.4", "official", false},
            {"open-xy: karplus", "1 machine", false}};
        ui::List list = {"Firmware library",
                         rows,
                         count > 7 ? 7 : count,
                         selected,
                         "- / +  choose      PLAY  boot once      SHIFT+PLAY  set default",
                         &bridge_chrome,
                         false};
        ui::draw_list(Frame(fb), list);
    });
    m.def("ui_render_confirm", [](nb::handle fb, unsigned accept) {
        ui::Confirm c = {"Uninstall XY Forever?",
                         {"Puts back the stock OP-XY 1.2.1 os.ldr and deletes",
                          "xy-forever/ from the device. Your projects stay.", "The real bootloader is not changed."},
                         3,
                         "Cancel",
                         "Uninstall",
                         accept != 0,
                         &bridge_chrome};
        ui::draw_confirm(Frame(fb), c);
    });
    m.def("ui_render_progress", [](nb::handle fb, unsigned done, unsigned total) {
        ui::Progress p = {"Installing OP-XY 1.2.5",
                          "Keyboard firmware (2 of 4)",
                          done,
                          total,
                          "Do not power off the OP-XY.",
                          &bridge_chrome};
        ui::draw_progress(Frame(fb), p);
    });
    // #21: one row, always selected
    m.def("ui_render_home", [](nb::handle fb, unsigned) { ui::draw_home_empty(Frame(fb), &bridge_chrome); });
    // #315: the screen saver's frame after `frames` frames from its start (key_ui draw_screen); its mark's box.
    m.def("ui_render_saver", [](nb::handle fb, unsigned frames) {
        saver::State s;
        saver::start(s, 0);
        saver::pass(s, 60000, 60000, false, false);
        for (unsigned i = 1; i <= frames; ++i) saver::pass(s, 60000 + i * saver::frame_ms, 60000, false, false);
        saver::draw(Frame(fb), s);
        return nb::make_tuple(int(s.x), int(s.y), saver::mark_w, saver::mark_h, unsigned(saver::colors[s.color]));
    });
    m.def("ui_scale", [](uint32_t a, uint32_t b, uint32_t c) { return ui::scale(a, b, c); });
    m.def("ui_list_first", [](unsigned count, unsigned selected) { return unsigned(ui::list_first(count, selected)); });
}

void bind_flow(nb::module_ m) {
    // mode: 0 = built-in copy only, 1 = library + built-in, 2 = nothing bootable.
    m.def("flow_start", [](unsigned mode) {
        std::memset(&cat, 0, sizeof(cat));
        cat.count = 4;
        cat.default_index = 1;
        const char* ids[] = {"granular", "opxy-121", "opxy-125", "noimage"};
        const char* names[] = {"open-xy: granular", "OP-XY 1.2.1", "OP-XY 1.2.5", "My test build"};
        const uint8_t kinds[] = {2, 1, 1, 3}, flags[] = {7, 1, 1, 0};
        for (unsigned i = 0; i != 4; ++i) {
            std::strcpy(cat.items[i].id, ids[i]);
            std::strcpy(cat.items[i].name, names[i]);
            cat.items[i].kind = kinds[i];
            cat.items[i].flags = flags[i];
        }
        cat.items[0].machines = 6;
        menu::start(flow_state, mode == 1 ? &cat : 0, mode != 2);
    });
    m.def("flow_key", [](unsigned key) {
        const unsigned a = unsigned(menu::key(flow_state, key, true));
        menu::key(flow_state, key, false);
        return a;
    });
    m.def("flow_hold", [](unsigned key, unsigned pressed) { menu::key(flow_state, key, pressed != 0); });
    // the Settings page's line; its Mod memory row (settings::Memory) and pending mark, as key_ui and
    // xyf_mods_io menu_state set them.
    m.def("flow_settings", [](unsigned memory, unsigned pending, CStr note) {
        flow_state.memory = memory;
        flow_state.memory_pending = pending != 0;
        flow_state.settings_note = kept(flow_note, note, false);
    });
    // #21: the settings.txt rows (usb_service settings_view): Menu at power-on, USB mode, a newer record.
    m.def("flow_settings_rows", [](unsigned power_on, unsigned usb, unsigned newer) {
        flow_state.power_on_text = settings::power_on_text(power_on);
        flow_state.usb_text = settings::usb_text(usb);
        flow_state.confirm_text = settings::confirm_text(usb & 1);           // #65: On / Off
        flow_state.saver_text = settings::saver_text(power_on * 2 + usb);    // #315: each of its five values
        if (newer) flow_state.settings_note = settings::note_newer;
    });
    // #21: the rows' marks (settings_view: not saved yet; bit per settings::Row) and the option list key_ui opens
    // on change_setting for the selected row (current: the value in effect).
    m.def("flow_settings_marked", [](unsigned marks) { flow_state.settings_marked = marks; });
    m.def("flow_options", [](unsigned current) {
        const unsigned row = flow_state.settings_selected;
        const bool memory = row == settings::row_memory, power_on = row == settings::row_power_on,
                   saver = row == settings::row_saver;
        menu::open_options(flow_state,
                           memory   ? settings::memory_options :
                           power_on ? settings::power_on_options :
                           saver    ? settings::saver_options :
                                      settings::usb_options,
                           memory   ? 2 :
                           power_on ? settings::power_on_option_count :
                           saver    ? settings::saver_option_count :
                                      settings::usb_option_count,
                           current);
    });
    m.def("flow_option", [] { return unsigned(flow_state.option_selected); });
    m.def("flow_storage", [](unsigned view) { flow_state.storage = view; });      // 193: xyf::menu::StorageView
    m.def("flow_draw", [](nb::handle fb, CStr status) { menu::draw(Frame(fb), flow_state, status, &bridge_chrome); });
    // the saved configuration as xyf_mods_io::menu_state sets it (#197: pending, the first row's note).
    m.def("flow_config", [](CStr label, unsigned pending, CStr note) {
        flow_state.continue_label = kept(flow_label, label, false);
        flow_state.pending = pending != 0;
        flow_state.continue_note = kept(flow_continue_note, note, false);
    });
    // The safe-mode row as menu_state sets it (label "": no setup with mods saved) and whether it restores.
    m.def("flow_safe", [](CStr label, unsigned restore) {
        flow_state.safe_label = kept(flow_safe_label, label, false);
        flow_state.safe_restore = restore != 0;
    });
    m.def("flow_screen", [] { return unsigned(flow_state.screen); });
}

void mods_update(uint16_t* fb) {
    if (progress_shown) mods::draw_progress(fb, run, &bridge_chrome, frame);
    else mods::draw(fb, page, &bridge_chrome, frame);
}

nb::object text_or_empty(const char* s) { return nb::bytes(s ? s : ""); }

void bind_mods(nb::module_ m) {
    m.def("mods_reset", [] {
        std::memset(&page, 0, sizeof(page));
        std::memset(&run, 0, sizeof(run));
        progress_shown = false;
        mods::reset(page);
    });
    m.def("mods_base", [](nb::bytes text) {
        return int(mods::parse_base(page, static_cast<const char*>(text.data()), unsigned(text.size())));
    });
    m.def("mods_services", [](nb::bytes text) {
        return int(mods::parse_services(page, static_cast<const char*>(text.data()), unsigned(text.size())));
    });
    m.def("mods_add", [](CStr folder) { return int(mods::add_package(page, folder)); });
    m.def("mods_package", [](unsigned index, nb::bytes text) {
        return int(mods::parse_package(page, index, static_cast<const char*>(text.data()), unsigned(text.size())));
    });
    // The folder names (xyf_mods_io keeps these: mods.apply takes them) and the list view.
    m.def("mods_ready", [](CStr profile, CStr generation) {
        std::strncpy(page.profile, profile, sizeof(page.profile) - 1);
        std::strncpy(page.generation, generation, sizeof(page.generation) - 1);
        page.view = mods::View::list;
        page.line = mods::line_none;
    });
    m.def("mods_reading", [](unsigned done) {
        char a[12];
        mods::decimal(a, done);
        page.view = mods::View::reading;
        mods::say(page, mods::line_info, "Reading mod packages: ", a, "...");
    });
    // as key_ui: the progress screen has the keys while shown
    m.def("mods_key", [](unsigned key, unsigned pressed) {
        return unsigned(progress_shown ? mods::progress_key(run, key, pressed != 0) :
                                         mods::key(page, key, pressed != 0));
    });
    m.def("mods_used", [] { return unsigned(mods::used(page)); });
    m.def("mods_limit", [] { return unsigned(page.limit); });
    // Mod memory: the 4 MiB layout's region, the choice (pending, saved), the limit in use, the shared part,
    // the Settings row and its PLAY.
    m.def("mods_expanded_limit", [] { return unsigned(page.expanded_limit); });
    m.def("mods_set_memory", [](unsigned expanded, unsigned saved) {
        page.expanded = expanded != 0;
        page.expanded_saved = saved != 0;
    });
    m.def("mods_expanded", [] { return unsigned(page.expanded); });
    m.def("mods_memory_limit", [] { return unsigned(mods::memory_limit(page)); });
    m.def("mods_shared", [] { return unsigned(mods::shared(page)); });
    m.def("mods_memory_row", [](unsigned read) { return unsigned(mods::memory_row(page, read != 0)); });
    m.def("mods_flip_memory", [](unsigned read) { return text_or_empty(mods::flip_memory(page, read != 0)); });
    m.def("mods_shown", [] { return unsigned(page.shown); });
    m.def("mods_count", [] { return unsigned(page.count); });
    m.def("mods_position", [] { return unsigned(mods::position(page)); });
    m.def("mods_request", [] {
        if (run.cancel) return unsigned(mods::request_cancel);
        const unsigned r = page.request;
        page.request = mods::request_none;
        return r;
    });
    m.def("mods_note", [] { return bytes_or_none(run.note); });
    // after mods_saving (#197: from the home menu)
    m.def("mods_progress_title", [](CStr title) { run.title = kept(progress_title, title); });
    m.def("mods_refusal", [] { return text_or_empty(mods::build_refusal(page)); });
    m.def("mods_save_refusal", [] { return text_or_empty(mods::save_refusal(page)); });
    m.def("mods_changed", [] { return unsigned(mods::changed(page)); });
    // the toggles as the saved selection (xyf_mods_io read_record)
    m.def("mods_mark_saved", [] {
        for (unsigned i = 0; i != page.count; ++i)
            page.packages[i].flags = (page.packages[i].flags & ~mods::flag_saved) |
                                     (page.packages[i].flags & mods::flag_on ? mods::flag_saved : 0);
    });
    // the save's progress
    m.def("mods_saving", [](unsigned build, unsigned stage, unsigned done, unsigned total) {
        if (!progress_shown) run.bar = run.bar_step = run.bar_since = 0;
        if (!progress_shown) run.title = "Saving changes";
        progress_shown = true;
        run.job = 0x10001;
        run.note[0] = 0;
        run.save = mods::save_on | (build ? mods::save_build : 0);
        run.save_stage = stage;
        if (stage) {
            run.job_done = done;
            run.job_total = total;
            run.job_commit = 0;
        }
    });
    m.def("mods_text", [] { return bytes_or_none(page.text); });
    m.def("mods_line", [] { return unsigned(page.line); });
    m.def("mods_row_id",
          [](unsigned row) { return bytes_or_none(mods::text_at(page, page.packages[page.rows[row]].id)); });
    // the display: the filter, the rows it lists (id, kind, name), every row in display order.
    m.def("mods_filter", [] { return unsigned(page.filter); });
    m.def("mods_all_on", [] { return unsigned(mods::all_on(page)); });
    m.def("mods_listed", [] { return unsigned(page.listed); });
    m.def("mods_list_id",
          [](unsigned row) { return bytes_or_none(mods::text_at(page, page.packages[page.list[row]].id)); });
    m.def("mods_list_kind", [](unsigned row) { return unsigned(page.packages[page.list[row]].kind); });
    m.def("mods_list_on", [](unsigned row) { return unsigned(page.packages[page.list[row]].flags & mods::flag_on); });
    m.def("mods_sorted_name",
          [](unsigned row) { return bytes_or_none(mods::text_at(page, page.packages[page.sorted[row]].name)); });
    m.def("mods_sorted_kind", [](unsigned row) { return unsigned(page.packages[page.sorted[row]].kind); });
    m.def("mods_sorted_id",
          [](unsigned row) { return bytes_or_none(mods::text_at(page, page.packages[page.sorted[row]].id)); });
    // mods_payload(base, unverified, capacity) -> mods.apply's request bytes (b'': it does not fit)
    m.def("mods_payload", [](CStr base, unsigned unverified, unsigned capacity) {
        char name[16];
        std::vector<unsigned char> out(capacity ? capacity : 1);
        const unsigned n = mods::request(page, base, unverified != 0, page.expanded, mods::flag_on, name, sizeof(name),
                                         out.data(), capacity);
        return nb::bytes(reinterpret_cast<const char*>(out.data()), n);
    });
    m.def("mods_job", [](unsigned job, unsigned phase, unsigned done, unsigned total, CStr name, unsigned cancelling,
                         unsigned commit) {
        if (!progress_shown) run.note[0] = 0;               // as xyf_mods_io start
        if (!progress_shown || run.job != job) run.bar = run.bar_step = run.bar_since = 0;
        progress_shown = true;
        run.job = job;
        run.job_phase = phase;
        run.job_done = done;
        run.job_total = total;
        run.job_commit = commit;
        std::strncpy(run.job_name, name, sizeof(run.job_name) - 1);
        run.cancel = cancelling ? 1 : 0;
    });
    m.def("mods_refuse_cancel", [] { mods::refuse_cancel(run); });
    m.def("mods_ended", [](unsigned phase, unsigned error, unsigned status, unsigned debris) {
        progress_shown = false;
        run.cancel = 0;
        mods::job_ended(page, run.job_name, phase, error, status, debris);
    });
    m.def("mods_refused", [](unsigned status, unsigned missing) { mods::start_refused(page, status, missing); });
    m.def("mods_compare", [](CStr a, CStr b) { return int(mods::compare_generation(a, b)); });
    // A full repaint (as after any other screen), or an incremental update of what the framebuffer holds.
    m.def("mods_update", [](nb::handle fb) { mods_update(Frame(fb)); });
    m.def("mods_draw", [](nb::handle fb) {
        frame.drawn = 0;
        mods_update(Frame(fb));
    });
    m.def("mods_progress", [](unsigned ms) { return unsigned(mods::progress(run, ms)); });
    m.def("mods_set_bar", [](unsigned bar) { run.bar = bar; });
    m.def("mods_stage_share", [](unsigned i) { return unsigned(mods::stage_share(i)); });
    m.def("mods_stage_ms", [](unsigned i) { return unsigned(mods::stage_ms(i)); });
}

}  // namespace

void bind_ui(nb::module_ m) {
    bind_screens(m);
    bind_flow(m);
    bind_mods(m);
}

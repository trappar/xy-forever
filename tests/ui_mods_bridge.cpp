// Host bridge for the Mods page: the device's page model and view (target/xyf_mods*.cpp)
// fed with package texts by the tests, rendered for previews. Not linked into the device.
#include "../target/xyf_mods_view.hpp"
#include <string.h>
using namespace xyf;
static mods::Page page;
static mods::Frame frame;
extern xyf::ui::Chrome bridge_chrome;                 // ui_preview_bridge.cpp
extern "C" {
void mods_reset() { memset(&page, 0, sizeof(page)); mods::reset(page); }
int mods_base(const char* text, unsigned bytes) { return mods::parse_base(page, text, bytes); }
int mods_services(const char* text, unsigned bytes) { return mods::parse_services(page, text, bytes); }
int mods_add(const char* folder) { return mods::add_package(page, folder); }
int mods_package(unsigned index, const char* text, unsigned bytes) { return mods::parse_package(page, index, text, bytes); }
// The folder names (xyf_mods_io keeps these: mods.apply takes them) and the list view.
void mods_ready(const char* profile, const char* generation) {
    strncpy(page.profile, profile, sizeof(page.profile) - 1);
    strncpy(page.generation, generation, sizeof(page.generation) - 1);
    page.view = mods::View::list;
    page.line = mods::line_none;
}
void mods_reading(unsigned done) {
    char a[12];
    mods::decimal(a, done);
    page.view = mods::View::reading;
    mods::say(page, mods::line_info, "Reading mod packages: ", a, "...");
}
unsigned mods_key(unsigned key, unsigned pressed) { return mods::key(page, key, pressed != 0); }
unsigned mods_used() { return mods::used(page); }
unsigned mods_limit() { return page.limit; }
// Mod memory: the 4 MiB layout's region, the choice (pending, saved), the limit in use, the shared part,
// the Settings row and its PLAY.
unsigned mods_expanded_limit() { return page.expanded_limit; }
void mods_set_memory(unsigned expanded, unsigned saved) { page.expanded = expanded != 0; page.expanded_saved = saved != 0; }
unsigned mods_expanded() { return page.expanded; }
unsigned mods_memory_limit() { return mods::memory_limit(page); }
unsigned mods_shared() { return mods::shared(page); }
unsigned mods_memory_row(unsigned read) { return mods::memory_row(page, read != 0); }
const char* mods_flip_memory(unsigned read) { const char* r = mods::flip_memory(page, read != 0); return r ? r : ""; }
unsigned mods_shown() { return page.shown; }
unsigned mods_count() { return page.count; }
unsigned mods_position() { return mods::position(page); }
unsigned mods_request() { const unsigned r = page.request; page.request = mods::request_none; return r; }
const char* mods_refusal() { const char* r = mods::build_refusal(page); return r ? r : ""; }
const char* mods_save_refusal() { const char* r = mods::save_refusal(page); return r ? r : ""; }
unsigned mods_changed() { return mods::changed(page); }
void mods_mark_saved() {                        // the toggles as the saved selection (xyf_mods_io read_record)
    for (unsigned i = 0; i != page.count; ++i)
        page.packages[i].flags = (page.packages[i].flags & ~mods::flag_saved) | (page.packages[i].flags & mods::flag_on ? mods::flag_saved : 0);
}
void mods_saving(unsigned build, unsigned stage, unsigned done, unsigned total) {   // the save's progress
    if (page.view != mods::View::building) page.bar = page.bar_step = page.bar_since = 0;
    page.view = mods::View::building; page.job = 0x10001; page.line = mods::line_none;
    page.save = mods::save_on | (build ? mods::save_build : 0); page.save_stage = stage;
    if (stage) { page.job_done = done; page.job_total = total; page.job_commit = 0; }
}
const char* mods_text() { return page.text; }
unsigned mods_line() { return page.line; }
const char* mods_row_id(unsigned row) { return mods::text_at(page, page.packages[page.rows[row]].id); }
// the display: the filter, the rows it lists (id, kind, name), every row in display order.
unsigned mods_filter() { return page.filter; }
unsigned mods_all_on() { return mods::all_on(page); }
unsigned mods_listed() { return page.listed; }
const char* mods_list_id(unsigned row) { return mods::text_at(page, page.packages[page.list[row]].id); }
unsigned mods_list_kind(unsigned row) { return page.packages[page.list[row]].kind; }
unsigned mods_list_on(unsigned row) { return page.packages[page.list[row]].flags & mods::flag_on; }
const char* mods_sorted_name(unsigned row) { return mods::text_at(page, page.packages[page.sorted[row]].name); }
unsigned mods_sorted_kind(unsigned row) { return page.packages[page.sorted[row]].kind; }
const char* mods_sorted_id(unsigned row) { return mods::text_at(page, page.packages[page.sorted[row]].id); }
unsigned mods_payload(const char* base, unsigned unverified, unsigned char* out, unsigned capacity) {
    char name[16];
    return mods::request(page, base, unverified != 0, name, sizeof(name), out, capacity);
}
void mods_job(unsigned job, unsigned phase, unsigned done, unsigned total, const char* name, unsigned cancelling,
              unsigned commit) {
    if (page.view != mods::View::building) page.line = mods::line_none;   // as xyf_mods_io start
    if (page.view != mods::View::building || page.job != job) page.bar = page.bar_step = page.bar_since = 0;
    page.view = mods::View::building;
    page.job = job; page.job_phase = phase; page.job_done = done; page.job_total = total; page.job_commit = commit;
    strncpy(page.job_name, name, sizeof(page.job_name) - 1);
    page.request = cancelling ? mods::request_cancel : mods::request_none;
}
void mods_refuse_cancel() { mods::refuse_cancel(page); }
void mods_ended(unsigned phase, unsigned error, unsigned status, unsigned debris) {
    page.view = mods::View::list;
    page.request = mods::request_none;
    mods::job_ended(page, phase, error, status, debris);
}
void mods_refused(unsigned status, unsigned missing) { mods::start_refused(page, status, missing); }
int mods_compare(const char* a, const char* b) { return mods::compare_generation(a, b); }
// A full repaint (as after any other screen), or an incremental update of what the framebuffer holds.
void mods_draw(uint16_t* fb) { frame.drawn = 0; mods::draw(fb, page, &bridge_chrome, frame); }
void mods_update(uint16_t* fb) { mods::draw(fb, page, &bridge_chrome, frame); }
unsigned mods_progress(unsigned ms) { return mods::progress(page, ms); }
void mods_set_bar(unsigned bar) { page.bar = bar; }
unsigned mods_stage_share(unsigned i) { return mods::stage_share(i); }
unsigned mods_stage_ms(unsigned i) { return mods::stage_ms(i); }
}

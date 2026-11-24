#ifndef XYF_MODS_HPP
#define XYF_MODS_HPP
#include <stdint.h>
// The Mods page (processor B's menu): the machines and extensions of the newest complete
// mod package generation on the device (docs/storage/mods.md, "Mod packages"), an on/off toggle each, the
// memory the choice needs against the mods region, and a status row saying what Continue will do with
// the toggles: they are a pending copy of the saved configuration (xyf_config.hpp), saved and built when you
// boot (xyf_mods_io's converge sequence, #197, shown on the progress screen: Progress, draw_progress).
// This part is pure (host-tested, previewed): the page model, its keys, the package texts it reads,
// the memory estimate, the job request and the result line. Reading the packages and running the job
// is xyf_mods_io (processor B only); drawing is xyf_mods_view. No stock calls, no division, no BSS.
namespace xyf { namespace mods {
static const uint32_t max_packages = 104, pool_bytes = 3072, max_services = 16, name_chars = 22;
static const uint32_t max_machines = 64, max_extensions = 8;    // mods.apply's bounds (usb_service)
// Page::selected: no row (nothing listed). The list holds only packages; the status, filter and all rows of
// earlier versions left it (the status joined the memory line, the filter the title bar; SHIFT + keys act on them).
static const uint32_t no_row = 0xffffu;
static const uint32_t missing_ids_taken = 5;                    // start_refused: usb_service mods_ids_taken
enum Kind : uint8_t { kind_synth, kind_drum, kind_sample, kind_effect, kind_player, kind_extension, kind_other };
// Page::filter: filter_all, 1 + a Kind (that kind only), or filter_on (what was on when it was chosen).
static const uint32_t filter_all = 0, filter_on = uint32_t(kind_other) + 1;
enum Flag : uint8_t { flag_on = 1, flag_read = 2, flag_saved = 4, flag_listed = 8 };   // saved: on in the saved
                                                                // configuration; listed: the filter shows it
struct Package {
    uint16_t id, name;              // pool offsets: folder name (an extension's id without "ext-"), info name=
    uint8_t kind, flags;
    uint16_t order;                 // info order= (collection order)
    uint32_t services;              // bit i: the base's service i (info services=)
    uint32_t bytes;                 // code + data + state + 8 per object (alignment allowance)
};
enum class View : uint32_t { reading, list };
// What the I/O part should do next (set by keys, taken by xyf_mods_io).
enum Request : uint32_t { request_none = 0, request_build, request_build_unverified, request_cancel };
// The result line's kind.
enum Line : uint32_t { line_none = 0, line_info, line_error, line_done };
struct Page {
    View view;
    uint32_t count;                 // packages listed (some may not be read yet)
    uint32_t shown;                 // packages read and shown (rows)
    uint32_t selected;              // a package index (a listed one), or no_row
    uint32_t request;
    bool shift;
    char profile[32], generation[32];
    // limit: the mods region of the standard layout (base info module_bytes); expanded_limit, the 4 MiB
    // layout's (module_bytes + module_base - expanded_module_base; 0: the base offers none). memory_limit() is
    // the one in use.
    uint32_t limit, expanded_limit, base_bytes, service_count;
    uint32_t service_bytes[max_services];
    uint16_t service_names[max_services];
    Package packages[max_packages];
    uint8_t rows[max_packages];     // build order (requests, saves): machines by order, then extensions by order
    // The display: sorted = every shown package by kind (Kind order), then name (case-insensitive), then
    // folder; list = the rows the filter shows (listed of them), in that order.
    uint8_t sorted[max_packages], list[max_packages];
    uint32_t filter, listed;
    char pool[pool_bytes];
    uint32_t pool_used;
    Line line;
    char text[80];                  // the reading or result line
    // unknown_ok: SHIFT+STOP (was SHIFT+PLAY on the status row) allowed an unknown base;
    // saved_missing: saved mods this package set does not list (xyf_mods_io; the selection then differs).
    uint32_t saved_missing;
    bool unknown_ok;
    // Mod memory (the Settings page): expanded, the pending choice (the 4 MiB layout); expanded_saved, the
    // saved configuration's (config.txt memory=). Both outlive reset(): the choice is made before the list is read.
    bool expanded, expanded_saved;
};
// The progress screen (#197): the converge sequence's (xyf_mods_io), shown by key_ui from the home menu in place of
// any page (draw_progress). save = save_on | save_build (it builds; 0: a build alone, the previews' "Step n of 8"),
// save_stage: 0 the build, 1 installing content, 2 saving. The job: phase 0..10 as mod_job::Phase, done of total in
// that phase; job_commit: 1 while B commits the result into the library (phase finish, done of 3; it cannot be
// cancelled). The bar: 0..bar_full, never lower than before; the current step (stage << 16 | done) and when it
// began (ms since the sequence started). cancel: STOP asked the build to stop; note: why STOP did nothing.
struct Progress {
    uint32_t job, job_phase, job_done, job_total, job_commit;
    uint32_t bar, bar_step, bar_since;
    uint32_t save, save_stage, cancel;
    uint32_t reading;               // #183: the base's content read (job_done of job_total bytes): its own bar
    const char* title;              // the title bar ("Saving changes", "Booting without mods", ...)
    char job_name[16];
    char note[64];
};
static const uint32_t save_on = 1, save_build = 2;
// Empty page, reading (keeps nothing but the memory choice: expanded, expanded_saved).
void reset(Page& page);
// One key event. Returns 0 nothing, 1 redraw, 2 leave the page (STOP on the list).
// - / + move (wrapping), SHIFT + - / + the first / last row; PLAY (a knob push) toggles the selected row,
// SHIFT + PLAY turns every listed row on, or off when all are on; a knob turn (- / + with menu::key_turn, key_ui's
// turn_knobs) moves as - / +, with SHIFT held it steps the filter (one step per click, wrapping); STOP leaves,
// SHIFT + STOP allows (or refuses again) an unknown base for the next save.
enum Effect : uint32_t { effect_none = 0, effect_redraw, effect_leave };
Effect key(Page& page, uint32_t key, bool pressed);
// A key on the progress screen: only STOP acts (once), asking the build to stop, or saying why not
// (refuse_cancel). effect_redraw when it changed anything.
Effect progress_key(Progress& progress, uint32_t key, bool pressed);
// The selection's row in list (0 when nothing is listed).
uint32_t position(const Page& page);
// The filter. set_filter: list again (filter_on: what is on now; the selection stays when listed, else
// the first row); next_filter: the filter after f, back: before f (All, each kind with a package, On only,
// wrapping); kind_count: shown packages of a kind; all_on: every listed row is on (false when none is listed);
// on_count: listed rows on; filter_name: "All", "Synths", ... "On only".
void set_filter(Page& page, uint32_t filter);
uint32_t next_filter(const Page& page, uint32_t filter, bool back = false);
uint32_t on_count(const Page& page);
uint32_t kind_count(const Page& page, uint32_t kind);
bool all_on(const Page& page);
const char* filter_name(uint32_t filter);
// Pool helpers.
const char* text_at(const Page& page, uint16_t offset);
// A generation folder found: keeps the newest kit version (`<version>-<12 hex>`, dotted numbers
// compared as numbers). >0: a newer than b.
int compare_generation(const char* a, const char* b);
// A generation folder name mods.apply takes (1-31 of a-z 0-9 _ - ., a letter or digit first).
bool generation_token(const char* t);
// Package texts. The base's info.txt (module_bytes, code/data/state, runtime_*) and services.txt
// (`name users=… code= data= state=`): false when a needed field is missing.
bool parse_base(Page& page, const char* text, uint32_t length);
bool parse_services(Page& page, const char* text, uint32_t length);
// A package folder of the generation (not base): adds it unread; the index, or -1 (not a package
// name, or the page is full).
int add_package(Page& page, const char* folder);
// Its info.txt read (and its files.sha256 present): shown. False: malformed (stays hidden).
bool parse_package(Page& page, uint32_t index, const char* text, uint32_t length);
// The memory estimate for the current toggles: the base, runtime, every service a chosen package
// names and each chosen package's bytes. The linker's check (module_bytes) stays the authority.
// Shared, the services' part of it (a service is charged once, with the first mod that needs it);
// memory_limit, the region of the pending layout (limit, or expanded_limit when the choice is expanded).
uint32_t used(const Page& page);
uint32_t shared(const Page& page);
uint32_t memory_limit(const Page& page);
// Settings' Mod memory row (xyf_settings.hpp Memory): reading until the base is read (read: the reading is
// over), unavailable when the base offers no 4 MiB layout (unless the choice is expanded: it can go back), else the
// choice. flip_memory: PLAY on the row: the choice flipped (saved at once, built at boot), or why not (nothing changed).
uint32_t memory_row(const Page& page, bool read);
const char* flip_memory(Page& page, bool read);
uint32_t chosen(const Page& page, bool extensions);
// Why Build cannot start, or 0.
const char* build_refusal(const Page& page);
// The toggles differ from the saved configuration (flag_saved, saved_missing; or the memory choice).
bool changed(const Page& page);
// Why the toggles cannot be built (build_refusal when any mod is on), or 0. #197: they are saved all the same.
const char* save_refusal(const Page& page);
// mods.apply's request without its SHA-256: base NUL profile NUL generation NUL machines NUL
// extensions NUL name NUL u32 flags (bit 0 unverified, bit 1 expanded). The packages whose flags have which:
// flag_on (the toggles) or flag_saved (the saved selection, built). Bytes, 0 when it does not fit. The name
// is "Mods: <count>".
uint32_t request(const Page& page, const char* base, bool unverified, bool expanded, uint8_t which, char* name,
                 uint32_t name_capacity, uint8_t* out, uint32_t capacity);
// The result line of a start refusal (mods.apply status and missing input, or missing_ids_taken) or of an
// ended job (phase done 8, failed 9, cancelled 10; error and status as job.status; done: the job committed
// library entry name, its content not installed). out: page.text.
void start_refused(Page& page, uint32_t status, uint32_t missing);
// STOP in the library commit: not cancelled, said (progress.note, on the progress screen). Also while the
// sequence installs content or saves (stage 1, 2).
void refuse_cancel(Progress& progress);
void job_ended(Page& page, const char* name, uint32_t phase, uint32_t error, uint32_t status, uint32_t debris);
void say(Page& page, Line line, const char* a, const char* b = "", const char* c = "");
void reading_line(Page& page, uint32_t at);   // "Reading mod packages: <at> of <count>..."
// The building bar for the job fields above at ms (since the job started): stage i (0..7 the job's
// phases, 8 the library commit) fills its share of the bar by its own done of total, and within one step
// by the time it has taken against the stage's typical time (approaching, never reaching, the step's end),
// so the bar moves on during a long step. Only ever moves forward. Returns progress.bar. In a save (progress.save)
// the build fills the first save_build_part of the bar (none without save_build), installing content
// (done of total files) up to save_content_end, saving the rest.
static const uint32_t save_build_part = 800000, save_content_end = 970000, save_content_only = 900000;
static const uint32_t bar_full = 1000000, bar_stages = 9;
uint32_t progress(Progress& progress, uint32_t ms);
// Stage i's share of the bar (of bar_full) and its typical time on the device (ms): the one table.
uint32_t stage_share(uint32_t stage);
uint32_t stage_ms(uint32_t stage);
// Decimal text of v at out (no division); returns the digits written.
uint32_t decimal(char* out, uint32_t v);
const char* kind_name(uint32_t kind);
const char* phase_text(uint32_t phase);
}}
#endif

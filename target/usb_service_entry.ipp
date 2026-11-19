#if defined(XYF_SIM)
uint32_t idle_gc_state() {
#if defined(XYF_USB_FILE_WRITE)
    return blob_collecting ? 1u | (slot().garbage.fd >= 0 ? 2u : 0u) | (slot().garbage.refs.count << 16) : 0u;
#else
    return 0;
#endif
}
#endif
// Part of usb_service.cpp, included there at this point: not compiled alone.
uint32_t presence() {
    // 1: the stock bootloader started USB (MSC). It does so only on its "wait
    // for battery" path (88a1dbae: 88a1c8fc -> 88a35044(0x40)), i.e. powered on
    // from the USB-powered off state (Tests 017 run 2 / 021 vs 017 run 1 / 023).
    // 2: not started, but the PSoC power status says USB power (stock 88a1c8fc:
    // [0x88a1cdb4] == 0x16): fast power cycle or software reset (Test 038:
    // REBOOT and a quick off/on both skipped the service).
    if (byte(0x88a35934u) == 1 && (word(0x88a3593cu) & 0x40u)) return 1;
    // 3: our own REBOOT left the marker (usb_service.hpp). The PSoC power status
    // is not used: it read 0x10 with and without USB.
    if (word(reboot_marker) == reboot_magic && word(reboot_marker + 4) == ~reboot_magic) return 3;
    return 0;
}

#if defined(XYF_USB_SET_DEFAULT)
void a_set_default(cold_storage::Stock121ReadOnlySession& storage, uint32_t hash) {
    // No library scan on the USB path (module budget): find the one firmware
    // folder whose name hashes to hash and has an os.ldr, read the two state
    // slots (and legacy state.txt), plan the next slot, write it.
    using namespace xyf;
    U32 status = state_write::status_not_mounted, seq = 0, written = 0;
    services::U32 step = 0;
    if (storage_server::ensure_mounted(storage) && storage.direct_ready() && !storage_server::held_files()) {
        char id[store::max_id + 1];
        for (U32 i = 0; i != sizeof(id); ++i) id[i] = 0;
        U32 matches = 0;
        const U32 dir = storage.direct_opendir("/yaffs2/xy-forever/firmware");
        if (dir) {
            for (U32 guard = 0; guard != 256; ++guard) {
                const uint8_t* entry = storage.direct_readdir(dir);
                if (!entry) break;
                char name[store::max_id + 2];
                U32 n = 0;
                for (; n != sizeof(name) - 1 && entry[library::dirent_name + n]; ++n)
                    name[n] = char(entry[library::dirent_name + n]);
                name[n] = 0;
                if (entry[library::dirent_type] == library::dirent_regular || !store::valid_id(name) ||
                    state_write::id_hash(name) != hash) continue;
                if (!matches++) for (U32 k = 0; k <= n; ++k) id[k] = name[k];
            }
            storage.direct_closedir(dir);
        }
        char path[96];
        bool present = false;
        U32 at = 0;
        const char* const parts[3] = {"/yaffs2/xy-forever/firmware/", id, "/os.ldr"};
        for (U32 k = 0; k != 3; ++k)
            for (const char* c = parts[k]; *c && at + 1 < sizeof(path); ++c) path[at++] = *c;
        path[at] = 0;
        if (matches == 1) {
            const int fd = storage.direct_open(path);
            present = fd >= 0 && storage.direct_close(fd) == 0;
        }
        if (!present) status = state_write::status_hash;
        else {
            state::Choice choice;
            for (U32 i = 0; i != sizeof(choice); ++i) reinterpret_cast<char*>(&choice)[i] = 0;
            static const char* const files[3] = {"/yaffs2/xy-forever/state-a.txt", "/yaffs2/xy-forever/state-b.txt",
                                                 "/yaffs2/xy-forever/state.txt"};
            static const state::Source sources[3] = {state::source_a, state::source_b, state::source_legacy};
            uint8_t* text = reinterpret_cast<uint8_t*>(state_write::a_scan);
            for (U32 k = 0; k != 3; ++k) {
                services::U32 length = 0;
                if (library::detail::read_text(storage, files[k], text, store::max_text, length))
                    state::consider(choice, sources[k], reinterpret_cast<const char*>(text), length);
            }
            state_write::Plan& plan = *reinterpret_cast<state_write::Plan*>(state_write::a_plan);
            if (!state_write::plan_default_id(choice, id, plan)) status = state_write::status_plan;
            else {
                seq = plan.seq;
                const cold_storage::Result w = storage.write_state_slot(plan.path,
                    reinterpret_cast<const uint8_t*>(plan.text), plan.length,
                    reinterpret_cast<uint8_t*>(state_write::a_readback), step);
                written = U32(w);
                status = w == cold_storage::Result::ok ? state_write::status_ok : state_write::status_storage;
            }
        }
    }
    result.a_state_write = status | (written & 0xffu) << 8 | (step & 0xffu) << 16 | (seq & 0xffu) << 24;
}
#endif

bool dry_run_requested() { return dry_run != 0; }
void a_dry_run() { dry_run = 1; }
void dry_run_finish(const void* block, uint32_t bytes) {
    if (result.role == 1) {
        // The session already ended; a link not shown closed halts b (never the OS).
        if (!b_link_closed(false)) { say("Internal link lost.", "Turn the OP-XY off and on."); for (;;) {} }
        menu_release();                                // USB off (already off after the boot's release)
        const U32 n = bytes < dry_run_max_bytes ? bytes : dry_run_max_bytes;
        const uint8_t* from = static_cast<const uint8_t*>(block);
        for (U32 i = 0; i != n; ++i) byte(dry_run_area + 8 + i) = from[i];
        word(dry_run_area + 4) = n; word(dry_run_area) = dry_run_magic;
        lines(dry_run_area, 8 + n, false);
        marker_write(marker_reboot | carried_flags, 0);
    }
    reset_self();
}

// Test 058: the XY Forever menu keeps USB (key_ui opens the menu without PLAY).
bool menu_requested() { return menu_usb == 1u || menu_usb == 2u; }   // 3: USB kept, no menu asked
#if defined(XYF_SERVICE_ON_MARKER_ONLY)
// Test 060: PLAY at power-on: the menu starts USB itself so the host can attach.
volatile U32 usb_off __attribute__((section(".data"))) = 0;   // 097: why the menu has no USB (0 = none)
#if defined(XYF_MTP_USB)
volatile U32 usb_deferred __attribute__((section(".data"))) = 0;   // menu_usb 2, USB not started yet
#endif
void menu_start_usb() {
    if (menu_usb == 3u) { menu_usb = 1; return; }      // Test 062: USB kept from the service
    if (menu_usb) return;
    usb_off = identity() ? 1u : file_client::identity() ? 2u : 0u;
    if (usb_off) return;
    P::reset(parser, rx);
    result.stock_callback = cdc_callback();
#if defined(XYF_MTP_USB)
    usb_deferred = 1;                                  // home_pump's first pass reads the flag, then starts USB
#else
    set_cdc_callback(0);
    usb_start();
#endif
    menu_usb = 2;                                      // 2: started by the menu (1: kept from the service)
}
const char* usb_notice() {
    return usb_off == 1u ? "USB off: stock USB code changed (1)." :
           usb_off == 2u ? "USB off: stock storage code changed (2)." : 0;
}
#endif
// Test 062: file-boot progress on the service screen (only after a HELLO this boot).
void progress(const char* a, const char* b) { say(a, b); }
// ---- Test 065: one link session; the menu is home and answers every USB command ----
void rpc_reset() { read_enabled = 0; }               // a left RPC mode (a transfer item was sent)
void stack_watch(uint32_t painted_begin) { stack_paint_begin = painted_begin; }
bool home_active() { return home != 0; }
bool home_usb() { return menu_usb != 0; }
// The menu's USB mark and activity line (usb_service.hpp).
volatile U32 host_framed __attribute__((section(".data"))) = 0;          // a valid frame arrived this boot
volatile U32 host_frame_ms __attribute__((section(".data"))) = 0;        // now() at the last one
volatile U32 host_change __attribute__((section(".data"))) = 0;          // the last changing request's line
volatile U32 host_change_ms __attribute__((section(".data"))) = 0;
enum HostLine : U32 { line_none = 0, line_stage, line_files, line_install, line_uninstall, line_default, line_build,
                      line_content };
const char* host_line(U32 line) {
    return line == line_stage ? "USB: receiving a file" : line == line_files ? "USB: writing files" :
           line == line_install ? "USB: installing XY Forever" : line == line_uninstall ? "USB: uninstalling XY Forever" :
           line == line_default ? "USB: setting the default" : line == line_build ? "USB: building Open-XY" :
           line == line_content ? "USB: changing content" : nullptr;
}
U32 change_line(uint8_t type) {                                          // the requests that change something
    if (type == P::t_put_begin || type == P::t_put_data || type == P::t_put_end) return line_stage;
    if (type == P::t_file_write || type == P::t_delete_file || type == P::t_remove_dir || type == P::t_make_dir ||
        type == P::t_rename_file || type == P::t_copy_file || type == P::t_write_begin || type == P::t_write_data ||
        type == P::t_write_end) return line_files;
    if (type == P::t_install) return line_install;
    if (type == P::t_uninstall) return line_uninstall;
    if (type == P::t_set_default) return line_default;
    if (type == P::t_mods_apply) return line_build;
    return line_none;
}

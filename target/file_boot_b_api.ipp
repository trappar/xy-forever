// Part of file_boot.cpp: B's xyf_boot functions (xyf_boot.hpp: the link hooks for key_ui, the file client and
// the USB service; the menu's status and choices), included in place.
namespace xy_forever { namespace xyf_boot {
StorageStart storage_start __attribute__((section(".data"))) = {start_magic, 2, 0, 0, 0, 0, 0, 0};   // 192: version 2 (repair)
#if defined(XYF_ONE_SESSION)
// 191: a link user (a USB file command, a menu command) while the catalog request is out: its end
// first (A's answer is normally milliseconds away), at most fetch_wait_ms. True when none is out.
bool fetch_wait() {
    Stock121PeerSession* session = b_session;
    const key_ui::Where where(xyf::where::catalog_first);
    const U32 start = now(), phase = storage_start.phase;
    while (session && b_fetching && !b_lost && now() - start < fetch_wait_ms) {
        if (b_fetch_step(*session) && !b_lost && (storage_start.phase != 3 || phase == 1)) key_ui::refresh_catalog();
        where.wait();
    }
    return !b_fetching;
}
#endif
bool tick() {
    Stock121PeerSession* session = b_session;
    if (!session || b_lost) return false;
#if defined(XYF_ONE_SESSION)
    // 191: A's first answer (its mount) or a retry after a failed mount, without blocking the menu.
    if (b_fetching) {
        const U32 phase = storage_start.phase;
        if (!b_fetch_step(*session)) return false;
        if (!b_lost && (storage_start.phase != 3 || phase == 1)) key_ui::refresh_catalog();
        return true;
    }
    if (b_failed_at && now() - b_failed_at >= retry_ms && !session->busy()) {
        b_fetch_begin(*session);
        return false;
    }
#endif
    const U32 before = b_lost;
    if (b_step(*session)) {
        const U32 t = now();
        if (session->busy()) {
#if defined(XYF_ONE_SESSION)
            // A file request left at link_sent(); it may stay out up to lost_ms.
            if (t - b_last_send > (session->filing() ? xyf_boot::lost_ms :
                                   b_patience_ms ? b_patience_ms : 2 * answer_ms)) b_lost = 1;
#else
            if (t - b_last_send > 2 * answer_ms) b_lost = 1;
#endif
        } else if (t - b_last_send >= keepalive_ms) {
            if (session->command(keepalive_tag << 16 | (++b_sequence & 0xffffu)) == SessionResult::waiting) {
                b_last_send = t;
#if defined(XYF_ONE_SESSION)
                ++b_keepalives;
#endif
            }
        }
    }
    return b_lost != before;
}
#if defined(XYF_ONE_SESSION)
bool link_value(uint32_t value) {
    Stock121PeerSession* session = b_session;
    return session && !b_lost && fetch_wait() && b_ask(*session, value, b_patience_ms ? b_patience_ms : xyf_boot::command_ms);
}
bool link_up() { return b_session && !b_lost; }
Stock121PeerSession* link_claim() {
    Stock121PeerSession* session = b_session;
    return session && !b_lost && fetch_wait() && b_settle(*session) ? session : 0;   // 191: after a catalog
}
bool link_step() {
    Stock121PeerSession* session = b_session;
    return session && b_step(*session);
}
void link_sent() { b_last_send = now(); }
void link_answer(uint32_t& value, uint32_t& error) { value = b_answer[0]; error = b_answer[1]; }
// before any stock reset of b. A request still open is polled until
// answered or close_ms passes (wait: false: the request is not waited for);
// then the session ends: the stock master close 889f562e turns SPI and both DMA channels off and
// removes the link interrupts, so nothing can complete the request later (its object is the
// session's own, which outlives this: the session is the menu's caller's). The link is lost from
// here either way. True only when a fresh snapshot shows the hardware off (check_quiet).
bool link_close(bool wait) {
    Stock121PeerSession* session = b_session;
    if (session) {
        const key_ui::Where where(xyf::where::link_close);
        const U32 start = now();
        while (wait && session->busy() && now() - start < close_ms) {
            b_poll(*session);                           // a file request's answer kept (#144)
            where.wait();
        }
        b_lost = 1;
        result.end_result = U32(session->end());
    }
    peer_services::Snapshot s;
    return Stock121PeerSession::capture(s) && peer_services::check_quiet(s) == peer_services::Result::ok;
}
void link_patience(uint32_t ms) { b_patience_ms = ms; }
bool link_idle() { return b_session && !b_lost && !b_fetching && !b_session->busy(); }
uint32_t link_keepalives() { return b_keepalives; }
bool refresh_catalog() {
    if (b_fetching) return false;                       // 191: one is on its way (tick)
    b_catalog_ok = 0;
    return b_session && !b_lost && b_fetch_catalog(*b_session);
}
#endif
#if defined(XYF_ONE_SESSION)
// 193: the menu's view of A's storage (xyf_menu StorageView). A rescan after a write (answered before)
// keeps the library the menu shows; the first mount's first 400 ms stay blank (a normal mount answers in
// about 250 ms); past 2 s it is the full scan after an unclean shutdown (normal: 0.25 s, scan 4.5-17 s).
uint32_t storage_view() {
    namespace m = xyf::menu;
    const StorageStart& st = storage_start;
    if (b_lost || st.phase == 4) return m::storage_unavailable;
    if (st.phase == 3) {                                   // A's mount failed: B asks again, or repairs
        const uint32_t repair = storage_repair(st.a_word);
        return repair == repair_failed ? m::storage_unavailable : repair == repair_pending ? m::storage_repairing :
                                         m::storage_starting;
    }
    if (b_catalog_ok || st.answered_ms) return m::storage_ready;
    const U32 waited = b_fetching ? now() - b_fetch_at : 0u;
    return waited < 400u ? m::storage_waiting : waited < 2000u ? m::storage_starting : m::storage_checking;
}
#endif
const char* status() {
#if defined(XYF_USB_SERVICE) && defined(XYF_ONE_SESSION)
    if (const char* off = usb_service::usb_notice()) return off;    // 097: the menu came up without USB
#endif
#if defined(XYF_MENU_ON_FAILURE)
    if (!b_lost && b_notice[0]) return b_notice;      // Test 059: why the menu opened
#endif
#if defined(XYF_ONE_SESSION)
    if (b_lost) return "Internal link lost. Turn the OP-XY off and on.";   // the menu (USB) stays
    // 193: the first row says "Starting storage..." (xyf_menu); this line says why it takes long.
    switch (storage_view()) {
    case xyf::menu::storage_checking: return "Checking storage after an unclean shutdown";   // 191: a full scan
    case xyf::menu::storage_repairing: return "Repairing storage after an unclean shutdown";   // 192
    case xyf::menu::storage_starting: return storage_start.phase == 3 ? "Storage did not mount: trying again" : "";
    case xyf::menu::storage_unavailable: return "The firmware library can't be read, so nothing can boot.";
    default: return "";
    }
#endif
    return b_lost ? "Storage link lost: starting the built-in OP-XY." :   // nothing when storage is OK
           b_catalog_ok ? "" : "Storage link: OK (library unavailable)";
}
const char* command(uint32_t) { return "Not available yet."; }
bool link_lost() { return b_lost != 0; }
const xyf::catalog::Catalog* catalog() { return b_catalog_ok ? &b_catalog() : 0; }
void choose_default() { b_choice = 1; }
#if defined(XYF_ONE_SESSION)
uint32_t clock_ms() { return now(); }
#endif
void choose(uint32_t entry) { b_choice = 2; b_entry = entry; }
#if defined(XYF_MENU_ON_FAILURE)
bool menu_forced() { return b_failed != 0 || b_power_on != 0; }
#else
bool menu_forced() { return false; }
#endif
#if defined(XYF_STATE_WRITE)
const char* set_default(uint32_t entry) {
    Stock121PeerSession* session = b_session;
    if (!session || b_lost) return "Storage link lost.";
    if (!b_catalog_ok || entry >= b_catalog().count) return "Set default: no such entry.";
    if (!b_send(*session, xyf::state_write::item_set_default + entry)) return "Storage link lost.";
    // a writes, remounts and rescans before it answers this catalog command.
    if (!b_send(*session, xyf_boot::item_catalog, xyf_boot::write_ms) ||
        session->transfer(reinterpret_cast<Byte*>(xyf_boot::catalog_buffer), xyf_boot::catalog_capacity, true) !=
            SessionResult::waiting) { b_lost = 1; return "Storage link lost."; }
    const U32 start = now();
    while (!session->blob_done() && now() - start < xyf_boot::write_ms && b_step(*session)) {}
    if (!session->blob_done()) { b_lost = 1; return "Storage link lost."; }
    b_last_send = now();
    b_catalog_ok = xyf::catalog::decode(reinterpret_cast<const Byte*>(xyf_boot::catalog_buffer),
                                        session->image_size(), b_catalog());
    result.menu.catalog_entries = b_catalog_ok ? b_catalog().count : 0;
    if (!b_catalog_ok) return "Library unavailable after the write.";
    return b_catalog().default_index == entry ? "Default saved." : "Could not save the default.";
}
#endif
#if defined(XYF_STAGE2)
uint32_t stage2_entry() { return b_lost ? 0 : b_stage2_entry; }
void stage2_done(uint32_t exit, uint32_t run_ms) { result.stage2.exit = exit; result.stage2.run_ms = run_ms; }
#if defined(XYF_STAGE2_MENU)
const uint8_t* catalog_blob(uint32_t& bytes) {
    bytes = b_catalog_ok ? result.menu.catalog_bytes : 0;
    return bytes ? reinterpret_cast<const uint8_t*>(catalog_buffer) : 0;
}
#endif
#endif
} }

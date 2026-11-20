// Part of usb_service.cpp: #65 Confirm on device (xyf_settings.hpp Gate). With the setting on, a host request that
// changes which code runs next (confirm_asks: boot.install, boot.uninstall, library.set_default, mods.apply) is held
// here, the screen asks (key_ui draws confirm_prompt), and the menu loop's keys answer it: a real PLAY runs the
// request as it came, a real STOP, or no answer within timeout_ms, answers declined with nothing done. Injected keys
// (diag.key) never answer (confirm_key). While it asks, every other request but hello, diag.status, screen.read,
// diag.peek, diag.key and job.status answers busy (one request at a time: a host sends none then). The setting is
// what settings.txt holds (stored_confirm; on until the record is read), never B's unsaved pick on the page.
#if defined(XYF_SETTINGS)                                              // xs: usb_service_settings.ipp
static const U32 confirm_max = 1460;                                     // mods.apply's largest request
U32 confirm_type __attribute__((section(".data"))) = 0;                  // the held request's type (0: none)
U32 confirm_seq __attribute__((section(".data"))) = 0;
U32 confirm_length __attribute__((section(".data"))) = 0;
uint8_t confirm_payload[confirm_max] __attribute__((section(".data"))) = {};
bool confirm_asks(U32 type) {
    return type == P::t_install || type == P::t_uninstall || type == P::t_set_default || type == P::t_mods_apply;
}
const char* confirm_prompt() {
    if (!confirm_type || confirm_gate.state != xs::pending) return 0;
    return confirm_type == P::t_install ? "Install a new XY Forever build" :
           confirm_type == P::t_uninstall ? "Uninstall XY Forever (put the stock os.ldr back)" :
           confirm_type == P::t_set_default ? "Set the default firmware" : "Build mod firmware";
}
const char* confirm_detail() {                                           // set_default's id, when it is one
    const char* id = reinterpret_cast<const char*>(confirm_payload);
    const bool named = confirm_type == P::t_set_default && confirm_length >= 2 && confirm_length <= xyf::store::max_id + 1 &&
                       !confirm_payload[confirm_length - 1] && xyf::store::valid_id(id);
    return named ? id : "";
}
uint32_t confirm_on() { settings_load(); return stored_confirm; }
// Another request while the device asks: busy (true), or answered as usual.
bool confirm_busy(U32 type) {
    return confirm_type && type != P::t_status && type != P::t_screen && type != P::t_peek && type != P::t_key &&
           type != P::t_job_status;
}
// A request that asks first: held and the prompt opened (true), or false to run it now (the setting off, another
// type, or too long to hold: the command refuses that itself).
bool confirm_hold(U32 type, uint8_t seq, const uint8_t* payload, U32 length) {
    if (!confirm_asks(type) || length > confirm_max) return false;
    if (!confirm_on()) return false;                                     // once A serves (else: the default, on)
    for (U32 i = 0; i != length; ++i) confirm_payload[i] = payload[i];
    confirm_type = type; confirm_seq = seq; confirm_length = length;
    xs::gate_open(confirm_gate, "", now());
#if defined(XYF_SIM) && defined(XYF_MTP_SESSION)                       // the screen's prompt, in the simulator's log
    ::xyf_sim::log("confirm: asks: %s; %s", confirm_prompt(), confirm_detail());
#endif
    return true;
}
// Each pass: the held request's answer. PLAY: it runs (busy if a job started meanwhile); STOP or the bound: declined
// with every reply field 0.
void confirm_turn() {
    if (!confirm_type || xs::gate_poll(confirm_gate, now()) == xs::pending) return;
    const U32 type = confirm_type, answer = confirm_gate.state;
    const uint8_t seq = uint8_t(confirm_seq);
    confirm_type = 0;
    xs::gate_clear(confirm_gate);
#if defined(XYF_SIM) && defined(XYF_MTP_SESSION)
    ::xyf_sim::log("confirm: answer %s", answer == xs::yes ? "yes" : answer == xs::no ? "no" : "timed out");
#endif
#if defined(XYF_SERVICE_SCREEN)
    key_ui::host_view();                                                 // the page again, before the request runs
#endif
    if (answer != xs::yes) {
        uint8_t p[16];
        P::put32(p, P::s_declined); P::put32(p + 4, 0); P::put32(p + 8, 0); P::put32(p + 12, 0);
        result.last_status = P::s_declined;
        send(uint8_t(type), seq, p, type == P::t_set_default ? 8 : type == P::t_mods_apply ? 12 : 16);
    }
    else if (job_blocks(type)) status_only(uint8_t(type), seq, P::s_busy);
    else if (type == P::t_install || type == P::t_uninstall) install(seq, confirm_payload, confirm_length, type == P::t_uninstall);
    else if (type == P::t_set_default) set_default(seq, confirm_payload, confirm_length);
    else mods_apply(seq, confirm_payload, confirm_length);
}
#endif

// Part of usb_service.cpp: #65 Ask before USB control (xyf_settings.hpp Gate). With the setting on (stored_confirm:
// what settings.txt holds, never B's unsaved pick on the page), the first serial request of a connection but hello
// opens the question "Allow this computer to control your OP-XY?" (key_ui draws it; trust_prompt) and is refused with
// waiting, as is every request while it asks: nothing is held, the host retries. A real PLAY trusts the computer, a
// real STOP refuses every request (declined); injected keys (diag.key) never answer (confirm_key). There is no time
// limit. The answer lasts until the cable is unplugged or the device restarts: closing and reopening the port keeps
// it. Unplugging is seen as a USB bus reset, which every new connection starts with (usb_connection). MTP stays open
// (#182), so the MTP tunnel (diag.mtp, the same session over serial) is never refused.
#if defined(XYF_SETTINGS)                                              // xs: usb_service_settings.ipp
enum Trust : U32 { trust_none = 0, trust_asking, trust_allowed, trust_refused };
U32 trust_state __attribute__((section(".data"))) = trust_none;
U32 trust_bus __attribute__((section(".data"))) = 0;                    // usb_connection() when trust_state was set
// The USB bus resets so far: on the composite (the product's default USB mode), our driver's count (mtp_usb.cpp
// on_event 4); the simulator models it (control "unplug"). The stock serial service (USB mode Serial, or the
// composite's fallback) tells us none: there an answer lasts until the device restarts.
U32 usb_connection() {
#if defined(XYF_MTP_USB)
    if (composite_on) return xyf::mtp_usb::stats().resets_bus;
#endif
#if defined(XYF_SIM) && defined(XYF_MTP_SESSION)                       // the simulator (sim/stock.cpp, log)
    return ::xyf_sim::usb_bus_resets();
#else
    return 0;
#endif
}
// The setting as settings.txt holds it; in the simulator Off unless it runs with --ask (tests answer no question).
uint32_t confirm_on() {
    settings_load();
#if defined(XYF_SIM) && defined(XYF_MTP_SESSION)
    if (!::xyf_sim::ask()) return 0;
#endif
    return stored_confirm;
}
bool trust_prompt() { return trust_state == trust_asking; }
// hello trust=: none (not asked on this connection), asking, allowed, refused.
const char* trust_word() {
    static const char* const words[] = {"none", "asking", "allowed", "refused"};
    return words[trust_state & 3u];
}
// A new connection (a bus reset since the answer): asked again. Each pass and before each request.
void trust_check() {
    const U32 bus = usb_connection();
    if (bus == trust_bus) return;
    trust_bus = bus;
    if (trust_state == trust_none) return;
#if defined(XYF_SIM) && defined(XYF_MTP_SESSION)
    ::xyf_sim::log("trust: a new connection: %s ends", trust_word());
#endif
    trust_state = trust_none;
    xs::gate_clear(confirm_gate);
}
// A request but hello: the status that refuses it (waiting, declined), or 0 to answer it. Until settings.txt is read
// the setting is the default, on (on the device USB starts once it is read, or after usb_settings_ms without it).
U32 trust_refuses(U32 type) {
#if defined(XYF_USB_FILE_WRITE) && defined(XYF_MTP_SESSION)
    if (type == t_mtp_tunnel) return 0;                                  // MTP, as on its own interface
#else
    (void)type;
#endif
    trust_check();
    if (!confirm_on() || trust_state == trust_allowed) return 0;
    if (trust_state == trust_refused) return P::s_declined;
    if (trust_state == trust_none) {
        trust_state = trust_asking;
        xs::gate_open(confirm_gate);
#if defined(XYF_SIM) && defined(XYF_MTP_SESSION)
        ::xyf_sim::log("trust: asks");
#endif
    }
    return P::s_waiting;
}
// Each pass: the question's answer (the keys came this pass).
void trust_turn() {
    trust_check();
    if (trust_state != trust_asking || confirm_gate.state == xs::pending) return;
    trust_state = confirm_gate.state == xs::yes ? trust_allowed : trust_refused;
    xs::gate_clear(confirm_gate);
#if defined(XYF_SIM) && defined(XYF_MTP_SESSION)
    ::xyf_sim::log("trust: answer %s", trust_word());
#endif
#if defined(XYF_SERVICE_SCREEN)
    key_ui::host_view();                                                 // the page again
#endif
}
#endif

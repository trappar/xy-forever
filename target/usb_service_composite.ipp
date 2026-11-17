// Part of usb_service.cpp, included there at this point: not compiled alone (decision 034).
#if defined(XYF_MTP_USB)
// ---- Serial + MTP as one composite device (record 031 b2) ------------------------------------------
// USB is started once per boot and never stopped or restarted inside it (serial and MTP both always on).
// 188: the product (no flag, no trial, no fallback restart: a device used without a computer never sees a
// HELLO): every boot whose boot-time USB check passed (menu_start_usb) starts the composite in the menu loop's
// first pass when the MTP span gate passes, else the stock serial service (no switch inside a boot). The
// trial below (the flag, the marks, the result file, the 20 s fallback, diag op 0) is the test mode's only
// (xyf-mtp-usb, XYF_MTP_USB_TRIAL).
// Trial with a cheap net: diag.mtp_usb (0x62, dev, test only, not in the published spec) op 0 writes the flag
// file and does the product's full restart (home_reboot(reboot_menu): both processors, record 022). Every boot
// of this build defers its USB start to the first pass of the menu loop (usb_begin: B reaches files only
// through A, which serves by then): one bounded check of A (command item), a stat of the flag; found: deleted
// FIRST (A's write entry, unlink), then ours registered and start(0x200). No flag, A silent, the delete
// refused, the span gate refused or the slot taken: start(0x400), the stock serial service, as every boot of
// the product. On the composite, no valid HELLO within composite_hello_ms of the start: the same full restart
// (fallback_restart), and the next boot (no flag) is on stock serial. A HELLO within it keeps the composite.
// The MTP interface (GetDeviceInfo, OpenSession, CloseSession, the test sink/source/loopback) is always
// available while the composite is up: every pass of the menu loop pumps it (composite_turn).
// Op 1: the composite's words, the driver's, the last sink's SHA-256, the gate's refusal record.
// Not bounded by us: the controller init inside the stock start (88a0bf86) waits 5 ticks of timer 7 (record
// 030); timer 7 is open at a boot's first start. The progress marks name a step that hangs.
namespace mu = xyf::mtp_usb;
const uint8_t t_mtp_usb = 0x62;
const U32 composite_hello_ms = 20000, mtp_seen_ms = 5000;
const U32 usb_driver_slot = 0x11803af0u;                                // record 002: the one function-driver slot
const char flag_path[] = "/yaffs2/xy-forever/tmp/composite.flag";
enum CompositeState : U32 { cs_none, cs_deferred, cs_stock, cs_composite, cs_fallback };
enum FlagCheck : U32 { flag_unchecked, flag_absent, flag_found, flag_a_silent };
const U32 probe_words = 14;
// state, flag, delete (status of the unlink), gate (0 passed), slot (driver slot after begin), started_ms,
// first_byte_ms, hello_ms, passes, max_gap_ms, last_ms, sinks, mark_first_ms, mark_total_ms
U32 probe[probe_words] __attribute__((section(".data"))) = {0};
enum : U32 { p_state, p_flag, p_delete, p_gate, p_slot, p_started, p_first_byte, p_hello, p_passes, p_max_gap,
             p_last, p_sinks, p_mark_first, p_mark_total };
U32 last_commands __attribute__((section(".data"))) = 0;
U32 mtp_ms __attribute__((section(".data"))) = 0;                     // now() at the last MTP command
uint8_t probe_digest[32] __attribute__((section(".data"))) = {0};
U32 probe_hash_storage[(sizeof(xyf_core::hash::Sha256) + 3) / 4] __attribute__((section(".data"))) = {0};
xyf_core::hash::Sha256& probe_hash() { return *reinterpret_cast<xyf_core::hash::Sha256*>(probe_hash_storage); }
U32 hash_open __attribute__((section(".data"))) = 0;                   // a sink's hash not finished yet
void probe_sink(const uint8_t* data, uint32_t n, bool first) {          // main loop (pump): the sink's data, in order
    if (first) { probe_hash().reset(); hash_open = 1; ++probe[p_sinks]; return; }
    probe_hash().update(data, n);
}
namespace gate = xyf::mtp_usb_gate;
// Why the last check answered s_identity (mtp_usb_gate.hpp; op 1 reports it after the digest).
uint32_t probe_refusal[gate::record_words] __attribute__((section(".data"))) = {0};
uint32_t crc_at(uint32_t address, uint32_t length) {                      // stock code on B
    return xyf::crc::update_volatile(0, reinterpret_cast<const volatile uint8_t*>(address), length);
}
// Not identity() again (the live serial service changes five words of usb_spans.hpp, #65, #86).
// Rule (record 029): the MTP start skips the second identity() (mtp_usb_gate.hpp). The boot-time identity() must have
// passed (menu_start_usb: usb_off 0, menu_usb 2). Then engine_identity()'s spans (mtp_spans.hpp), then the
// driver's own (mtp_usb_spans.hpp, TX start), all on B. In usb_begin: before USB starts.
gate::Check probe_spans() {
    const gate::Spans engine = {xyf::mtp_spans::table, xyf::mtp_spans::count};
    const gate::Spans spans = {xyf::mtp_usb_spans::table, xyf::mtp_usb_spans::count};
    return gate::refusal<crc_at>(menu_usb == 2u && !usb_off, engine, spans, probe_refusal);
}
// ---- progress marks that survive a power cycle (record 030 section 5) ----------------------------------------
// mu::mark() calls probe_mark before each step that could freeze. Each mark appends one line to marks_text
// ("NN MS NAME": step, B's clock in ms, name) and rewrites the whole file through A's file engine: open "wb",
// one write (<= 1 KiB, one chunk), close: three requests, each bounded by the file client (change_ms). With A's
// write window closed (writes_due 0) it first enters A's write entry, mounts and creates tmp/ (A opens its
// window: 0.31 s for a whole put-file with checkpoint [019 M]); then writes_due: the menu loop's normal settle
// closes the window (window_settle_ms after the last write). Marks run from the flag's delete to the first
// valid HELLO (or the fallback restart). A failed mark is not retried: mu::mark tries no further mark this boot.
const char marks_path[] = "/yaffs2/xy-forever/tmp/mtp-probe-marks.txt";
const char marks_folder[] = "/yaffs2/xy-forever/tmp";
const U32 marks_capacity = 1024;                                         // file_client::chunk: one write
char marks_text[marks_capacity] __attribute__((section(".data"))) = {0};
U32 marks_length __attribute__((section(".data"))) = 0;
U32 marks_append(const char* text) {
    U32 n = 0;
    while (text[n]) ++n;
    if (marks_length + n > marks_capacity) return 0;
    for (U32 i = 0; i != n; ++i) marks_text[marks_length + i] = text[i];
    return marks_length += n;
}
U32 writes_open() {                                                      // A's write window, open from here on
    namespace C = file_client;
    if (writes_due) { writes_ms = now(); return P::s_ok; }               // still open: the settle waits again
    U32 code = enter_writes();                                           // A's write entry (stale mount closed)
    if (code == P::s_ok) code = C::mount_ready(mounted);                 // op 8
    if (code != P::s_ok) return code;
    writes_due = 1; writes_ms = now(); storage_used = 1;                 // the normal settle closes it
    const C::Reply d = C::mkdir(marks_folder);                          // op 0x26; EEXIST is fine
    if (d.outcome) { if (d.outcome == C::link) mounted = C::mounted_stale; return C::status(d.outcome); }
    return P::s_ok;
}
U32 file_put(const char* path, const char* text, U32 length) {         // the whole file: open "wb", write, close
    namespace C = file_client;
    const U32 entry = writes_open();
    if (entry != P::s_ok) return entry;
    const C::Reply o = C::open(path, storage_server::write_open_flags);   // op 0x29 "wb"
    if (o.outcome) { if (o.outcome == C::link) mounted = C::mounted_stale; return C::status(o.outcome); }
    if (o.value < 0) return P::s_storage;
    const C::Reply w = C::write(o.value, reinterpret_cast<const uint8_t*>(text), length);   // op 0x2b
    if (w.outcome) { if (w.outcome == C::link) mounted = C::mounted_stale; return C::status(w.outcome); }
    const C::Reply c = C::close(o.value);                               // op 0x2d
    if (c.outcome) { if (c.outcome == C::link) mounted = C::mounted_stale; return C::status(c.outcome); }
    writes_ms = now();
    return w.value == int(length) && !c.value ? P::s_ok : P::s_storage;
}
U32 marks_write() { return file_put(marks_path, marks_text, marks_length); }
bool probe_mark(uint32_t step) {
    const U32 t0 = now();
    char line[24];
    line[0] = step >= 10 ? '1' : '0'; line[1] = char('0' + (step >= 10 ? step - 10 : step)); line[2] = ' ';   // < 20
    U32 n = 3 + decimal_text(line + 3, t0);
    line[n++] = ' '; line[n] = 0;
    if (!marks_append(line) || !marks_append(mu::mark_name(step)) || !marks_append("\n")) return false;
    const bool ok = marks_write() == P::s_ok;
    const U32 cost = now() - t0;
    if (step == mu::mark_flag_found) probe[p_mark_first] = cost;
    probe[p_mark_total] += cost;
    return ok;
}
void marks_begin() {
    marks_length = 0;
    marks_append("xy-forever mtp-usb composite marks, build " XYF_BUILD_NUMBER ": step ms name\n");
    mu::set_mark_hook(probe_mark);
}
void marks_end() { mu::set_mark_hook(0); }                              // writes_due: A's window closes by the settle
// The composite's result as text (everything op 1 reports), one line per part, words as 8 hex digits:
// "probe" probe_words words, "driver" the driver words, "sha256" the last sink's digest, "refusal" gate::record_words.
const char result_path[] = "/yaffs2/xy-forever/tmp/mtp-probe-result.txt";
const U32 stack_attached = 0x88a137a0u;      // record 031: 88a13714 sets it to 1 only when attach 88a08d52 returned 0
const U32 n_driver_words = 32;
void driver_words(U32* w) {
    const mu::Stats& st = mu::stats();
    const U32 driver[] = {st.passes, st.longest_pass_urbs, st.longest_pass_bytes, st.urbs_received, st.urbs_dropped,
                          st.in_sent, st.kicks, st.kick_rx, st.deconfigures, st.resets, st.cancels, st.resets_bus,
                          U32(st.sink_bytes), U32(st.source_bytes), st.sink_sum, st.device_infos, st.sessions_opened,
                          st.sessions_closed, U32(mu::state()), st.marks_written, st.marks_failed, st.mtp_commands,
                          st.serial_rx, st.serial_tx, st.serial_held, st.serial_busy, st.line_codings, st.control_lines,
                          st.handler_in, st.handler_out, st.handler_held, st.handler_urbs_in};
    static_assert(sizeof(driver) / sizeof(driver[0]) == n_driver_words, "op 1 layout");
    for (U32 i = 0; i != n_driver_words; ++i) w[i] = driver[i];
}
U32 text_put(char* t, U32 n, const char* s) { while (*s) t[n++] = *s++; return n; }
U32 text_hex(char* t, U32 n, U32 v, U32 digits) {
    for (U32 i = 0; i != digits; ++i) t[n++] = "0123456789abcdef"[(v >> (4 * (digits - 1 - i))) & 15u];
    return n;
}
U32 text_words(char* t, U32 n, const char* label, const U32* w, U32 count) {
    n = text_put(t, n, label);
    for (U32 i = 0; i != count; ++i) { t[n++] = ' '; n = text_hex(t, n, w[i], 8); }
    t[n++] = '\n';
    return n;
}
void sink_digest() {                                                     // after the sink's response (the host asks)
    xyf_core::hash::Digest d;
    if (hash_open && probe_hash().finish(d)) for (U32 i = 0; i != 32; ++i) probe_digest[i] = d.bytes[i];
    hash_open = 0;
}
U32 result_text(char* t) {                   // <= 1024 (one write): about 700 bytes
    sink_digest();
    U32 n = text_put(t, 0, "xy-forever mtp-usb composite result, build " XYF_BUILD_NUMBER "\n");
    n = text_words(t, n, "probe", probe, probe_words);
    U32 driver[n_driver_words];
    driver_words(driver);
    n = text_words(t, n, "driver", driver, n_driver_words);
    n = text_put(t, n, "sha256 ");
    for (U32 i = 0; i != 32; ++i) n = text_hex(t, n, probe_digest[i], 2);
    t[n++] = '\n';
    U32 refusal[gate::record_words];                                     // uint32_t is long on the target
    for (U32 i = 0; i != gate::record_words; ++i) refusal[i] = probe_refusal[i];
    return text_words(t, n, "refusal", refusal, gate::record_words);
}
void home_reboot(U32 target);
// The first pass of the menu loop (menu_start_usb deferred the start): the flag, then the boot's only start.
void usb_begin() {
    probe[p_state] = cs_stock;
#if defined(XYF_MTP_USB_TRIAL)
    namespace C = file_client;
    bool trial = false;
    if (!a_serving()) probe[p_flag] = flag_a_silent;                     // one bounded request: stock serial
    else {
        uint8_t record[C::record_bytes];
        const C::Reply s = C::stat(flag_path, record);                   // op 0x22
        probe[p_flag] = !s.outcome && s.value == 0 ? flag_found : flag_absent;
    }
    if (probe[p_flag] == flag_found) {
        // Deleted FIRST (before any mark, before USB): a freeze anywhere after leaves no flag behind.
        U32 code = writes_open();
        if (code == P::s_ok) {
            const C::Reply u = C::unlink(flag_path);                    // op 0x25
            if (u.outcome == C::link) mounted = C::mounted_stale;
            code = u.outcome ? C::status(u.outcome) : u.value == 0 ? P::s_ok : P::s_storage;
        }
        probe[p_delete] = code;
        marks_begin();
        mu::mark(mu::mark_flag_found);                                   // written after the delete (see above)
        if (code == P::s_ok) { mu::mark(mu::mark_flag_deleted); trial = true; }
    }
#else
    const bool trial = true;                                             // 188: the product: every boot
#endif
    if (trial) {
        probe[p_gate] = probe_spans();                                   // stock code as checked, before any start
        if (!probe[p_gate]) {
            result.stock_callback = cdc_callback();                      // unchanged (menu_release restores it)
            probe[p_started] = probe[p_last] = now();
            mu::set_sink_hook(probe_sink);
            session_enter();                                             // the responder on interface 2
            mu::set_handler(&mtp_handler);
            if (mu::begin(mu::stock_port())) {                           // register, start(0x200): marks 3-5 / 14
                probe[p_slot] = word(usb_driver_slot);
                mu::mark(word(stack_attached) == 1u ? mu::mark_attach_ok : mu::mark_attach_failed);
                composite_on = 1; probe[p_state] = cs_composite;
                return;
            }
            mu::set_sink_hook(0);
            mu::set_handler(0); mtp_on = 0;
        }
        marks_end();
    }
    set_cdc_callback(0);                                                 // as every boot of the product
    usb_start();
}
// No valid HELLO within composite_hello_ms: leave (stop(0), unregister, frees), the last mark, the result file,
// A's window closed (op 9, as before a REBOOT), then the REBOOT from home (A resets on the reboot item, B stops
// USB and resets 100 ms later: both processors, record 022; never B alone). The next boot: no flag, stock serial.
#if defined(XYF_MTP_USB_TRIAL)
void fallback_restart() {
    probe[p_state] = cs_fallback;
    mu::mark(mu::mark_fallback_restart);
    mu::leave();
    composite_on = 0;
    probe[p_slot] = word(usb_driver_slot);                               // 0: our driver left the slot
    mu::set_sink_hook(0);
    marks_length = result_text(marks_text);
    file_put(result_path, marks_text, marks_length);                     // enters A's writes itself if closed
    marks_end();
    settle_writes(false);
    home_reboot(reboot_menu);                                            // returns only if the link was refused
    menu_usb = 0;                                    // then: no USB this boot (our driver left; no stock CDC to call)
}
#endif
// Every pass of home_pump with the composite up: the driver's pass, the MTP mark's clock, the HELLO bound.
void composite_turn() {
    if (!composite_on) return;
    const U32 t = now();
    const U32 gap = t - probe[p_last];                                   // one pass of the menu loop
    if (gap > probe[p_max_gap]) probe[p_max_gap] = gap;
    mtp_pass_ms = probe[p_max_gap];
    probe[p_last] = t; ++probe[p_passes];
    mu::pump();
    if (mu::stats().mtp_commands != last_commands) { last_commands = mu::stats().mtp_commands; mtp_ms = now(); }
#if defined(XYF_MTP_USB_TRIAL)
    if (!probe[p_hello] && now() - probe[p_started] >= composite_hello_ms) fallback_restart();
#endif
}
// Speed (2026-11-15): while MTP traffic is active (an operation in flight, IN bytes staged, or a command within
// mtp_recent_ms), the menu-loop pass keeps running driver passes and responder steps: a small operation is
// answered in this pass (command in, data and response out: 1-3 ms instead of two or three 6 ms passes) and a
// transfer streams (each file request's 1 KiB goes out while the next is read). The burst ends after
// mtp_quiet_ms without USB or responder progress, or after mtp_burst_ms (the menu still redraws between
// bursts; the guard is kicked every pass).
const U32 mtp_burst_ms = 40, mtp_quiet_ms = 3, mtp_recent_ms = 300;
void mtp_burst() {
    if (!composite_on || !mtp_live()) return;
    ms::Session& m = mslot();
    if (!m.responder.busy() && !m.responder.pending() && now() - mtp_ms > mtp_recent_ms) return;
    const U32 start = now();
    U32 moved = now();
    while (now() - start < mtp_burst_ms) {
        const U32 before = m.bytes_in + m.bytes_out + m.steps;
        mu::pump();
        if (mu::stats().mtp_commands != last_commands) { last_commands = mu::stats().mtp_commands; mtp_ms = now(); }
        mtp_pump();
        if (m.bytes_in + m.bytes_out + m.steps != before) moved = now();
        else if (now() - moved >= mtp_quiet_ms) break;
    }
}
void composite_byte() {                                                  // bytes arrived on the composite's serial
    if (!composite_on || probe[p_first_byte]) return;
    probe[p_first_byte] = (now() - probe[p_started]) | 1u;              // nonzero: the mark is written once
    mu::mark(mu::mark_first_serial_byte);
}
void composite_hello() {                                                 // after the reply to a valid HELLO
    if (!composite_on || probe[p_hello]) return;
    probe[p_hello] = (now() - probe[p_started]) | 1u;
#if defined(XYF_MTP_USB_TRIAL)
    mu::mark(mu::mark_first_hello);
    marks_length = result_text(marks_text);
    file_put(result_path, marks_text, marks_length);
    marks_end();                                                         // no more marks this boot
#endif
}
// diag.mtp_usb op 0: the trial. Refused while anything else runs, without the guard rule, on the composite
// already, or when the span gate refuses (s_identity, op 1 says which span). Else the flag file (s_ok after its
// close), the reply, A's window closed, the full restart.
U32 trial_start() {
#if !defined(XYF_MTP_USB_TRIAL)
    if (true) return P::s_unsupported;                                   // 188: the product has no trial
#endif
    if (job_running() || closing XYF_OR_MTP_BUSY || (put_expected && result.put_received != put_expected)) return P::s_busy;
    if (guard_on || !menu_usb || composite_on) return P::s_state;       // record 022: nothing restarts B alone
    if (probe_spans()) return P::s_identity;
    return file_put(flag_path, "T2a\n", 4);
}
void probe_result(uint8_t seq) {
    U32 driver[n_driver_words];
    driver_words(driver);
    sink_digest();
    P::put32(files, P::s_ok);
    U32 at = 4;
    for (U32 i = 0; i != probe_words; ++i, at += 4) P::put32(files + at, probe[i]);
    for (U32 i = 0; i != n_driver_words; ++i, at += 4) P::put32(files + at, driver[i]);
    for (U32 i = 0; i != 32; ++i) files[at++] = probe_digest[i];
    for (U32 i = 0; i != gate::record_words; ++i, at += 4) P::put32(files + at, probe_refusal[i]);
    send(t_mtp_usb, seq, files, at);
}
void mtp_usb_command(uint8_t seq, const uint8_t* payload, U32 length) {
    const uint8_t op = length ? payload[0] : 0xff;
    if (op == 0 && length == 1) {
        const U32 code = trial_start();
        status_only(t_mtp_usb, seq, code);
        if (code != P::s_ok) return;
        settle_writes(false);                                            // as REBOOT: A's window closed first
        const U32 t = now();
        while (now() - t < 50u) {}
        home_reboot(reboot_menu);
    }
    else if (op == 1 && length == 1) probe_result(seq);
    else status_only(t_mtp_usb, seq, op > 1 && op != 0xff ? P::s_unsupported : P::s_bad_request);
}
#endif

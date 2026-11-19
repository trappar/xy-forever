// Product-only, RAM-only rehearsal: one request left out per menu pass. No writer or filesystem mutation.
#if defined(XYF_TFW_CHECK)
namespace ais = xy_forever::a_install_stage;
enum RehearsalStep { rs_enter, rs_preflight, rs_close, rs_begin, rs_chunk, rs_finalize, rs_digest, rs_abort };
struct Rehearsal {
    U32 active, step, pending, cancel, code, token, size, done, kind, crc, started, deadline, word;
    uint32_t expected_ack;
    uint8_t sha[32], observed[32];
};
Rehearsal rehearsal __attribute__((section(".data"))) = {};
U32 rehearsal_counter __attribute__((section(".data"))) = 0;
bool rehearsal_active() { return rehearsal.active != 0; }
void rehearsal_end(U32 code) { rehearsal.code = code; rehearsal.active = rehearsal.pending = 0; }
void rehearsal_pump() {
    Rehearsal& s = rehearsal;
    if (!s.active) return;
    if (now() - s.started >= s.deadline) { s.cancel = 1; s.code = P::s_cancelled; }
    if (s.pending) {
        const file_client::Reply r = file_client::left_poll(false);
        if (r.outcome == file_client::busy) return;
        s.pending = 0;
        if (r.outcome) { rehearsal_end(file_client::status(r.outcome)); return; } // never send recovery after link
        if (s.step == rs_enter) { if (r.error || U32(r.value) != s.expected_ack) { rehearsal_end(P::s_link); return; } }
        else if (s.step == rs_preflight) {
            if (r.value || r.error != ais::version) { rehearsal_end(r.value ? U32(r.value) : U32(P::s_unsupported)); return; }
        }
        else if (s.step == rs_close) {
            if ((r.value != 1 && r.value != 3) || r.error) { rehearsal_end(P::s_storage); return; }
            mounted = 0;
        } else if (s.step == rs_digest) {
            P::put32(s.observed + 8 * s.word, U32(r.value));
            P::put32(s.observed + 8 * s.word + 4, r.error);
            if (++s.word != 4) return;
            if (!sha_equal(s.observed, s.sha)) { s.code = P::s_sha; s.cancel = 1; }
        } else {
            if (r.value) { rehearsal_end(U32(r.value)); return; } // A clears its owner on every refusal
            if (s.step == rs_chunk) {
                const U32 n = s.size - s.done < ais::piece ? s.size - s.done : ais::piece;
                s.done += n;
                if (r.error != s.done) { s.cancel = 1; s.code = P::s_state; }
                if (s.done != s.size && !s.cancel) return;
            }
            if (s.step == rs_finalize && r.error != s.crc) { s.cancel = 1; s.code = P::s_crc; }
        }
        if (s.step == rs_abort) { rehearsal_end(s.code); return; } // success also ABORTs after collecting A's proof
        ++s.step;
    }
    // Admission required an idle, live session. Once that accepted job loses its
    // link between requests, idle cannot become true again in this boot.
    if (!menu_link::usb_link_up()) { rehearsal_end(P::s_link); return; }
    if (s.cancel && s.step <= rs_begin) { rehearsal_end(s.code); return; }
    if (s.cancel && s.step > rs_begin) s.step = rs_abort;
    // A keepalive can occupy the shared slot between passes. Never enter claim's
    // synchronous settle wait here: USB/status must keep pumping while A is delayed.
    if (!menu_link::usb_idle()) return;
    file_client::Reply r;
    if (s.step == rs_enter) r = file_client::serving_start(s.expected_ack);
    else if (s.step == rs_close) r = file_client::unmount_start();
    else {
        uint8_t body[4096];
        const U32 op = s.step == rs_preflight ? ais::preflight : s.step == rs_begin ? ais::begin : s.step == rs_chunk ? ais::chunk :
                       s.step == rs_finalize ? ais::finalize : s.step == rs_digest ? ais::digest : ais::abort;
        const U32 n = op == ais::chunk ? (s.size - s.done < ais::piece ? s.size - s.done : ais::piece) : 0;
        const U32 fields[] = {ais::magic, ais::version, op, s.token, op == ais::chunk ? s.done :
            op == ais::digest ? s.word : 0, op == ais::begin ? s.size : n,
            op == ais::begin ? s.crc : n ? xyf::crc::update(0, staged + s.done, n) : 0,
            op == ais::begin ? s.kind : 0};
        for (U32 i = 0; i != 8; ++i) P::put32(body + 4 * i, fields[i]);
        if (op == ais::begin) for (U32 i = 0; i != 32; ++i) body[32 + i] = s.sha[i];
        for (U32 i = 0; i != n; ++i) body[32 + i] = staged[s.done + i];
        r = file_client::write_start(int(ais::handle), body, op == ais::begin ? 64 : 32 + n);
    }
    if (r.outcome) rehearsal_end(file_client::status(r.outcome));
    else s.pending = 1;
}
void rehearsal_command(uint8_t seq, const uint8_t* p, U32 length) {
    U32 code = P::s_bad_request;
    const U32 op = length >= 8 ? P::get32(p + 4) : ~0u;
    Rehearsal& s = rehearsal;
    if (length >= 4 && P::get32(p) != ais::version) code = P::s_unsupported;
    else if (op == 0 && length == 48) {
        stop_idle_gc();
        if (boot_probe_active() || s.active || recovery_pending || job_running() || closing || uploading() ||
            boot_sweep < 2 XYF_OR_MTP_BUSY) code = P::s_busy;
        else if (writes_due || file_client::left_out() || !menu_link::usb_idle()) code = P::s_busy;
        else if (P::get32(p + 8) > 1 || !P::get32(p + 12) || P::get32(p + 12) > ais::max_ms)
            code = P::s_bad_request;
        else if (!result.put_ok || !put_expected) code = P::s_state;
        else if (!sha_equal(p + 16, put_sha)) code = P::s_sha;
        else if (rehearsal_counter == 0xffffffffu) code = P::s_state;
        else {
            s.active = 1; s.step = s.pending = s.cancel = s.code = s.done = s.word = 0;
            s.token = ++rehearsal_counter; s.size = put_expected; s.kind = P::get32(p + 8);
            s.crc = result.put_crc;
            s.started = now(); s.deadline = P::get32(p + 12);
            for (U32 i = 0; i != 32; ++i) { s.sha[i] = put_sha[i]; s.observed[i] = 0; }
            code = P::s_ok;
        }
    } else if (op == 1 && length == 8) code = P::s_ok;
    else if (op == 2 && length == 8 && s.active) { s.cancel = 1; s.code = P::s_cancelled; code = P::s_ok; }
    if (code) { status_only(P::t_a_rehearsal, seq, code); return; }
    uint8_t reply[60];
    const U32 fields[] = {code, ais::version, s.active ? s.step + 1 : 0, s.done, s.size, s.crc, s.code};
    for (U32 i = 0; i != 7; ++i) P::put32(reply + 4 * i, fields[i]);
    for (U32 i = 0; i != 32; ++i) reply[28 + i] = s.observed[i];
    send(P::t_a_rehearsal, seq, reply, sizeof(reply));
}
#else
bool rehearsal_active() { return false; }
void rehearsal_pump() {}
#endif

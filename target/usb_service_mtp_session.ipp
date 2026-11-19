// Part of usb_service.cpp, included there at this point: not compiled alone.
#if defined(XYF_MTP_SESSION)
#if defined(XYF_SIM)
}
namespace xyf_sim { void log(const char* format, ...); }                // the session screen in the log (sim/runtime.hpp)
namespace {
#endif
// ---- The MTP session (mtp_session.hpp). Not a job: enabled when the composite starts (the test build)
// or from the menu's MTP mode row / diag.mtp op 0 (the simulator), it stays until STOP / op 1 (session_leave:
// cancel, the open files closed one per pass, A's window closed) or the boot's end. Each pass runs responder
// steps for at most mtp_slice_ms (one file request each). Only an operation in flight holds the file client
// (mtp_busy): serial file commands, jobs and the menu's writes answer busy meanwhile, never wait; the reverse,
// an MTP operation that needs the files while a job, a window close or a serial upload runs, answers Device_Busy
// (mtp_prepare). An operation without USB progress for mtp_stall_ms is cancelled (the responder closes its file
// and removes a partial upload). A's write window: opened by the first change, kept while operations run, closed
// by the menu loop's settle window_settle_ms after the last one (as after serial writes).
namespace ms = xy_forever::mtp_session;
namespace mr = xyf_core::mtp;
ms::Session& mslot() { return *reinterpret_cast<ms::Session*>(job_work_end); }
static_assert(sizeof(ms::Session) <= mtp_slot_bytes - file_client::stream_capacity &&      // #144: the read-ahead
              job_work_end + mtp_slot_bytes == file_client::stream_buffer + file_client::stream_capacity,
              "the session fits its slot below the file client's read-ahead buffer (the slot's top)");
const uint8_t t_mtp_tunnel = 0x61;                                      // diag.mtp (dev, test only): the test transport
const U32 mtp_slice_ms = 8, mtp_stall_ms = 10000, mtp_serving_ms = 1000;
volatile U32 mtp_pass_ms __attribute__((section(".data"))) = 0;          // the composite's longest menu-loop pass
volatile U32 mtp_on __attribute__((section(".data"))) = 0;               // the session is enabled
volatile U32 mtp_piece __attribute__((section(".data"))) = mr::default_piece;   // #144: the transfer piece (op 7)
U32 units_since(uint64_t start) { return U32((xyf::clock::cycles() - start) >> 10); }   // #144: 1024 cycles
uint32_t mtp_window() {                                                  // the first change: A's write window
    U32 code = enter_writes();
    if (code == P::s_ok) { storage_used = 1; writes_due = 1; writes_ms = now(); code = file_client::mount_ready(mounted); }
    return code;                                                         // then op 8 again if needed, as file.change
}
bool put_running() { return uploading(); }
bool mtp_busy() {                                                        // an operation in flight, or the leave's closes
    if (!mtp_on) return false;
    const U32 stage = mslot().stage;
    return stage == ms::st_leaving || stage == ms::st_closing ||
           (stage == ms::st_running && (mslot().responder.busy() || mslot().files.write_fd >= 0 ||   // #144: a write
                                         mslot().files.ahead_asked));                                  // or read out
}
xyf_core::U32 mtp_prepare(void*) {                                                 // before an operation that needs the files
    ms::Session& m = mslot();
    stop_idle_gc();
    if (boot_probe_active() || recovery_pending || job_running() || closing || put_running()) { ++m.refused_busy; return mr::r_device_busy; }
    if (!writes_due) m.files.window = 0;                                 // the settle closed A's window
    // Speed (2026-11-15): A's check at most once per mtp_serving_ms (a request each; the open window
    // already skipped it).
    // #3: a stale request means op 9 goes out next (mount_ready): A is asked first, whatever the cache says.
    const bool serving = mounted != file_client::mounted_stale &&
                         (writes_due || (m.served_ms && now() - m.served_ms < mtp_serving_ms));
    if (!serving && a_serving()) m.served_ms = now() | 1u;
    if ((!serving && !m.served_ms) || file_client::mount_ready(mounted) != P::s_ok) {   // A gone or silent
        m.files.link = 1; m.served_ms = 0;
        return mr::r_device_busy;
    }
    m.files.link = 0;
    m.op_started_ms = now();
    return 0;
}
void mtp_log(void*, xyf_core::U32 code, const xyf_core::U32* params, xyf_core::U32 nparams, xyf_core::U32 response,
             xyf_core::U32 data) {
    ms::Session& m = mslot();
    uint32_t* e = m.log[m.log_count % ms::log_entries];
    e[0] = (code & 0xffffu) | response << 16; e[1] = params[0]; e[2] = params[1]; e[3] = params[2];
    e[4] = (data & 0xfu) | (nparams & 0xfu) << 4 | now() << 8;
    ++m.log_count;
    if (response && data < 3) {
        const U32 op_ms = now() - m.command_ms;                          // speed: the command to its response
        ++m.hist[op_ms < 2 ? 0 : op_ms < 5 ? 1 : op_ms <= 20 ? 2 : 3];
        ++m.operations;
        const U32 took = now() - m.op_started_ms;
        if (m.op_started_ms && took > m.longest_op_ms) m.longest_op_ms = took;
        m.op_started_ms = 0;
    }
}
void mtp_say(bool force) {
#if defined(XYF_SIM)
    static char mtp_numbers[2][12];
    ms::Session& m = mslot();
    const U32 t = now();
    if (!force && t - m.said_ms < job_say_ms) return;
    m.said_ms = t;
    decimal_text(mtp_numbers[0], m.bytes_in);
    decimal_text(mtp_numbers[1], m.bytes_out);
    ::xyf_sim::log("mtp screen: stage=%u in=%s out=%s link=%u", m.stage, mtp_numbers[0], mtp_numbers[1], m.files.link);
    if (m.stage == ms::st_ended) say("MTP mode ended.", m.files.link ? "Processor A did not answer." : "", "", "");
    else if (m.stage != ms::st_running) say("MTP mode", "Leaving...", "", "");
    else say("MTP mode", mtp_numbers[0], mtp_numbers[1], m.files.link ? "A lost. STOP to leave" : "STOP to leave");
#else
    (void)force;                                                         // the device: the menu stays (the MTP mark)
#endif
}
U32 session_enter() {
    if (mtp_on && mslot().stage != ms::st_ended) return P::s_busy;
    ms::Session& m = mslot();
    ms::files_init(m.files, &mounted, mtp_window);
    mr::FileOps ops = ms::file_ops(m.files);
    ops.prepare = mtp_prepare; ops.log = mtp_log;
    m.responder.init(ops);
    m.responder.set_piece(mtp_piece);
    m.in.length = m.in.sent = 0;
    m.stage = ms::st_running; m.bytes_in = m.bytes_out = m.longest_ms = m.steps = m.cancels = m.said_ms = 0;
    m.stalls = m.refused_busy = m.log_count = m.operations = m.longest_op_ms = m.op_started_ms = 0;
    m.hist[0] = m.hist[1] = m.hist[2] = m.hist[3] = m.command_ms = m.served_ms = 0;
    m.started_ms = m.progress_ms = now();
    mtp_on = 1;
    mtp_say(true);
    return P::s_ok;
}
U32 session_leave() {
    if (!mtp_on || mslot().stage == ms::st_ended) return P::s_state;
    ms::Session& m = mslot();
    if (m.stage == ms::st_running) { m.responder.cancel(); m.stage = ms::st_leaving; mtp_say(true); }
    return P::s_ok;
}
void mtp_pump() {
    if (!mtp_on) return;
    ms::Session& m = mslot();
    const U32 start = now();
    if (m.stage == ms::st_running) {
        if (job_running()) m.responder.invalidate();                     // speed: the job changes files (cache)
        ms::Timing& timing = m.files.timing;
        for (;;) {                                                       // one file request per step
            const uint64_t step_start = xyf::clock::cycles();
            if (!m.responder.step()) { if (m.responder.busy()) ++timing.waits; break; }
            ++timing.steps; timing.step_units += units_since(step_start);
            ++m.steps; m.progress_ms = now();
            if (now() - start >= mtp_slice_ms) break;
        }
        if (!m.responder.busy()) ms::collect(m.files);                 // #144: a write (read) left out, MTP idle
        if (m.responder.busy()) {
            if (writes_due) writes_ms = now();                           // A's window stays while operations run
            if (now() - m.progress_ms > mtp_stall_ms) { m.responder.cancel(); ++m.stalls; m.progress_ms = now(); }
        } else m.progress_ms = now();
        mtp_say(false);
    } else if (m.stage == ms::st_leaving) {
        if (m.responder.step()) return;                                  // the cancel: close, remove a partial upload
        U32 i = 0;
        while (i != ms::max_open && m.files.open[i] < 0) ++i;
        const mr::FileOps ops = ms::file_ops(m.files);
        if (i != ms::max_open) ops.close(ops.context, m.files.open[i]);  // one request per pass
        else { ms::collect(m.files); m.stage = ms::st_closing; settle_begin(); }   // op 9 when a change opened it
    } else if (m.stage == ms::st_closing && !closing) {
        m.stage = ms::st_ended;
        mtp_say(true);
    }
    const U32 pass = now() - start;
    if (pass > m.longest_ms) m.longest_ms = pass;
}
#if defined(XYF_MTP_USB)
// The composite's MTP interface (mtp_usb::Handler) over the session's responder.
bool mtp_live() { return mtp_on && mslot().stage == ms::st_running; }
bool h_deliver(const uint8_t* data, uint32_t n) {
    ms::Session& m = mslot();
    const bool command = mtp_live() && !m.responder.busy() && n >= 12 && data[4] == 1 && !data[5];
    if (!mtp_live() || !m.responder.receive(data, n)) return false;
    if (command) m.command_ms = now();                                   // speed: the operation's start
    m.bytes_in += n; m.progress_ms = now();
    return true;
}
uint32_t h_pending() { return mtp_live() ? mslot().responder.pending() : 0u; }
uint32_t h_length() { return mtp_live() ? mslot().responder.staged_length() : 0u; }
uint32_t h_take(uint8_t* out, uint32_t n) {
    ms::Session& m = mslot();
    const U32 got = mtp_live() ? m.responder.take(out, n) : 0u;
    m.bytes_out += got;
    if (got) m.progress_ms = now();
    return got;
}
bool h_idle() { return !mtp_live() || !mslot().responder.busy(); }
void h_cancel(bool reset) {
    ms::Session& m = mslot();
    if (!mtp_live() || (!m.responder.busy() && !(reset && m.responder.session_open()))) return;
    if (reset) m.responder.reset(); else m.responder.cancel();
    ++m.cancels;
}
uint16_t h_status() { return mtp_live() ? uint16_t(mslot().responder.device_status()) : uint16_t(mr::r_ok); }
const xyf::mtp_usb::Handler mtp_handler = {h_deliver, h_pending, h_length, h_take, h_idle, h_cancel, h_status};
#endif
// #144 diag.mtp op 7, u8 then: 0 the timing: u32 status, u32 piece, u32 B's clock (units of 1024 cycles), B's
// mtp_session::Timing (timing_words), u32 1 when A's words follow (0: A not asked now: an operation, a job or a
// window close holds the file client, or A did not answer), A's file_engine::timing_base_words
// (storage_server_timing.ipp); 1 clear both (u32 status, u32 A cleared); 2 u32 bytes: the transfer piece, chunk
// to buffer_bytes (u32 status, u32 piece; s_busy during an operation); 3 A's other requests by operation: u32
// status, u32 1 when A's words follow (as for 0), file_engine::timing_op_slots x (count, sum, longest, checks). The status;
// n: the reply's length.
// A's timing words [first, end) at files + n, after a u32 flag: 1 when A answered them all (a_free: A may be asked).
void a_words(bool a_free, U32 first, U32 end, U32& n) {
    const U32 flag = n;
    U32 a_ok = a_free ? 1u : 0u;
    n += 4;
    for (U32 k = first / 2; k != end / 2; ++k, n += 8) {
        file_client::Reply r = {file_client::refused, 0, 0};
        if (a_ok) r = file_client::a_timing(k);
        if (r.outcome != file_client::done) a_ok = 0;
        P::put32(files + n, a_ok ? U32(r.value) : 0u); P::put32(files + n + 4, a_ok ? r.error : 0u);
    }
    P::put32(files + flag, a_ok);
}
static_assert(file_engine::timing_base_words % 2 == 0 && file_engine::timing_words % 2 == 0, "A's word pairs");
U32 mtp_timing(const uint8_t* payload, U32 length, bool live, U32& n) {
    ms::Session& m = mslot();
    const uint8_t sub = payload[1];
    // A leaves file RPC for a catalog refresh (including the one after a window close).
    // Admit the normal read service first: a raw timing request there would stay out and
    // block the very command needed to re-enter RPC. serving() preserves an occupied slot.
    const bool a_free = length == 2 && (sub == 0 || sub == 1 || sub == 3) &&
                        mtp_on && !mtp_busy() && !job_running() && !closing && !put_running() && !m.files.link &&
                        a_serving();
    if (sub == 0 && length == 2) {
        P::put32(files + 4, mtp_piece);
        P::put32(files + 8, U32(xyf::clock::cycles() >> 10));
        const U32* b = reinterpret_cast<const U32*>(&m.files.timing);
        n = 12;
        for (U32 i = 0; i != ms::timing_words; ++i, n += 4) P::put32(files + n, mtp_on ? b[i] : 0u);
        a_words(a_free, 0, file_engine::timing_base_words, n);
        return P::s_ok;
    }
    if (sub == 3 && length == 2) {
        n = 4;
        a_words(a_free, file_engine::timing_base_words, file_engine::timing_words, n);
        return P::s_ok;
    }
    if (sub == 1 && length == 2) {
        if (mtp_on) m.files.timing = ms::Timing();
        const file_client::Reply r = a_free ? file_client::a_timing(file_engine::timing_clear) : file_client::Reply();
        P::put32(files + 4, a_free && r.outcome == file_client::done && r.value == 0 ? 1u : 0u);
        n = 8;
        return P::s_ok;
    }
    if (sub == 2 && length == 6) {
        const U32 bytes = P::get32(payload + 2);
        if (bytes < mr::chunk || bytes > mr::buffer_bytes) return P::s_bad_request;
        if (mtp_busy()) return P::s_busy;
        mtp_piece = bytes;
        if (live) m.responder.set_piece(bytes);
        P::put32(files + 4, mtp_piece);
        n = 8;
        return P::s_ok;
    }
    return P::s_bad_request;
}
// diag.mtp: u8 op, then: 0 enter, 1 leave, 2 one bulk-OUT transfer (s_busy: not taken now, send it again),
// 3 u32 room: bulk-IN bytes (reply u32 status, u32 zlp, bytes), 4 cancel, 5 state (u32 status, then stage,
// bytes in, bytes out, longest pass ms, device status, link, steps, file requests, busy, stalls, refused busy,
// operations, longest operation ms, log count; speed, 2026-11-15: GetObjectInfo answered from the listing, read
// and write requests, their bytes, operations under 2 ms, 2-4, 5-20, over 20 ms, the longest menu-loop pass ms
// (the composite's; 0 without it)), 6 the operations log (u32 status, u32 count, then up to
// log_entries entries of 5 words, oldest first: mtp_session.hpp).
void mtp_tunnel(uint8_t seq, const uint8_t* payload, U32 length) {
    ms::Session& m = mslot();
    const uint8_t op = length ? payload[0] : 0xff;
    const bool live = mtp_on && m.stage == ms::st_running;
    U32 code = P::s_ok, n = 4;
    if (op == 0 && length == 1) code = session_enter();
    else if (op == 1 && length == 1) code = session_leave();
    else if (op == 2 && length > 1 && length - 1 <= mr::buffer_bytes) {
        const uint64_t pump_start = xyf::clock::cycles();
        if (!live) code = P::s_state;
        else if (m.responder.receive(payload + 1, length - 1)) m.bytes_in += length - 1;
        else code = P::s_busy;
        if (live) { ++m.files.timing.pumps; m.files.timing.pump_units += units_since(pump_start); }
    } else if (op == 3 && length == 5) {
        U32 room = P::get32(payload + 1);
        if (room > read_range_max - 8) room = read_range_max - 8;
        U32 got = 0, zlp = 0;
        if (!live) code = P::s_state;
        else {
            const uint64_t pump_start = xyf::clock::cycles();
            got = m.responder.take(files + 8, ms::phase_room(m.in, room));   // never past a container's end
            zlp = got && ms::phase_zlp(m.in, files + 8, got) ? 1u : 0u;
            m.bytes_out += got;
            ++m.files.timing.pumps; m.files.timing.pump_units += units_since(pump_start);
        }
        P::put32(files + 4, zlp);
        n = 8 + got;
    } else if (op == 4 && length == 1) {
        if (!live) code = P::s_state;
        else { m.responder.cancel(); ++m.cancels; m.in.length = m.in.sent = 0; }
    } else if (op == 5 && length == 1) {
        const U32 values[22] = {mtp_on ? m.stage : 0u, m.bytes_in, m.bytes_out, m.longest_ms,
                                live ? U32(m.responder.device_status()) : 0u,
                                m.files.link, m.steps, m.files.requests, mtp_busy() ? 1u : 0u,
                                m.stalls, m.refused_busy, m.operations, m.longest_op_ms, m.log_count,
                                m.responder.cache_hits(), m.files.data_requests, m.files.data_bytes,
                                m.hist[0], m.hist[1], m.hist[2], m.hist[3], mtp_pass_ms};
        for (U32 i = 0; i != 22; ++i) P::put32(files + 4 + 4 * i, values[i]);
        n = 92;
    } else if (op == 6 && length == 1) {
        const U32 count = mtp_on ? m.log_count : 0u, first = count > ms::log_entries ? count - ms::log_entries : 0u;
        P::put32(files + 4, count);
        n = 8;
        for (U32 i = first; i != count; ++i)
            for (U32 k = 0; k != 5; ++k, n += 4) P::put32(files + n, m.log[i % ms::log_entries][k]);
    } else if (op == 7 && length >= 2) {
        code = mtp_timing(payload, length, live, n);
    } else code = xyf::unlisted_op::status(payload, length, 7);     // not in protocol/v1.json yet
    P::put32(files, code);
    result.last_status = code;
    send(t_mtp_tunnel, seq, files, n);
}
#endif

// Part of file_boot.cpp: B's side of the held link (its state, the bounded poll, settle, ask and send),
// included in place (in file_boot's anonymous namespace).
// b: link state shared with the key_ui hooks (initialized data, not BSS).
Stock121PeerSession* volatile b_session __attribute__((section(".data"))) = 0;
volatile U32 b_lost __attribute__((section(".data"))) = 0;
volatile U32 b_last_send __attribute__((section(".data"))) = 0;
volatile U32 b_sequence __attribute__((section(".data"))) = 0;
volatile U32 b_choice __attribute__((section(".data"))) = 0;      // 0 none, 1 default, 2 entry
volatile U32 b_entry __attribute__((section(".data"))) = 0;
#if defined(XYF_ONE_SESSION)
volatile U32 b_patience_ms __attribute__((section(".data"))) = 0;   // Test 065
volatile U32 b_keepalives __attribute__((section(".data"))) = 0;
#endif
volatile U32 b_catalog_ok __attribute__((section(".data"))) = 0;
#if defined(XYF_ONE_SESSION)
#define XYF_A_STORAGE_WORD a_storage_word
// 191: B's catalog request in flight while the menu runs (0 none, 1 asked, 2 the blob), since when,
// the echo count before it, and when A's last answer said its mount failed (0: it did not).
volatile U32 b_fetching __attribute__((section(".data"))) = 0;
volatile U32 b_fetch_at __attribute__((section(".data"))) = 0;
volatile U32 b_fetch_echoes __attribute__((section(".data"))) = 0;
volatile U32 b_failed_at __attribute__((section(".data"))) = 0;
#else
#define XYF_A_STORAGE_WORD 0u
#endif

xyf::catalog::Catalog& b_catalog() { return *reinterpret_cast<xyf::catalog::Catalog*>(xyf_boot::library_buffer); }
bool running(SessionResult s) { return s == SessionResult::waiting || s == SessionResult::choose_image; }

// b: a file request's answer (the request's result +0x10 and errno +0x14), copied by the poll that sees it
// complete (#144): the session's one request slot is reused by keepalives (every keepalive_ms) and menu
// commands, which zero it, and the menu loop may run between a request's submit and its collect.
#if defined(XYF_ONE_SESSION)
volatile U32 b_answer[2] __attribute__((section(".data"))) = {0, 0};
#endif
SessionResult b_poll(Stock121PeerSession& session) {
#if defined(XYF_ONE_SESSION)
    const bool filing = session.filing();
    const SessionResult status = session.poll(now());
    if (filing && !session.filing()) {
        b_answer[0] = session.request_value();
        b_answer[1] = session.request_error();
    }
    return status;
#else
    return session.poll(now());
#endif
}
// b: one bounded poll; false (and lost) once the session left the held state.
bool b_step(Stock121PeerSession& session) {
    if (b_lost) return false;
    const SessionResult status = b_poll(session);
    ++result.polls;
    result.poll_result = U32(status);
    if (running(status)) return true;
    b_lost = 1;
    return false;
}
// b: wait (bounded) for the outstanding echo. A file request still out past its caller's
// bound is not (yet) a lost link: the caller fails (link), the session stays up until lost_ms.
bool b_settle(Stock121PeerSession& session) {
    const key_ui::Where where(xyf::where::link_settle);
    const U32 start = now();
    while (session.busy() && !b_lost && now() - start < answer_ms) {
        b_step(session);
        where.wait();
    }
    if (session.busy() && !session.filing()) b_lost = 1;
    return !b_lost && !session.busy();
}
// b: one command as a request (Stock121PeerSession::ask). a's loop answers it, so it
// waits in a's request slot while a is busy (mount, scan, image read) and cannot be
// lost (Diagnosis 075: the op-10 command before this was overwritten by b's next
// header while a was still mounting). Bounded by wait_ms.
bool b_ask(Stock121PeerSession& session, U32 value, U32 wait_ms) {
    if (!b_settle(session)) return false;              // an outstanding keepalive echo first
#if defined(XYF_ONE_SESSION)
    // 191: A answers its first command after its mount (a full yaffs scan may outlast command_ms).
    if (!result.storage_ms && wait_ms < xyf_boot::start_ms) wait_ms = xyf_boot::start_ms;
#endif
    const U32 before = session.echoes();
    if (session.ask(value) != SessionResult::waiting) {
        b_lost = 1;
        return false;
    }
    ++result.menu.commands;
    const key_ui::Where where(xyf::where::command);
    const U32 start = now();
    while (session.busy() && !b_lost && now() - start < wait_ms) {
        b_step(session);
        where.wait();
    }
    if (session.busy() || session.echoes() != before + 1) b_lost = 1;
    // Report (b): when a answered the first command, and the longest wait for an answer.
    const U32 waited = now() - start;
    if (!b_lost && !result.storage_ms) result.storage_ms = now();
    if (waited > result.hash_ms) result.hash_ms = waited;
    b_last_send = now();
    return !b_lost;
}
// b: one tagged command item.
bool b_send(Stock121PeerSession& session, U32 item, U32 wait_ms = xyf_boot::command_ms) {
#if defined(XYF_ONE_SESSION)
    usb_service::rpc_reset(item);                      // Test 065: a leaves RPC mode on every b_send item
#endif
    // The low three bits stay 0 (a's bootloader's select takes the argument & 7: the user area).
    return b_ask(session, Stock121PeerSession::command_tag << 16 | (item & 0xffu) << 8 | (++b_sequence << 3 & 0xf8u),
                 wait_ms);
}

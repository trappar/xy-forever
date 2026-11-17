#ifndef XYF_PEER_SERVICES_HPP
#define XYF_PEER_SERVICES_HPP
#include "boot_services.hpp"
#include "../native/peer_boot.hpp"
namespace xy_forever { namespace peer_services {
// begin() timeout meaning "no session cap" (xyf_clock is wrap-safe): the session then
// ends only by its own bounds (handshake, idle rule, command and transfer waits).
static const uint32_t no_cap = 0xffffffffu;
using xyf_core::Byte;
using xyf_core::U32;
enum class Result { ok, waiting, choose_image, admit_image, ready, unavailable,
    identity, role, lifecycle, irq, bounds, protocol, timeout, drain, close };
enum class Stage { stopped, captured, opening, handshake1, handshake10,
    serving, receiving, admission, draining, closed, failed };
struct Interrupt { U32 id, index, handler, context, control; };
struct Snapshot {
    U32 role, gpio_role, driver, vtable, spi, tx_dma, rx_dma;
    U32 interrupt_mask, spi_control, spi_status, spi_count, tx_config, rx_config;
    U32 driver_phase, active_request, queue_head, queue_tail;
    U32 deferred_head, deferred_tail, gpio_table, gpio_slots, gpio_matches[2];
    U32 tx_pending, rx_pending, direction, primary_state, pending_request, released;
    U32 protocol_state;
    Interrupt interrupts[6];
};
struct Diagnostic {
    U32 magic, version; Stage stage; Result result;
    U32 requests, responses, last_operation, last_device_result, started_ms, elapsed_ms;
    U32 link_initialized;
    Snapshot before, after;
};
// Necessary static configuration/lifetime checks, not proof of physical link
// progress. All IRQ IDs and DMA fields are specific to the observed old image.
Result check_closed(const Snapshot& snapshot);
Result check_open(const Snapshot& snapshot);
Result check_drained(const Snapshot& snapshot);
// The link hardware off after the master close 889f562e (SPI control and both DMA
// configurations 0, no link interrupt registered), before a stock reset of b. The close never
// completes a request it abandons, so the driver's request pointers are not checked.
Result check_quiet(const Snapshot& snapshot);
// Borrowed DMA storage must be aligned, bounded SDRAM outside the entire live
// retained heap and both old/current module+stack reservations. This is an
// address policy only; it does not establish live CPLB mapping or ownership.
bool dma_buffer_range(U32 address, U32 count);
// Exact four embedded request slots in the retained role0 driver.
bool request_pool_slot(U32 driver, U32 request);
// A staged-session command (Stock121PeerSession::ask): a stock partition-select
// request (op 0x18, no data body) whose argument 0 carries command_tag in bits
// 31..16. The image transfer's own selects use argument 0 = 0 or 1, and b's
// install selects a partition number, so the two cannot be confused.
bool command_request(U32 operation, U32 argument0, U32 argument1);
inline U32 command_ack(U32 value) { return ~value; }   // role0's answer to a command
// Record 016 F1: a request of the stock bootloader's boot read, the one b's bootloader
// sends after b restarted alone: partition select 0x18 (not a command) or sector read 0x11/0x12.
bool boot_request(U32 vtable, U32 operation, U32 argument0, U32 argument1);
static const U32 handshake_value = 0x12345678u;        // the stock link open's op-10 argument (889f55fc)

// Experimental cold-only adapter. Uses the retained SPI machinery but never
// calls its NAND request executors or its unbounded synchronous RPC wrapper.
// begin()/poll() on BOTH roles form the image-selection barrier: no caller may
// independently fall back or call RAM boot until safe_to_handoff() is true.
// Caller buffers, this object and its diagnostic must remain live until closed.
// Scratch/destination are separate, 32-byte aligned, outside the whole live old
// heap and both module/stack reservations. Scratch is >=65536 bytes; destination
// capacity on role1 includes the extra sector. Frozen owner sources must also
// remain outside the heap/reservations while retained services are reopened;
// 0x8a staging is therefore unavailable during this session. Caller ownership
// and live mapping still require qualification. Nest readonly storage inside this
// session so their retained logger guards are restored in reverse order.
class Stock121PeerSession {
public:
    explicit Stock121PeerSession(Diagnostic& diagnostic);
    Result begin(services::Phase phase, Byte* scratch, U32 scratch_capacity,
                 Byte* destination, U32 destination_capacity,
                 U32 now_ms, U32 timeout_ms);
    Result freeze(const Byte* image, U32 size, const xyf_core::hash::Digest& admitted);
    // As freeze(), without SHA-256: identified by size and CRC-32; digest is zeroed.
    Result freeze_image(const Byte* image, U32 size, U32 crc, xyf_core::hash::Digest& digest);   // crc: of these bytes
    Result admit(const xyf_core::hash::Digest& admitted);
    // Handshake-only probes may hold both roles here. Image reception starts
    // only after this explicit call on role1; role0 may freeze earlier.
    Result start_receiving();
    // Test022 handshake-only probe: call before begin(). Role1 performs the
    // fresh 1/10 handshake, then sends stock generic release 7; role0 serves
    // the stock 1/10/7 exchange and accepts that release only after the link
    // initialized, without a frozen image. No storage requests are allowed.
    // Both roles then drain and close; the caller boots its embedded image.
    void handshake_only();
    // Test024 held session (call before begin, both roles). As handshake_only,
    // but role1 never releases by itself: its caller sends command() echoes
    // (stock generic op 10; role0's stock slave ISR 889f7af4 replies ~value
    // without any storage access) and finally release(). Role0 counts every
    // change of the stock slave RX header (L1 scratch 0x11b000c0, 12 bytes:
    // op|seq<<8, argument0, argument1) as role1 activity and fails with timeout
    // once idle_ms pass without activity; begin()'s timeout stays a hard cap.
    // Role1 fails if the link is not initialized within handshake_ms of begin.
    void hold(U32 idle_ms, U32 handshake_ms);
    // Test027 remote read (role0 held session; call before begin). A pending
    // stock storage request (slave pool slot at primary+4) is handed to
    // serve(context, request, session) instead of failing the session; the
    // handler answers it exactly once, either through the request's stock
    // executor (which queues the stock reply itself) or reply(). Requests
    // outside the four pool slots still fail (no safe reply exists). Serving
    // counts as role1 activity for the idle window.
    typedef void (*Serve)(void* context, U32 request, Stock121PeerSession& session);
    void serve(Serve handler, void* context);
    // Role0: answer a served request with result/errno and no data body (the
    // stock executors' own reply shape: +0x10, +0x14, then 889f6508).
    void reply(U32 request, U32 result, U32 error);
    U32 served() const;            // role0 requests handed to serve()
    // File boot (XY Forever, Test025): image transfer with an explicit outcome.
    // Role0 without an image calls decline() before begin(): it answers role1's
    // first partition select (stock 0x18, no data body) with result 1, then
    // accepts role1's release 7 as agreement on the embedded image. Role0 with
    // a frozen image refuses on restore (0x18 argument 0, result 2) if its
    // final identity check fails: the stock slave ISR acknowledges generic 7
    // itself, so restore is the owner's last refusal point. Role1 treats a
    // nonzero select/restore result as declined: it releases role0 and both
    // boot embedded. watch(): role0 fails once idle_ms pass without a request.
    void decline();
    void watch(U32 idle_ms);
    void hold_touch(U32 now);      // Test 048: role0 held session, a long local job (state write) is activity
    bool declined() const;
    // Commit point (the boot decision; independent of later drain/close):
    // role0 once the release byte arrives after a completed transfer and
    // restore; role1 once the release reply after restore is zero.
    bool committed() const;
    // Role1 after a complete transfer (admit phase): the CRC/SHA-verified
    // digest the owner announced; the caller passes it to admit() after its
    // own LDR admission instead of hashing the image a third time.
    const xyf_core::hash::Digest& received_digest() const { return client_.digest(); }
    U32 received_crc() const { return client_.crc(); }   // checked over the received bytes
    // XY Forever menu boot (both roles, call before begin): a held session
    // (keepalives/commands as hold()) that can also carry transfers. Role0
    // freezes on command: freeze_blob() for small non-LDR data (the library
    // catalog; ends after restore, the session stays held and the server
    // waits for the next freeze) or freeze() for the boot image (ends with
    // release 7 and the commit, as file boot). refuse() declines at run time:
    // role1's next select is answered 1 and both boot embedded. A release 7
    // without a frozen image also agrees on embedded. Role1 starts each
    // transfer with transfer(); a blob is admitted by its own CRC/SHA and
    // ends after restore (blob_done()), an image pauses at admit_image as in
    // file boot. touch(): role0's own long work (mount, image read) is not idle.
    void stage(U32 idle_ms, U32 handshake_ms);
    Result freeze_blob(const Byte* data, U32 size);
    void refuse();
    void touch(U32 now_ms);
    Result transfer(Byte* destination, U32 capacity, bool blob);
    bool blob_done() const;
    bool blob_pending() const { return blob_; }   // Test 061: role0, a frozen blob not yet restored
    // Test 065 (one link session): role0 staged session with a serve hook. While RPC mode
    // is on and nothing is frozen, b's stock storage requests go to the serve hook (storage
    // server); otherwise the image protocol. The owner switches modes from b's command items.
    void set_rpc(bool on) { rpc_ = on; }
    bool rpc() const { return rpc_; }
    U32 blobs() const;             // blobs completed (role0 served / role1 received)
    // Role0 staged: after agreement the session waits for allow_close() before
    // draining and closing, so storage nested inside it can unmount first
    // (logger guards are restored last-in, first-out).
    bool agreed() const;
    void allow_close();
    Result command(U32 value);     // role1, held, link ready and idle
    // Staged sessions (role1, link ready and idle): one command to role0 as a request.
    // An op-10 command() is echoed by role0's stock ISR and only sampled by role0's
    // poll: it is lost when another header follows before role0 polls again (role0
    // mounting storage, Diagnosis 075). A request waits in role0's stock pool slot
    // until role0's poll takes it, and role0's poll answers it itself with
    // command_ack(value). So busy() ending with one more echoes() means role0's loop
    // has the command, and it acts on it before it polls again.
    Result ask(U32 value);
    // Record 016 F1 (role0, staged): b restarted alone and its stock bootloader asks.
    // Seen as the stock link open's op-10 header (handshake_value) after b's first command, or as a
    // boot request while image mode has nothing frozen (in RPC mode the serve hook decides and calls
    // follow(), as it knows install mode). From then on poll() answers nothing: it takes b's first
    // boot request and holds it unanswered (a's bootloader answers it once a restarts, Test 108).
    // follow_now(): the owner closes storage and restarts a now (a held request, or follow_ms after
    // the handshake without one).
    void follow(U32 request);
    bool peer_restarted() const { return peer_restart_; }
    bool follow_now(U32 now_ms) const;
    static const U32 follow_ms = 2000;
    // Role0: true while an answer queued by poll() has not left yet (stock finished
    // queue, active request or TX). poll() only queues the answer; the stock interrupt
    // code sends it. A caller about to reset the processor waits (bounded) for false:
    // the stock reset disables interrupts at once.
    bool answering() const;
    Result release();              // role1, held, link ready and idle
    // Role1, staged, link ready and idle: B's own file request (file_client.cpp) on this
    // session's one request slot. request_slot() is the zeroed request object for a stock request
    // builder, or 0 while the slot is taken (a keepalive, command, transfer or file request) or the
    // session is not held. send_request() submits it as the stock RPC 889f7b12 does (done byte +4
    // cleared, 11a0060c) but never waits: poll() completes it. filing(): a file request is still
    // out, also after its caller stopped waiting (the slot stays in flight until the answer comes;
    // the stock code still owns the object and its buffers). Then request_value() / request_error()
    // hold the reply's result (+0x10) and errno (+0x14).
    U32* request_slot();
    Result send_request(U32 operation);
    bool filing() const { return filing_; }
    U32 request_value() const { return request_[4]; }
    U32 request_error() const { return request_[5]; }
    bool busy() const;             // role1 request outstanding
    U32 echoes() const;            // role1 verified op-10 echoes and command answers after the handshake
    U32 activity() const;          // role0 RX header changes seen
    U32 commands_seen() const;     // role0 commands: op-10 arguments tagged command_tag, or ask() requests
    U32 last_command() const;      // role0 last such argument
    U32 last_argument() const;     // role0 last op-10 argument of any kind
    U32 max_gap() const;           // role0 longest gap between activity (ms)
    static const U32 command_tag = 0x434du;   // "CM" in argument bits 31..16
    // Caller supplies a real monotonic millisecond clock; timeout is for the
    // complete selection+transfer session and may not exceed ten minutes.
    // Retained interrupt helpers have not been fault-injection qualified.
    Result poll(U32 now_ms);
    Result end();
    U32 role() const;
    U32 image_size() const;
    bool link_ready() const;
    bool safe_to_handoff() const;
    static bool capture(Snapshot& snapshot);
    // poll() re-checks the stock code identity (bitwise CRC over ~29 KB, a few
    // ms) at most every identity_interval_ms instead of on every poll (Test 037:
    // the per-poll CRC set the link round trip, ~4 ms per request, and the USB
    // upload rate). begin(), end() and capture() still check it every time.
    static const U32 identity_interval_ms = 250;
    // Role1 at cold entry (Tests 017-020): the stock master's deferred list
    // still names one stale node (head == tail, no next). The master setup
    // resets that list; capture() reports it here instead of as open work.
    static U32 stale_deferred();       // first stale node seen (0 if none)
    static U32 stale_deferred_last();  // latest stale node seen
private:
    Stock121PeerSession(const Stock121PeerSession&);
    Stock121PeerSession& operator=(const Stock121PeerSession&);
    Diagnostic& diagnostic_;
    xyf_core::peer::Server server_;
    xyf_core::peer::Client client_;
    Byte* scratch_; U32 scratch_capacity_, driver_, primary_, timeout_, pending_;
    U32 saved_logs_[4], request_[12], expected_bytes_, frozen_size_;
    bool opened_, guarded_, active_, failed_, agreed_, transferring_, handshake_only_, hold_;
    bool decline_, declined_, committed_, watch_, staged_, blob_, blob_done_, defer_close_;
    U32 blobs_;
    U32 idle_ms_, handshake_ms_, last_activity_, header_[3], expected_echo_;
    U32 echoes_, activity_, commands_seen_, last_command_, last_argument_, max_gap_;
    Serve serve_; void* serve_context_; U32 served_; bool served_activity_; bool rpc_;
    bool asking_;                  // role1: the outstanding request is an ask()
    bool filing_;                  // role1: the outstanding request is a file request
    U32 identity_ms_;
    bool peer_restart_;            // role0: b's bootloader seen (record 016 F1)
    U32 peer_request_, peer_ms_;   // role0: the boot request held unanswered; when b's restart was seen
    static bool capture_state(Snapshot& snapshot);   // capture() without the identity CRC
    void observe(U32 now);
    Result fail(Result result);
    Result submit(const xyf_core::peer::Request& request);
    void respond(U32 result);
};
} }
#endif

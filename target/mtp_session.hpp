#ifndef XYF_MTP_SESSION_HPP
#define XYF_MTP_SESSION_HPP
#include <stdint.h>
#include "../native/mtp_responder.hpp"
// 3.5b / T2b: the MTP session on processor B (build switch XYF_MTP_SESSION: the simulator and the test build
// xyf-mtp-usb, never the product). The responder core (native/mtp_responder) runs over B's timed file client:
// one request per FileOps call, run from B's menu loop (usb_service mtp_pump), responder steps for at most a
// slice per pass. Speed (2026-11-15): while MTP traffic is active the menu loop's pass repeats driver passes and steps
// (usb_service mtp_burst, up to 40 ms), so small operations finish within one pass. T2b: an open session is not a job: only an operation in flight (Responder::busy) holds the
// file client (serial file commands, jobs, menu actions answer busy meanwhile; an MTP operation that needs the
// files while a job, a window close or a serial upload runs answers Device_Busy). Its state (Session, about
// 35 KB, mostly the responder) lives in the last 64 KiB of the job region (the jobs' work regions end below).
//
// Transport: what the session needs from a USB bulk pair. The real driver (branch fan-mtpusb) implements it;
// the simulator's test transport is the dev command diag.mtp (0x61, usb_service), which tunnels containers.
//   deliver(transfer)   one bulk-OUT transfer (<= buffer_bytes); false: not now (the transport keeps it)
//   fetch(out, room)    bulk-IN bytes; zlp is set when this fetch ended an IN phase (one container) whose
//                       length is a multiple of packet bytes: the transport then sends a zero-length packet
//   cancel()            the class request Cancel (may come from an interrupt)
// Files: space (statfs) and seek map to the file client's statfs and lseek; nothing is answered "not
// supported". Changes pass the responder's write rule and the engine's policy (A again); the adapter also
// refuses any change outside xyf_core::mtp::writable. The first change opens A's write window (on_change);
// the session closes it when it ends, or when no change came for idle_ms with no file open for writing.
namespace xy_forever { namespace mtp_session {
namespace M = xyf_core::mtp;
static const uint32_t packet_bytes = 512, max_open = 4, idle_ms = 2000, log_entries = 128;
// The IN side's phase tracking (pure): each container starts with its u32 length.
struct InPhase { uint32_t length, sent; uint8_t head[4]; };
// How many bytes the next fetch may take so that it never crosses a container's end (one container per IN
// phase): the rest of the header (4 bytes) first, then the rest of the container.
uint32_t phase_room(const InPhase& p, uint32_t room);
// Bytes [0, n) of data were just sent; true when they ended a container whose length % packet_bytes == 0.
bool phase_zlp(InPhase& p, const uint8_t* data, uint32_t n);
struct Files {
    volatile uint32_t* mounted;         // usb_service's op-8 state (rename needs it)
    uint32_t (*on_change)();            // opens A's write window (usb_service enter_writes): s_ok, else a status
    uint32_t link, window, writing, last_change_ms, requests;
    uint32_t data_requests, data_bytes; // speed (2026-11-15): read and write requests, the bytes they moved
    int open[max_open];                 // files the responder holds (closed when the session ends)
    uint8_t open_write[max_open];
    uint8_t page[M::page * (6 + 255)] __attribute__((aligned(4)));   // one list page (file_client::list_entry)
};
void files_init(Files& f, volatile uint32_t* mounted, uint32_t (*on_change)());
M::FileOps file_ops(Files& f);          // the adapter (context: &f)
struct Session {
    M::Responder responder;
    Files files;
    InPhase in;
    uint32_t stage, bytes_in, bytes_out, longest_ms, started_ms, steps, cancels, said_ms;
    // T2b: stalls (an operation without USB progress for stall_ms: cancelled), operations refused busy,
    // operations logged; the log ring (T2c: 128 entries of 5 words): {code | response << 16, parameters 1..3,
    // data | parameter count << 4 | ms << 8} (mtp_responder.hpp).
    uint32_t stalls, refused_busy, log_count, progress_ms, operations, longest_op_ms, op_started_ms;
    uint32_t log[log_entries][5];
    // Speed (2026-11-15): each operation's time from its command to its response (ms) in buckets: < 2, 2-4, 5-20,
    // > 20; the command's arrival; when A last proved it serves (usb_service a_serving; at most one check a second).
    uint32_t hist[4], command_ms, served_ms;
};
enum Stage : uint32_t { st_none = 0, st_running, st_leaving, st_closing, st_ended };
} }
#endif

#ifndef XYF_BLE_SESSION_HPP
#define XYF_BLE_SESSION_HPP

#include "companions.hpp"

namespace xyf_core { namespace companion {

// Nordic application update order. uninitialized..answer happen before the bootloader
// session; start..exit inside it; regreeting..verify after the chip left it.
enum class BlePhase {
    uninitialized,
    query,
    enter,
    settle,
    probe,
    greeting,
    answer,
    start,
    header,
    data,
    finish,
    exit,
    regreeting,
    release,
    verify,
    complete,
    aborting,
    aborted,
    failed
};
// write: send, no reply (complete_action). exchange: send, then accept the
// reply. listen: send nothing; accept the next unsolicited frame of `command`
// (the bootloader greeting). delay: wait delay_us (complete_action).
enum class BridgeAction { write, exchange, listen, delay };
struct BridgeRequest {
    BridgeAction action;
    U32 command;
    U32 timeout_ms;
    U32 delay_us;
};
// One official application the caller admits: SHA-256 of ble.bin and the
// version from its ble.version. Supplied at run time; none are built in.
struct BleKnownImage {
    Byte sha256[32];
    U32 version[4];
};

// Parses the stock semicolon-separated metadata format, not JSON. The bridge
// driver must already have checked wire framing and correlated the reply.
// The whole payload is validated; a missing or repeated key is a framing error.
Status ble_metadata_field(const Byte* payload, U32 size, const char* key, const Byte*& value, U32& value_size);
Status ble_mode_is_bootloader(const Byte* payload, U32 size, bool& bootloader);
// "a.b.c+d", each decimal <= 65535 (the chip's sw_version form).
Status ble_parse_version(const Byte* text, U32 size, U32* version);
// The stock platform word: product 0x21 at bits 14+ and hardware revision in
// the low ten bits. Stock sends it in start and header; the chip ignores it
// (017), so it is no guard: the SHA-256 match in BleSession::initialize is.
Status ble_platform_word(U32 hardware_revision, U32& word);
// Stock backplane entry carries a six-byte key from a private firmware profile.
// This API deliberately contains no captured key material.
Status backplane_enter_packet(const Byte* key, U32 key_size, Byte* output, U32 capacity, U32& produced);

// Application-only Cypress flow using the 62-byte callback MTU present in
// both retained bootloaders. Its six-byte entry key is supplied privately.
class BackplaneSession {
public:
    BackplaneSession();
    Status initialize(const CypressRow* rows, U32 count, const Byte* key, U32 key_size);
    Status request(Byte* output, U32 capacity, U32& produced) const;
    Status accept(const Byte* packet, U32 size);
    Status exit_request(Byte* output, U32 capacity, U32& produced) const;
    Phase phase() const { return configured_ ? session_.phase() : Phase::failed; }

private:
    CypressSession session_;
    Byte key_[6];
    bool configured_;
};

// Response-driven Nordic application update (1.2.1 bootloader):
// query mode; from the application send 0xee, read its 0x54, wait for the
// bootloader; answer its [0xbb] greeting with 0xee (session); start, header,
// 248-byte chunks, finish; exit with 0x18, answer the next [0xbb] with 0x18,
// then check the running sw_version. Every reply is checked (0x54 ok); there
// are no retries: one bad reply or timeout stops in `failed`. The driver runs
// each request once and reports with complete_action (write/delay) or
// accept/transport_failed (exchange/listen). The image must stay unchanged.
// 0x18 is requested only after finish returned 0x54: with an invalid
// application it would put the chip into System OFF.
class BleSession {
public:
    BleSession();
    // Refuses (no traffic ever) unless the image's SHA-256 equals one entry of
    // `known`; the version sent comes from that entry.
    Status initialize(const Byte* image, U32 size, const BleKnownImage* known, U32 count, U32 hardware_revision);
    Status request(Byte* output, U32 capacity, U32& produced, BridgeRequest& info) const;
    Status complete_action(bool success);
    // command/payload are a decoded and transport-correlated bridge message.
    Status accept(U32 command, const Byte* payload, U32 size);
    Status transport_failed();
    // Abandon a session that may be open (0xee again: the chip reboots into its
    // loop, nothing more is erased). Allowed while in_session().
    Status abort();
    BlePhase phase() const { return phase_; }
    U32 offset() const { return offset_; }
    U32 platform_word() const { return platform_; }
    bool in_session() const { return session_; }
    // Finish returned 0x54: the chip holds a valid application.
    bool application_valid() const { return valid_; }

private:
    Status stop(Status reason);
    const Byte* image_;
    U32 size_, version_[4], platform_, crc_, offset_, probes_;
    BlePhase phase_;
    bool session_, valid_;
};

}}
#endif

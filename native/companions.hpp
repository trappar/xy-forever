#ifndef XYF_COMPANIONS_HPP
#define XYF_COMPANIONS_HPP

#include "ldr.hpp"

namespace xyf_core { namespace companion {

enum class Status {
    ok,
    argument,
    capacity,
    framing,
    length,
    checksum,
    device_status,
    identity,
    bounds,
    verification,
    stopped,
    layout
};

// Output storage must not overlap input storage. On error produced is zero;
// capacity/argument errors do not write the output buffer. No device I/O occurs.
Status cypress_encode(U32 command, const Byte* payload, U32 size, Byte* output, U32 capacity, U32& produced);
// On success payload borrows storage within packet for expected_size bytes.
Status cypress_decode(const Byte* packet, U32 size, U32 expected_size, const Byte*& payload);
Status crc16_ccitt(const Byte* data, U32 size, U32 initial, U32& result);
Status ble_header(U32 image_size, const U32* version, U32 crc, U32 device_id, Byte* output, U32 capacity,
                  U32& produced);
Status ble_start(const U32* version, U32 image_kind, U32 device_id, Byte* output, U32 capacity, U32& produced);
Status ble_chunk(const Byte* image, U32 image_size, U32 offset, Byte* output, U32 capacity, U32& produced,
                 U32& consumed);

struct CypressRow {
    U32 array_id, number;
    const Byte* data;
    U32 size;
};

enum class Phase { enter, bounds, buffer, program, verify, application, complete, failed };

// Rows and their data must outlive the session and remain unchanged. Only the
// independently admitted OP-XY application layout is accepted. This class
// emits requests and consumes replies; it has no transport, retries, timing,
// companion-reset operations or power-loss recovery implementation.
class CypressSession {
public:
    CypressSession();
    Status initialize(const CypressRow* rows, U32 count, U32 transfer_size);
    Status request(Byte* output, U32 capacity, U32& produced) const;
    Status accept(const Byte* packet, U32 size);
    // Stock exits with a write-only packet, then closes the bridge callback.
    Status exit_request(Byte* output, U32 capacity, U32& produced) const;
    Phase phase() const { return phase_; }
    U32 row_index() const { return row_; }
    U32 row_offset() const { return offset_; }

private:
    void select_row_phase();
    const CypressRow* rows_;
    U32 count_, transfer_size_, row_, offset_;
    Phase phase_;
};

}}
#endif

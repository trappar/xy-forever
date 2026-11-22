#include "ble_session.hpp"
#include "hash.hpp"

namespace xyf_core { namespace companion {
namespace {
bool text_equal(const Byte* value, U32 size, const char* expected) {
    U32 i = 0;
    for (; i < size; ++i)
        if (!expected[i] || value[i] != Byte(expected[i])) return false;
    return expected[i] == 0;
}
U32 minimum(U32 a, U32 b) { return a < b ? a : b; }
}

Status ble_metadata_field(const Byte* payload, U32 size, const char* key, const Byte*& value, U32& value_size) {
    value = 0;
    value_size = 0;
    if (!payload || !key || !size || size >= 249) return Status::argument;
    bool found = false;
    U32 begin = 0;
    while (begin < size) {
        U32 end = begin;
        for (; end < size && payload[end] != ';'; ++end)
            if (!payload[end]) return Status::framing;
        U32 colon = begin;
        for (; colon < end && payload[colon] != ':'; ++colon) {}
        if (colon == begin || colon == end) return Status::framing;
        if (text_equal(payload + begin, colon - begin, key)) {
            if (found) return Status::framing;
            found = true;
            value = payload + colon + 1;
            value_size = end - colon - 1;
        }
        begin = end + 1;
    }
    if (found) return Status::ok;
    value = 0;
    value_size = 0;
    return Status::framing;
}

Status ble_mode_is_bootloader(const Byte* payload, U32 size, bool& bootloader) {
    bootloader = false;
    const Byte* value = 0;
    U32 value_size = 0;
    const Status result = ble_metadata_field(payload, size, "mode", value, value_size);
    if (result == Status::ok) bootloader = text_equal(value, value_size, "bootloader");
    return result;
}

Status ble_parse_version(const Byte* text, U32 size, U32* version) {
    if (!text || !version) return Status::argument;
    const char separators[4] = {'.', '.', '+', 0};
    U32 fields[4] = {0, 0, 0, 0};
    U32 i = 0;
    for (U32 field = 0; field < 4; ++field) {
        U32 digits = 0;
        for (; i < size && text[i] >= '0' && text[i] <= '9'; ++i, ++digits) {
            fields[field] = fields[field] * 10 + U32(text[i] - '0');
            if (fields[field] > 0xffffu) return Status::framing;
        }
        if (!digits) return Status::framing;
        if (field < 3) {
            if (i == size || text[i] != Byte(separators[field])) return Status::framing;
            ++i;
        }
    }
    if (i != size) return Status::framing;
    for (U32 field = 0; field < 4; ++field) version[field] = fields[field];
    return Status::ok;
}

Status ble_platform_word(U32 hardware_revision, U32& word) {
    word = 0;
    if (hardware_revision >= 1000) return Status::argument;
    word = 0x84000u | hardware_revision;
    return Status::ok;
}

Status backplane_enter_packet(const Byte* key, U32 key_size, Byte* output, U32 capacity, U32& produced) {
    produced = 0;
    if (!key || key_size != 6) return Status::argument;
    return cypress_encode(0x38, key, key_size, output, capacity, produced);
}

BackplaneSession::BackplaneSession() : session_(), key_{0, 0, 0, 0, 0, 0}, configured_(false) {}

Status BackplaneSession::initialize(const CypressRow* rows, U32 count, const Byte* key, U32 key_size) {
    configured_ = false;
    if (!key || key_size != 6) return Status::argument;
    const Status result = session_.initialize(rows, count, 62);
    if (result != Status::ok) return result;
    for (U32 i = 0; i < 6; ++i) key_[i] = key[i];
    configured_ = true;
    return Status::ok;
}

Status BackplaneSession::request(Byte* output, U32 capacity, U32& produced) const {
    produced = 0;
    if (!configured_) return Status::stopped;
    if (session_.phase() == Phase::enter) return backplane_enter_packet(key_, 6, output, capacity, produced);
    return session_.request(output, capacity, produced);
}

Status BackplaneSession::accept(const Byte* packet, U32 size) {
    return configured_ ? session_.accept(packet, size) : Status::stopped;
}

Status BackplaneSession::exit_request(Byte* output, U32 capacity, U32& produced) const {
    produced = 0;
    return configured_ ? session_.exit_request(output, capacity, produced) : Status::stopped;
}

BleSession::BleSession()
    : image_(0), size_(0), version_{0, 0, 0, 0}, platform_(0), crc_(0), offset_(0), probes_(0),
      phase_(BlePhase::uninitialized), session_(false), valid_(false) {}

Status BleSession::stop(Status reason) {
    phase_ = BlePhase::failed;
    return reason;
}

Status BleSession::initialize(const Byte* image, U32 size, const BleKnownImage* known, U32 count,
                              U32 hardware_revision) {
    phase_ = BlePhase::failed;
    image_ = 0;
    size_ = 0;
    offset_ = 0;
    probes_ = 0;
    session_ = false;
    valid_ = false;
    if (!image || !known || !count || size < 8 || size > 0x70000 || (size & 3)) return Status::argument;
    hash::Sha256 sha;
    hash::Digest digest;
    if (!sha.update(image, size) || !sha.finish(digest)) return Status::argument;
    const BleKnownImage* match = 0;
    for (U32 i = 0; i < count && !match; ++i) {
        hash::Digest entry;
        for (U32 j = 0; j < 32; ++j) entry.bytes[j] = known[i].sha256[j];
        if (hash::equal(entry, digest)) match = known + i;
    }
    if (!match) return Status::identity;
    for (U32 i = 0; i < 4; ++i)
        if (match->version[i] > 0xffffu) return Status::argument;
    Status result = ble_platform_word(hardware_revision, platform_);
    if (result != Status::ok) return result;
    result = crc16_ccitt(image, size, 0, crc_);
    if (result != Status::ok) return result;
    image_ = image;
    size_ = size;
    for (U32 i = 0; i < 4; ++i) version_[i] = match->version[i];
    phase_ = BlePhase::query;
    return Status::ok;
}

namespace {
Status one_byte(Byte value, Byte* output, U32 capacity, U32& produced) {
    if (!output) return Status::argument;
    if (capacity < 1) return Status::capacity;
    output[0] = value;
    produced = 1;
    return Status::ok;
}
}

Status BleSession::request(Byte* output, U32 capacity, U32& produced, BridgeRequest& info) const {
    produced = 0;
    info = BridgeRequest{BridgeAction::exchange, 0, 0, 0};
    Status result = Status::ok;
    switch (phase_) {
    case BlePhase::query:
    case BlePhase::probe:
    case BlePhase::verify: info = BridgeRequest{BridgeAction::exchange, 9, 100, 0}; return Status::ok;
    case BlePhase::enter:              // to the application, which replies 0x54
        result = one_byte(0xee, output, capacity, produced);
        if (result == Status::ok) info = BridgeRequest{BridgeAction::exchange, 0x0d, 100, 0};
        return result;
    case BlePhase::settle: info = BridgeRequest{BridgeAction::delay, 0, 0, 250000}; return Status::ok;
    case BlePhase::greeting: info = BridgeRequest{BridgeAction::listen, 5, 500, 0}; return Status::ok;
    case BlePhase::regreeting: info = BridgeRequest{BridgeAction::listen, 5, 2500, 0}; return Status::ok;
    case BlePhase::answer:
    case BlePhase::aborting:   // starts / abandons the session
        result = one_byte(0xee, output, capacity, produced);
        if (result == Status::ok) info = BridgeRequest{BridgeAction::write, 0x0d, 0, 0};
        return result;
    case BlePhase::exit:               // in the session: only after finish returned 0x54
    case BlePhase::release:            // the loop's greeting after a valid exit: run the application
        if (!valid_) return Status::stopped;
        result = one_byte(0x18, output, capacity, produced);
        if (result == Status::ok) info = BridgeRequest{BridgeAction::write, 0x0d, 0, 0};
        return result;
    case BlePhase::start:
        result = ble_start(version_, 0xb0, platform_, output, capacity, produced);
        if (result == Status::ok) info = BridgeRequest{BridgeAction::exchange, 0x0d, 1500, 0};
        return result;
    case BlePhase::header: {
        if (!output) return Status::argument;
        if (capacity < 65) return Status::capacity;
        Byte header[64];
        U32 size = 0;
        result = ble_header(size_, version_, crc_, platform_, header, 64, size);
        if (result != Status::ok) return result;
        output[0] = 3;
        for (U32 i = 0; i < size; ++i) output[i + 1] = header[i];
        produced = size + 1;
        info = BridgeRequest{BridgeAction::exchange, 0x0d, 1000, 0};
        return Status::ok;
    }
    case BlePhase::data: {
        U32 consumed = 0;
        result = ble_chunk(image_, size_, offset_, output, capacity, produced, consumed);
        if (result == Status::ok) info = BridgeRequest{BridgeAction::exchange, 0x0d, 1000, 0};
        return result;
    }
    case BlePhase::finish:
        result = one_byte(5, output, capacity, produced);
        if (result == Status::ok) info = BridgeRequest{BridgeAction::exchange, 0x0d, 1000, 0};
        return result;
    default: return Status::stopped;
    }
}

Status BleSession::complete_action(bool success) {
    switch (phase_) {
    case BlePhase::settle:
        if (!success) return stop(Status::device_status);
        phase_ = BlePhase::probe;
        return Status::ok;
    case BlePhase::answer:
        session_ = true;               // the 0xee may have arrived even if the write failed
        if (!success) return stop(Status::device_status);
        phase_ = BlePhase::start;
        return Status::ok;
    case BlePhase::exit:
        if (!success) return stop(Status::device_status);
        session_ = false;              // the chip reset into its loop
        phase_ = BlePhase::regreeting;
        return Status::ok;
    case BlePhase::release:
        if (!success) return stop(Status::device_status);
        probes_ = 0;
        phase_ = BlePhase::verify;
        return Status::ok;
    case BlePhase::aborting:
        if (!success) return stop(Status::device_status);
        session_ = false;
        phase_ = BlePhase::aborted;
        return Status::ok;
    default: return Status::stopped;
    }
}

Status BleSession::accept(U32 command, const Byte* payload, U32 size) {
    switch (phase_) {
    case BlePhase::query:
    case BlePhase::enter:
    case BlePhase::probe:
    case BlePhase::greeting:
    case BlePhase::start:
    case BlePhase::header:
    case BlePhase::data:
    case BlePhase::finish:
    case BlePhase::regreeting:
    case BlePhase::verify: break;
    default: return Status::stopped;
    }
    if (command > 255 || !payload || !size || size > 249) return stop(Status::framing);
    if (phase_ == BlePhase::greeting || phase_ == BlePhase::regreeting) {
        if (command != 5 || size != 1 || payload[0] != 0xbb) return stop(Status::framing);
        phase_ = phase_ == BlePhase::greeting ? BlePhase::answer : BlePhase::release;
        return Status::ok;
    }
    if (phase_ == BlePhase::query || phase_ == BlePhase::probe || phase_ == BlePhase::verify) {
        if (command != 0x0a) return stop(Status::framing);
        const Byte* mode = 0;
        U32 mode_size = 0;
        Status result = ble_metadata_field(payload, size, "mode", mode, mode_size);
        if (result != Status::ok) return stop(result);
        const bool loop = text_equal(mode, mode_size, "bootloader");
        const bool normal = text_equal(mode, mode_size, "normal");
        if (!loop && !normal) return stop(Status::identity);
        if (phase_ == BlePhase::query) {
            phase_ = loop ? BlePhase::greeting : BlePhase::enter;
            return Status::ok;
        }
        if (phase_ == BlePhase::probe ? loop : normal) {
            if (phase_ == BlePhase::probe) {
                phase_ = BlePhase::greeting;
                return Status::ok;
            }
            const Byte* text = 0;
            U32 text_size = 0;
            U32 running[4];
            result = ble_metadata_field(payload, size, "sw_version", text, text_size);
            if (result == Status::ok) result = ble_parse_version(text, text_size, running);
            if (result != Status::ok) return stop(result);
            for (U32 i = 0; i < 4; ++i)
                if (running[i] != version_[i]) return stop(Status::verification);
            phase_ = BlePhase::complete;
            return Status::ok;
        }
        if (++probes_ == 20) return stop(Status::identity);
        return Status::ok;
    }
    // enter (the application's reply) and every session step: 0x54 or stop.
    if (command != 0x0e || payload[0] != 0x54) return stop(Status::device_status);
    switch (phase_) {
    case BlePhase::enter: phase_ = BlePhase::settle; break;
    case BlePhase::start: phase_ = BlePhase::header; break;
    case BlePhase::header: phase_ = BlePhase::data; break;
    case BlePhase::data:
        offset_ += minimum(size_ - offset_, 248);
        if (offset_ == size_) phase_ = BlePhase::finish;
        break;
    default:                           // finish: size and CRC-16 checked, application valid
        valid_ = true;
        phase_ = BlePhase::exit;
        break;
    }
    return Status::ok;
}

Status BleSession::transport_failed() {
    switch (phase_) {
    case BlePhase::uninitialized:
    case BlePhase::complete:
    case BlePhase::aborted:
    case BlePhase::failed: return Status::stopped;
    case BlePhase::verify:             // read-only polls, at most 20 as stock
        if (++probes_ == 20) return stop(Status::identity);
        return Status::ok;
    default: return stop(Status::device_status);
    }
}

Status BleSession::abort() {
    if (!session_ || phase_ == BlePhase::aborting || phase_ == BlePhase::aborted) return Status::stopped;
    phase_ = BlePhase::aborting;
    return Status::ok;
}

}}

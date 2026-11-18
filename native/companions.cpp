#include "companions.hpp"

namespace xyf_core { namespace companion {
namespace {
U32 minimum(U32 a, U32 b) { return a < b ? a : b; }
U32 read16(const Byte* p) { return U32(p[0]) | U32(p[1]) << 8; }
U32 read32(const Byte* p) {
    return U32(p[0]) | U32(p[1]) << 8 | U32(p[2]) << 16 | U32(p[3]) << 24;
}
void little16(Byte* p, U32 x) { p[0] = Byte(x); p[1] = Byte(x >> 8); }
void big16(Byte* p, U32 x) { p[0] = Byte(x >> 8); p[1] = Byte(x); }
void big32(Byte* p, U32 x) {
    p[0] = Byte(x >> 24); p[1] = Byte(x >> 16); p[2] = Byte(x >> 8); p[3] = Byte(x);
}
void copy(Byte* out, const Byte* in, U32 size) {
    for (U32 i = 0; i < size; ++i) out[i] = in[i];
}
U32 sum(const Byte* p, U32 size) {
    U32 result = 0;
    for (U32 i = 0; i < size; ++i) result += p[i];
    return result;
}
bool version_valid(const U32* version) {
    if (!version) return false;
    for (U32 i = 0; i < 4; ++i) if (version[i] > 0xffffu) return false;
    return true;
}
bool image_size_valid(U32 size) { return size >= 8 && size <= 0x70000 && !(size & 3); }
void version_write(Byte* out, const U32* version) {
    for (U32 i = 0; i < 4; ++i) big16(out + 2 * i, version[i]);
}
}

Status cypress_encode(U32 command, const Byte* payload, U32 size,
                      Byte* output, U32 capacity, U32& produced) {
    produced = 0;
    if (command > 255 || size > 505 || (!payload && size) || !output)
        return Status::argument;
    if (capacity < size + 7) return Status::capacity;
    output[0] = 1; output[1] = Byte(command);
    little16(output + 2, size);
    copy(output + 4, payload, size);
    little16(output + size + 4, (0u - sum(output, size + 4)) & 0xffffu);
    output[size + 6] = 0x17;
    produced = size + 7;
    return Status::ok;
}

Status cypress_decode(const Byte* packet, U32 size, U32 expected_size,
                      const Byte*& payload) {
    payload = 0;
    if (!packet) return Status::argument;
    if (size < 7 || packet[0] != 1 || packet[size - 1] != 0x17) return Status::framing;
    const U32 data_size = read16(packet + 2);
    if (data_size != expected_size || size != data_size + 7) return Status::length;
    if (read16(packet + size - 3) != ((0u - sum(packet, size - 3)) & 0xffffu))
        return Status::checksum;
    if (packet[1]) return Status::device_status;
    payload = packet + 4;
    return Status::ok;
}

Status crc16_ccitt(const Byte* data, U32 size, U32 initial, U32& result) {
    result = 0;
    if ((!data && size) || initial > 0xffffu) return Status::argument;
    U32 crc = initial;
    for (U32 i = 0; i < size; ++i) {
        crc ^= U32(data[i]) << 8;
        for (U32 bit = 0; bit < 8; ++bit)
            crc = ((crc << 1) ^ ((crc & 0x8000u) ? 0x1021u : 0u)) & 0xffffu;
    }
    result = crc;
    return Status::ok;
}

Status ble_header(U32 image_size, const U32* version, U32 crc, U32 device_id,
                  Byte* output, U32 capacity, U32& produced) {
    produced = 0;
    if (!image_size_valid(image_size) || !version_valid(version) || crc > 0xffffu || !output)
        return Status::argument;
    if (capacity < 64) return Status::capacity;
    for (U32 i = 0; i < 64; ++i) output[i] = 0;
    big32(output, 0xbeefcafeu); output[4] = 3;
    // 1.2.1 Nordic bootloader: CRC-16 at 9-10, version at 11-18.
    big32(output + 5, image_size); big16(output + 9, crc);
    version_write(output + 11, version); big32(output + 19, 0x1f000u); big32(output + 23, device_id);
    produced = 64;
    return Status::ok;
}

Status ble_start(const U32* version, U32 image_kind, U32 device_id,
                 Byte* output, U32 capacity, U32& produced) {
    produced = 0;
    if (!version_valid(version) || image_kind > 255 || !output) return Status::argument;
    if (capacity < 14) return Status::capacity;
    output[0] = 2; version_write(output + 1, version);
    output[9] = Byte(image_kind); big32(output + 10, device_id);
    produced = 14;
    return Status::ok;
}

Status ble_chunk(const Byte* image, U32 image_size, U32 offset,
                 Byte* output, U32 capacity, U32& produced, U32& consumed) {
    produced = 0; consumed = 0;
    if (!image || !image_size_valid(image_size) || offset >= image_size || !output)
        return Status::argument;
    const U32 count = minimum(image_size - offset, 248);
    if (capacity < count + 1) return Status::capacity;
    output[0] = 3; copy(output + 1, image + offset, count);
    produced = count + 1; consumed = count;
    return Status::ok;
}

CypressSession::CypressSession()
    : rows_(0), count_(0), transfer_size_(0), row_(0), offset_(0), phase_(Phase::failed) {}

Status CypressSession::initialize(const CypressRow* rows, U32 count, U32 transfer_size) {
    rows_ = 0; count_ = 0; transfer_size_ = 0; row_ = 0; offset_ = 0; phase_ = Phase::failed;
    if (!rows || count != 367 || transfer_size < 11 || transfer_size > 512) return Status::layout;
    for (U32 i = 0; i < count; ++i) {
        const U32 expected = i < 366 ? 25 + i : 511;
        if (rows[i].array_id || rows[i].number != expected || !rows[i].data || rows[i].size != 256)
            return Status::layout;
    }
    rows_ = rows; count_ = count; transfer_size_ = transfer_size; phase_ = Phase::enter;
    return Status::ok;
}

void CypressSession::select_row_phase() {
    phase_ = rows_[row_].size - offset_ > transfer_size_ - 10 ? Phase::buffer : Phase::program;
}

Status CypressSession::request(Byte* output, U32 capacity, U32& produced) const {
    produced = 0;
    if (phase_ == Phase::failed || phase_ == Phase::complete) return Status::stopped;
    if (phase_ == Phase::enter) return cypress_encode(0x38, 0, 0, output, capacity, produced);
    if (phase_ == Phase::bounds) {
        const Byte array = 0;
        return cypress_encode(0x32, &array, 1, output, capacity, produced);
    }
    if (phase_ == Phase::application) return cypress_encode(0x31, 0, 0, output, capacity, produced);
    const CypressRow& row = rows_[row_];
    if (phase_ == Phase::buffer) {
        const U32 count = minimum(row.size - offset_, transfer_size_ - 7);
        return cypress_encode(0x37, row.data + offset_, count, output, capacity, produced);
    }
    Byte payload[259];
    payload[0] = Byte(row.array_id); little16(payload + 1, row.number);
    if (phase_ == Phase::verify) return cypress_encode(0x3a, payload, 3, output, capacity, produced);
    const U32 count = row.size - offset_;
    copy(payload + 3, row.data + offset_, count);
    return cypress_encode(0x39, payload, count + 3, output, capacity, produced);
}

Status CypressSession::accept(const Byte* packet, U32 size) {
    if (phase_ == Phase::failed || phase_ == Phase::complete) return Status::stopped;
    const U32 expected = phase_ == Phase::enter ? 8 : phase_ == Phase::bounds ? 4 :
        (phase_ == Phase::verify || phase_ == Phase::application) ? 1 : 0;
    const Byte* payload = 0;
    Status result = cypress_decode(packet, size, expected, payload);
    if (result != Status::ok) { phase_ = Phase::failed; return result; }
    switch (phase_) {
    case Phase::enter:
        if (read32(payload) != 0x100a11a0u || payload[4] || payload[7] != 1) result = Status::identity;
        else phase_ = Phase::bounds;
        break;
    case Phase::bounds: {
        const U32 first = read16(payload), last = read16(payload + 2);
        if (first > last || first > 25 || last < 511) result = Status::bounds;
        else select_row_phase();
        break;
    }
    case Phase::buffer:
        offset_ += minimum(rows_[row_].size - offset_, transfer_size_ - 7);
        select_row_phase();
        break;
    case Phase::program:
        phase_ = Phase::verify;
        break;
    case Phase::verify:
        if (payload[0] != Byte(0u - sum(rows_[row_].data, rows_[row_].size))) result = Status::verification;
        else {
            ++row_; offset_ = 0;
            if (row_ == count_) phase_ = Phase::application;
            else select_row_phase();
        }
        break;
    case Phase::application:
        if (!payload[0]) result = Status::verification;
        else phase_ = Phase::complete;
        break;
    default:
        result = Status::stopped;
        break;
    }
    if (result != Status::ok) phase_ = Phase::failed;
    return result;
}

Status CypressSession::exit_request(Byte* output, U32 capacity, U32& produced) const {
    produced = 0;
    if (phase_ != Phase::complete) return Status::stopped;
    return cypress_encode(0x3b, 0, 0, output, capacity, produced);
}

} }

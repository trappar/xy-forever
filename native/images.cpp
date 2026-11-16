#include "images.hpp"

namespace xyf_core { namespace images {
namespace {
bool line(const Byte* data, U32 size, U32& at, U32& begin, U32& count) {
    while (at < size) {
        begin = at;
        while (at < size && data[at] != '\r' && data[at] != '\n') ++at;
        count = at - begin;
        if (at < size && data[at] == '\r') ++at;
        if (at < size && data[at] == '\n') ++at;
        if (count && data[begin] != '#') return true;
    }
    return false;
}
int nibble(Byte byte) {
    if (byte >= '0' && byte <= '9') return byte - '0';
    if (byte >= 'a' && byte <= 'f') return byte - 'a' + 10;
    if (byte >= 'A' && byte <= 'F') return byte - 'A' + 10;
    return -1;
}
bool hex(const Byte* data, U32 offset, Byte& value) {
    int high = nibble(data[offset]), low = nibble(data[offset + 1]);
    if (high < 0 || low < 0) return false;
    value = static_cast<Byte>((high << 4) | low);
    return true;
}
U32 word(const Byte* data) {
    return U32(data[0]) | (U32(data[1]) << 8) | (U32(data[2]) << 16) | (U32(data[3]) << 24);
}
}

Status inspect_cyacd(const Byte* data, U32 size, CyacdSummary& result) {
    result.silicon_id = 0; result.revision = 0; result.checksum_type = 0; result.rows = 0;
    if (!data) return Status::argument;
    if (!size || size > 0x30000u) return Status::size;
    for (U32 i = 0; i != size; ++i) if (data[i] >= 128) return Status::encoding;
    U32 at = 0, begin, count;
    if (!line(data, size, at, begin, count) || count != 12) return Status::header;
    Byte header[6];
    for (U32 i = 0; i != 6; ++i) if (!hex(data, begin + i * 2, header[i])) return Status::encoding;
    U32 silicon = (U32(header[0]) << 24) | (U32(header[1]) << 16) | (U32(header[2]) << 8) | header[3];
    if (silicon != 0x100a11a0u || header[4] || header[5]) return Status::header;
    U32 rows = 0;
    while (line(data, size, at, begin, count)) {
        if (rows >= 367 || count != 525 || data[begin] != ':') return Status::layout;
        Byte prefix[5];
        for (U32 i = 0; i != 5; ++i) if (!hex(data, begin + 1 + i * 2, prefix[i])) return Status::encoding;
        U32 number = (U32(prefix[1]) << 8) | prefix[2], length = (U32(prefix[3]) << 8) | prefix[4];
        if (prefix[0] || number != (rows < 366 ? rows + 25 : 511) || length != 256) return Status::layout;
        U32 sum = 0;
        for (U32 i = 0; i != 262; ++i) {
            Byte value;
            if (!hex(data, begin + 1 + i * 2, value)) return Status::encoding;
            sum += value;
        }
        if (sum & 255u) return Status::checksum;
        ++rows;
    }
    if (rows != 367) return Status::layout;
    result.silicon_id = silicon; result.revision = header[4]; result.checksum_type = header[5]; result.rows = rows;
    return Status::ok;
}

Status cyacd_row(const Byte* data, U32 size, U32 index, Byte* output, U32 capacity, companion::CypressRow& result) {
    result.array_id = 0; result.number = 0; result.data = 0; result.size = 0;
    if (!output || capacity < 256 || index >= 367) return Status::argument;
    CyacdSummary summary;
    Status status = inspect_cyacd(data, size, summary);
    if (status != Status::ok) return status;
    U32 at = 0, begin = 0, count;
    if (!line(data, size, at, begin, count)) return Status::header;
    for (U32 row = 0; row <= index; ++row)
        if (!line(data, size, at, begin, count)) return Status::layout;
    for (U32 i = 0; i != 256; ++i) {
        Byte value;
        if (!hex(data, begin + 11 + i * 2, value)) return Status::encoding;
        output[i] = value;
    }
    result.array_id = 0; result.number = index < 366 ? index + 25 : 511;
    result.data = output; result.size = 256;
    return Status::ok;
}

Status inspect_ble(const Byte* data, U32 size, BleSummary& result) {
    result = BleSummary{0,0,0};
    if (!data) return Status::argument;
    if (size < 8 || size > 0x70000u || (size & 3u)) return Status::size;
    U32 stack = word(data), reset = word(data + 4), crc;
    if ((stack & 7u) || stack <= 0x20000000u || stack > 0x20040000u ||
        !(reset & 1u) || reset < 0x10000u || reset >= 0x100000u) return Status::vectors;
    if (companion::crc16_ccitt(data, size, 0, crc) != companion::Status::ok) return Status::argument;
    result.stack = stack; result.reset = reset; result.crc16 = crc;
    return Status::ok;
}
} }

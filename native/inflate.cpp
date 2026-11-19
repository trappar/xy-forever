// Original bounded decoder implementing RFC 1951 (https://www.rfc-editor.org/rfc/rfc1951).
// No source is copied from zlib, puff, miniz or another implementation.
#include "inflate.hpp"
#if defined(XYF_SMALL_CRC)
#include "crc32_small.hpp"
#endif

namespace xyf_core { namespace zip {
namespace {
struct Bits {
    const Reader& reader;
    InflateWorkspace& workspace;
    U32 start, size, consumed, buffered, cursor, value, count;
    Status status;
    Bits(const Reader& r, InflateWorkspace& w, U32 offset, U32 length)
        : reader(r), workspace(w), start(offset), size(length), consumed(0),
          buffered(0), cursor(0), value(0), count(0), status(Status::ok) {}
    bool take(U32 n, U32& result) {
        while (count < n) {
            if (consumed == size) { status = Status::truncated; return false; }
            if (cursor == buffered) {
                buffered = size - consumed;
                if (buffered > sizeof(workspace.input)) buffered = sizeof(workspace.input);
                if (!reader.read(reader.context, start + consumed, workspace.input, buffered)) {
                    status = Status::io; return false;
                }
                cursor = 0;
            }
            value |= static_cast<U32>(workspace.input[cursor++]) << count;
            ++consumed; count += 8;
        }
        result = value & ((1u << n) - 1u);
        value >>= n; count -= n;
        return true;
    }
    void align() { value = 0; count = 0; }
};

bool build(Huffman& tree, const Byte* lengths, U32 symbols, bool allow_empty, bool require_complete) {
    for (U32 i = 0; i != 16; ++i) { tree.count[i] = 0; tree.first[i] = 0; tree.offset[i] = 0; }
    U32 used = 0, maximum = 0;
    for (U32 i = 0; i != symbols; ++i) {
        if (lengths[i] > 15) return false;
        if (lengths[i]) { ++tree.count[lengths[i]]; ++used; if (lengths[i] > maximum) maximum = lengths[i]; }
    }
    if (!used) return allow_empty;
    int remaining = 1;
    U32 offset = 0;
    for (U32 length = 1; length != 16; ++length) {
        remaining = remaining * 2 - static_cast<int>(tree.count[length]);
        if (remaining < 0) return false;
        tree.first[length] = (tree.first[length - 1] + tree.count[length - 1]) << 1;
        tree.offset[length] = offset;
        offset += tree.count[length];
    }
    if (remaining && (require_complete || used != 1 || maximum != 1)) return false;
    U32 next[16];
    for (U32 i = 0; i != 16; ++i) next[i] = tree.offset[i];
    for (U32 i = 0; i != symbols; ++i)
        if (lengths[i]) tree.symbols[next[lengths[i]]++] = i;
    return true;
}

bool symbol(Bits& bits, const Huffman& tree, U32& result) {
    U32 code = 0;
    for (U32 length = 1; length != 16; ++length) {
        U32 bit;
        if (!bits.take(1, bit)) return false;
        code = (code << 1) | bit;
        if (code >= tree.first[length] && code - tree.first[length] < tree.count[length]) {
            result = tree.symbols[tree.offset[length] + code - tree.first[length]];
            return true;
        }
    }
    bits.status = Status::deflate;
    return false;
}

struct Output {
    InflateWorkspace& workspace;
    const Sink& sink;
    U32 expected, produced, crc;
    Status status;
    Output(InflateWorkspace& w, const Sink& s, U32 size)
        : workspace(w), sink(s), expected(size), produced(0), crc(0xffffffffu), status(Status::ok) {}
    bool byte(Byte value) {
        if (produced == expected) { status = Status::bounds; return false; }
        workspace.window[produced & 32767u] = value;
        crc = crc32_update(crc, &value, 1);
        ++produced;
        if (!(produced & 32767u) && sink.write && !sink.write(sink.context, workspace.window, 32768)) {
            status = Status::sink; return false;
        }
        return true;
    }
    bool finish() {
        if (produced != expected) { status = Status::bounds; return false; }
        U32 tail = produced & 32767u;
        if (tail && sink.write && !sink.write(sink.context, workspace.window, tail)) {
            status = Status::sink; return false;
        }
        return true;
    }
};

bool fixed(InflateWorkspace& workspace) {
    for (U32 i = 0; i != 288; ++i)
        workspace.lengths[i] = static_cast<Byte>(i < 144 ? 8 : i < 256 ? 9 : i < 280 ? 7 : 8);
    if (!build(workspace.literal, workspace.lengths, 288, false, true)) return false;
    for (U32 i = 0; i != 32; ++i) workspace.lengths[i] = 5;
    return build(workspace.distance, workspace.lengths, 32, false, true);
}

bool dynamic(Bits& bits, InflateWorkspace& workspace) {
    U32 literals, distances, codes;
    if (!bits.take(5, literals) || !bits.take(5, distances) || !bits.take(4, codes)) return false;
    literals += 257; ++distances; codes += 4;
    if (literals > 286) { bits.status = Status::deflate; return false; }
    static const Byte order[19] = {16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15};
    for (U32 i = 0; i != 19; ++i) workspace.lengths[i] = 0;
    for (U32 i = 0; i != codes; ++i) {
        U32 length;
        if (!bits.take(3, length)) return false;
        workspace.lengths[order[i]] = static_cast<Byte>(length);
    }
    if (!build(workspace.code, workspace.lengths, 19, false, true)) {
        bits.status = Status::deflate; return false;
    }
    U32 total = literals + distances, index = 0;
    while (index < total) {
        U32 code;
        if (!symbol(bits, workspace.code, code)) return false;
        if (code < 16) workspace.lengths[index++] = static_cast<Byte>(code);
        else {
            U32 count, value = 0;
            if (code == 16) {
                if (!index) { bits.status = Status::deflate; return false; }
                value = workspace.lengths[index - 1];
                if (!bits.take(2, count)) return false;
                count += 3;
            } else if (code == 17) {
                if (!bits.take(3, count)) return false;
                count += 3;
            } else if (code == 18) {
                if (!bits.take(7, count)) return false;
                count += 11;
            } else { bits.status = Status::deflate; return false; }
            if (count > total - index) { bits.status = Status::deflate; return false; }
            while (count--) workspace.lengths[index++] = static_cast<Byte>(value);
        }
    }
    if (!workspace.lengths[256] ||
        !build(workspace.literal, workspace.lengths, literals, false, false) ||
        !build(workspace.distance, workspace.lengths + literals, distances, true, false)) {
        bits.status = Status::deflate; return false;
    }
    return true;
}

bool compressed(Bits& bits, Output& output, InflateWorkspace& workspace) {
    static const unsigned short length_base[29] = {
        3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258
    };
    static const Byte length_extra[29] = {
        0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0
    };
    static const unsigned short distance_base[30] = {
        1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577
    };
    static const Byte distance_extra[30] = {
        0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13
    };
    for (;;) {
        U32 code;
        if (!symbol(bits, workspace.literal, code)) return false;
        if (code < 256) { if (!output.byte(static_cast<Byte>(code))) return false; continue; }
        if (code == 256) return true;
        if (code > 285) { bits.status = Status::deflate; return false; }
        U32 extra, length_index = code - 257;
        if (!bits.take(length_extra[length_index], extra)) return false;
        U32 length = length_base[length_index] + extra;
        if (!symbol(bits, workspace.distance, code)) return false;
        if (code > 29) { bits.status = Status::deflate; return false; }
        if (!bits.take(distance_extra[code], extra)) return false;
        U32 distance = distance_base[code] + extra;
        if (distance > output.produced || length > output.expected - output.produced) {
            bits.status = Status::bounds; return false;
        }
        for (U32 i = 0; i != length; ++i) {
            Byte value = workspace.window[(output.produced - distance) & 32767u];
            if (!output.byte(value)) return false;
        }
    }
}
}

// Running (pre-inverted) CRC-32 state. Boot images are checked from uncached memory:
// aligned input is read a word at a time (byte table, one data load per 4 bytes).
static_assert(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__, "word loads assume little-endian");
#if defined(XYF_SMALL_CRC)
// Narrow (48 KiB) cold layouts: the 64-byte nibble table instead of the 1 KiB byte table.
U32 crc32_update(U32 running, const Byte* data, U32 count) {
    return crc::small_update(running, data, count);
}
#else
U32 crc32_update(U32 running, const Byte* data, U32 count) {
    static const U32 table[256] = {
        0x00000000u,0x77073096u,0xee0e612cu,0x990951bau,0x076dc419u,0x706af48fu,0xe963a535u,0x9e6495a3u,
        0x0edb8832u,0x79dcb8a4u,0xe0d5e91eu,0x97d2d988u,0x09b64c2bu,0x7eb17cbdu,0xe7b82d07u,0x90bf1d91u,
        0x1db71064u,0x6ab020f2u,0xf3b97148u,0x84be41deu,0x1adad47du,0x6ddde4ebu,0xf4d4b551u,0x83d385c7u,
        0x136c9856u,0x646ba8c0u,0xfd62f97au,0x8a65c9ecu,0x14015c4fu,0x63066cd9u,0xfa0f3d63u,0x8d080df5u,
        0x3b6e20c8u,0x4c69105eu,0xd56041e4u,0xa2677172u,0x3c03e4d1u,0x4b04d447u,0xd20d85fdu,0xa50ab56bu,
        0x35b5a8fau,0x42b2986cu,0xdbbbc9d6u,0xacbcf940u,0x32d86ce3u,0x45df5c75u,0xdcd60dcfu,0xabd13d59u,
        0x26d930acu,0x51de003au,0xc8d75180u,0xbfd06116u,0x21b4f4b5u,0x56b3c423u,0xcfba9599u,0xb8bda50fu,
        0x2802b89eu,0x5f058808u,0xc60cd9b2u,0xb10be924u,0x2f6f7c87u,0x58684c11u,0xc1611dabu,0xb6662d3du,
        0x76dc4190u,0x01db7106u,0x98d220bcu,0xefd5102au,0x71b18589u,0x06b6b51fu,0x9fbfe4a5u,0xe8b8d433u,
        0x7807c9a2u,0x0f00f934u,0x9609a88eu,0xe10e9818u,0x7f6a0dbbu,0x086d3d2du,0x91646c97u,0xe6635c01u,
        0x6b6b51f4u,0x1c6c6162u,0x856530d8u,0xf262004eu,0x6c0695edu,0x1b01a57bu,0x8208f4c1u,0xf50fc457u,
        0x65b0d9c6u,0x12b7e950u,0x8bbeb8eau,0xfcb9887cu,0x62dd1ddfu,0x15da2d49u,0x8cd37cf3u,0xfbd44c65u,
        0x4db26158u,0x3ab551ceu,0xa3bc0074u,0xd4bb30e2u,0x4adfa541u,0x3dd895d7u,0xa4d1c46du,0xd3d6f4fbu,
        0x4369e96au,0x346ed9fcu,0xad678846u,0xda60b8d0u,0x44042d73u,0x33031de5u,0xaa0a4c5fu,0xdd0d7cc9u,
        0x5005713cu,0x270241aau,0xbe0b1010u,0xc90c2086u,0x5768b525u,0x206f85b3u,0xb966d409u,0xce61e49fu,
        0x5edef90eu,0x29d9c998u,0xb0d09822u,0xc7d7a8b4u,0x59b33d17u,0x2eb40d81u,0xb7bd5c3bu,0xc0ba6cadu,
        0xedb88320u,0x9abfb3b6u,0x03b6e20cu,0x74b1d29au,0xead54739u,0x9dd277afu,0x04db2615u,0x73dc1683u,
        0xe3630b12u,0x94643b84u,0x0d6d6a3eu,0x7a6a5aa8u,0xe40ecf0bu,0x9309ff9du,0x0a00ae27u,0x7d079eb1u,
        0xf00f9344u,0x8708a3d2u,0x1e01f268u,0x6906c2feu,0xf762575du,0x806567cbu,0x196c3671u,0x6e6b06e7u,
        0xfed41b76u,0x89d32be0u,0x10da7a5au,0x67dd4accu,0xf9b9df6fu,0x8ebeeff9u,0x17b7be43u,0x60b08ed5u,
        0xd6d6a3e8u,0xa1d1937eu,0x38d8c2c4u,0x4fdff252u,0xd1bb67f1u,0xa6bc5767u,0x3fb506ddu,0x48b2364bu,
        0xd80d2bdau,0xaf0a1b4cu,0x36034af6u,0x41047a60u,0xdf60efc3u,0xa867df55u,0x316e8eefu,0x4669be79u,
        0xcb61b38cu,0xbc66831au,0x256fd2a0u,0x5268e236u,0xcc0c7795u,0xbb0b4703u,0x220216b9u,0x5505262fu,
        0xc5ba3bbeu,0xb2bd0b28u,0x2bb45a92u,0x5cb36a04u,0xc2d7ffa7u,0xb5d0cf31u,0x2cd99e8bu,0x5bdeae1du,
        0x9b64c2b0u,0xec63f226u,0x756aa39cu,0x026d930au,0x9c0906a9u,0xeb0e363fu,0x72076785u,0x05005713u,
        0x95bf4a82u,0xe2b87a14u,0x7bb12baeu,0x0cb61b38u,0x92d28e9bu,0xe5d5be0du,0x7cdcefb7u,0x0bdbdf21u,
        0x86d3d2d4u,0xf1d4e242u,0x68ddb3f8u,0x1fda836eu,0x81be16cdu,0xf6b9265bu,0x6fb077e1u,0x18b74777u,
        0x88085ae6u,0xff0f6a70u,0x66063bcau,0x11010b5cu,0x8f659effu,0xf862ae69u,0x616bffd3u,0x166ccf45u,
        0xa00ae278u,0xd70dd2eeu,0x4e048354u,0x3903b3c2u,0xa7672661u,0xd06016f7u,0x4969474du,0x3e6e77dbu,
        0xaed16a4au,0xd9d65adcu,0x40df0b66u,0x37d83bf0u,0xa9bcae53u,0xdebb9ec5u,0x47b2cf7fu,0x30b5ffe9u,
        0xbdbdf21cu,0xcabac28au,0x53b39330u,0x24b4a3a6u,0xbad03605u,0xcdd70693u,0x54de5729u,0x23d967bfu,
        0xb3667a2eu,0xc4614ab8u,0x5d681b02u,0x2a6f2b94u,0xb40bbe37u,0xc30c8ea1u,0x5a05df1bu,0x2d02ef8du
    };
    typedef U32 __attribute__((may_alias)) Word;
    U32 i = 0;
    for (; i != count && (reinterpret_cast<unsigned long>(data + i) & 3u); ++i)
        running = table[(running ^ data[i]) & 0xffu] ^ (running >> 8);
    for (; count - i >= 4; i += 4) {
        running ^= *reinterpret_cast<const Word*>(data + i);
        running = table[running & 0xffu] ^ (running >> 8); running = table[running & 0xffu] ^ (running >> 8);
        running = table[running & 0xffu] ^ (running >> 8); running = table[running & 0xffu] ^ (running >> 8);
    }
    for (; i != count; ++i) running = table[(running ^ data[i]) & 0xffu] ^ (running >> 8);
    return running;
}
#endif

Status inflate_raw(const Reader& reader, U32 offset, U32 compressed_size,
                   U32 expected_size, InflateWorkspace& workspace,
                   const Sink& sink, Extraction& result) {
    result = Extraction{0,0,0};
    if (!reader.read) return Status::argument;
    if (offset > reader.size || compressed_size > reader.size - offset) return Status::bounds;
    Bits bits(reader, workspace, offset, compressed_size);
    Output output(workspace, sink, expected_size);
    U32 final = 0;
    while (!final) {
        U32 kind;
        if (!bits.take(1, final) || !bits.take(2, kind)) return bits.status;
        if (!kind) {
            bits.align();
            U32 length, complement;
            if (!bits.take(16, length) || !bits.take(16, complement)) return bits.status;
            if ((length ^ complement) != 65535u) return Status::deflate;
            if (length > expected_size - output.produced) return Status::bounds;
            for (U32 i = 0; i != length; ++i) {
                U32 byte;
                if (!bits.take(8, byte)) return bits.status;
                if (!output.byte(static_cast<Byte>(byte))) return output.status;
            }
        } else if (kind == 3) return Status::deflate;
        else {
            if (kind == 1) {
                if (!fixed(workspace)) return Status::deflate;
            } else if (!dynamic(bits, workspace)) return bits.status;
            if (!compressed(bits, output, workspace))
                return bits.status != Status::ok ? bits.status : output.status;
        }
    }
    if (bits.consumed != compressed_size) return Status::deflate;
    if (!output.finish()) return output.status;
    result = Extraction{output.produced, output.crc ^ 0xffffffffu, bits.consumed};
    return Status::ok;
}

const char* status_name(Status status) {
    switch (status) {
    case Status::ok: return "ok";
    case Status::argument: return "argument";
    case Status::io: return "io";
    case Status::truncated: return "truncated";
    case Status::unsupported: return "unsupported";
    case Status::layout: return "layout";
    case Status::name: return "name";
    case Status::duplicate: return "duplicate";
    case Status::bounds: return "bounds";
    case Status::capacity: return "capacity";
    case Status::deflate: return "deflate";
    case Status::checksum: return "checksum";
    case Status::sink: return "sink";
    }
    return "unknown";
}
} }

// Original bounded decoder implementing RFC 1951 (https://www.rfc-editor.org/rfc/rfc1951).
// No source is copied from zlib, puff, miniz or another implementation.
#include "inflate.hpp"
#if defined(XYF_SMALL_CRC)
#include "crc32_small.hpp"
#endif

namespace xyf_core { namespace zip {
namespace {
// #279: the root tables' bits (Huffman::table): the longest code is 15 bits, so a second table needs at most
// 15 - root bits, which fit an entry's three (the code-length code's longest is 7).
const U32 literal_root = 10, distance_root = 8, code_root = 7;
struct Bits {
    const Reader& reader;
    InflateWorkspace& workspace;
    Work* work;
    U32 start, size, consumed, buffered, cursor, value, count;
    Status status;
    Bits(const Reader& r, InflateWorkspace& w, U32 offset, U32 length, Work* service)
        : reader(r), workspace(w), work(service), start(offset), size(length), consumed(0), buffered(0), cursor(0),
          value(0), count(0), status(Status::ok) {}
    // #279: the input buffer's unread bytes moved to its start, then filled behind them from the reader, to a 16-byte
    // boundary of the input where it can (an AES reader then decrypts no block twice); false with status set.
    bool refill() {
        if (!checkpoint(work)) {
            status = Status::cancelled;
            return false;
        }
        const U32 kept = buffered - cursor, at = start + consumed + kept;
        for (U32 i = 0; i != kept; ++i) workspace.input[i] = workspace.input[cursor + i];
        U32 more = size - consumed - kept;
        if (more > sizeof(workspace.input) - kept) {
            more = sizeof(workspace.input) - kept;
            if (more > ((at + more) & 15u)) more -= (at + more) & 15u;
        }
        cursor = 0;
        buffered = kept + more;
        if (more && !reader.read(reader.context, at, workspace.input + kept, more)) {
            status = cancelled(work) ? Status::cancelled : Status::io;
            return false;
        }
        return true;
    }
    // One more input byte into value (count < 25), refilling the input buffer; false with status set.
    bool load() {
        if (consumed == size) {
            status = Status::truncated;
            return false;
        }
        if (cursor == buffered && !refill()) return false;
        value |= static_cast<U32>(workspace.input[cursor++]) << count;
        ++consumed;
        count += 8;
        return true;
    }
    bool take(U32 n, U32& result) {
        while (count < n)
            if (!load()) return false;
        result = value & ((1u << n) - 1u);
        value >>= n;
        count -= n;
        return true;
    }
    // #279: at least n (<= 16) bits buffered when the input has them; false only for a read failure or a cancel.
    // Bytes buffered ahead of what was taken are not consumed: used() leaves them out.
    bool fill(U32 n) {
        while (count < n && consumed != size)
            if (!load()) return false;
        return true;
    }
    U32 used() const { return consumed - (count >> 3); }
    void align() {                                         // the rest of the current byte dropped
        value >>= count & 7u;
        count &= ~7u;
    }
};

// A code's bits arrive first bit first: its table index is the code reversed.
U32 reversed(U32 code, U32 length) {
    U32 result = 0;
    for (U32 bit = 0; bit != length; ++bit) result |= ((code >> bit) & 1u) << (length - 1 - bit);
    return result;
}

// #279: the look-up tables (Huffman::table). root: the root table's bits; codes up to that long take one look-up,
// longer ones a second (a table per root entry they share, as large as the longest of them needs).
void tables(Huffman& tree, U32 root) {
    const U32 root_size = 1u << root, mark = 0x4000u;     // mark | bits: the second table's size, while counting
    tree.root = root;
    for (U32 i = 0; i != root_size; ++i) tree.table[i] = 0;
    for (U32 length = 1; length != 16; ++length)
        for (U32 k = 0; k != tree.count[length]; ++k) {
            const U32 index = reversed(tree.first[length] + k, length);
            const U32 symbol = tree.symbols[tree.offset[length] + k];
            if (length <= root) {
                for (U32 at = index; at < root_size; at += 1u << length)
                    tree.table[at] = static_cast<unsigned short>(symbol << 4 | length);
            } else {
                unsigned short& entry = tree.table[index & (root_size - 1u)];
                if (!entry || ((entry & mark) && (entry & 7u) < length - root))
                    entry = static_cast<unsigned short>(mark | (length - root));
            }
        }
    U32 next = root_size;
    for (U32 i = 0; i != root_size; ++i) {
        const U32 entry = tree.table[i];
        if (!(entry & mark)) continue;
        const U32 size = 1u << (entry & 7u);
        if (size > table_entries - next) {                 // no room: the careful decoder takes these codes
            tree.table[i] = 0;
            continue;
        }
        tree.table[i] = static_cast<unsigned short>(table_link | next << 3 | (entry & 7u));
        for (U32 at = 0; at != size; ++at) tree.table[next + at] = 0;
        next += size;
    }
    for (U32 length = root + 1; length < 16; ++length)
        for (U32 k = 0; k != tree.count[length]; ++k) {
            const U32 index = reversed(tree.first[length] + k, length);
            const U32 entry = tree.table[index & (root_size - 1u)];
            if (!(entry & table_link)) continue;
            const U32 symbol = tree.symbols[tree.offset[length] + k], second = entry >> 3 & 0xfffu;
            for (U32 at = index >> root; at < (1u << (entry & 7u)); at += 1u << (length - root))
                tree.table[second + at] = static_cast<unsigned short>(symbol << 4 | length);
        }
}

bool build(Huffman& tree, const Byte* lengths, U32 symbols, bool allow_empty, bool require_complete, U32 root) {
    for (U32 i = 0; i != 16; ++i) {
        tree.count[i] = 0;
        tree.first[i] = 0;
        tree.offset[i] = 0;
    }
    U32 used = 0, maximum = 0;
    for (U32 i = 0; i != symbols; ++i) {
        if (lengths[i] > 15) return false;
        if (lengths[i]) {
            ++tree.count[lengths[i]];
            ++used;
            if (lengths[i] > maximum) maximum = lengths[i];
        }
    }
    if (!used) {
        tree.root = root;
        for (U32 i = 0; i != (1u << root); ++i) tree.table[i] = 0;
        return allow_empty;
    }
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
        if (lengths[i]) tree.symbols[next[lengths[i]]++] = static_cast<unsigned short>(i);
    tables(tree, root);
    return true;
}

// The table entry for the next input bits, or 0 (also when fewer bits are buffered than the look-up needs).
U32 look_up(const Huffman& tree, U32 value, U32 count) {
    if (count < tree.root) return 0;
    U32 entry = tree.table[value & ((1u << tree.root) - 1u)];
    if (entry & table_link) {
        const U32 bits = entry & 7u;
        entry = count < tree.root + bits ?
                    0 :
                    tree.table[(entry >> 3 & 0xfffu) + (value >> tree.root & ((1u << bits) - 1u))];
    }
    return entry;
}

bool symbol(Bits& bits, const Huffman& tree, U32& result) {
    if (!bits.fill(15)) return false;
    const U32 entry = look_up(tree, bits.value, bits.count);
    if (entry) {                                           // #279: a code in one or two look-ups
        bits.value >>= entry & 15u;
        bits.count -= entry & 15u;
        result = entry >> 4;
        return true;
    }
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
    Work* work;
    U32 expected, produced, crc;
    Status status;
    Output(InflateWorkspace& w, const Sink& s, U32 size, Work* service)
        : workspace(w), sink(s), work(service), expected(size), produced(0), crc(0xffffffffu), status(Status::ok) {}
    bool flush(U32 count) {
        if (!checkpoint(work)) {
            status = Status::cancelled;
            return false;
        }
        crc = crc32_update(crc, workspace.window, count);  // #279: per chunk, not per byte
        if (count && sink.write && !write_to_sink(sink, workspace.window, count)) {
            status = Status::sink;
            return false;
        }
        return true;
    }
    bool byte(Byte value) {
        if (produced == expected) {
            status = Status::bounds;
            return false;
        }
        workspace.window[produced & 32767u] = value;
        ++produced;
        return (produced & 32767u) || flush(32768);
    }
    bool finish() {
        if (produced != expected) {
            status = Status::bounds;
            return false;
        }
        return flush(produced & 32767u);
    }
};

bool fixed(InflateWorkspace& workspace) {
    for (U32 i = 0; i != 288; ++i)
        workspace.lengths[i] = static_cast<Byte>(i < 144 ? 8 : i < 256 ? 9 : i < 280 ? 7 : 8);
    if (!build(workspace.literal, workspace.lengths, 288, false, true, literal_root)) return false;
    for (U32 i = 0; i != 32; ++i) workspace.lengths[i] = 5;
    return build(workspace.distance, workspace.lengths, 32, false, true, distance_root);
}

bool dynamic(Bits& bits, InflateWorkspace& workspace) {
    U32 literals, distances, codes;
    if (!bits.take(5, literals) || !bits.take(5, distances) || !bits.take(4, codes)) return false;
    literals += 257;
    ++distances;
    codes += 4;
    if (literals > 286) {
        bits.status = Status::deflate;
        return false;
    }
    static const Byte order[19] = {16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15};
    for (U32 i = 0; i != 19; ++i) workspace.lengths[i] = 0;
    for (U32 i = 0; i != codes; ++i) {
        U32 length;
        if (!bits.take(3, length)) return false;
        workspace.lengths[order[i]] = static_cast<Byte>(length);
    }
    if (!build(workspace.code, workspace.lengths, 19, false, true, code_root)) {
        bits.status = Status::deflate;
        return false;
    }
    U32 total = literals + distances, index = 0;
    while (index < total) {
        U32 code;
        if (!symbol(bits, workspace.code, code)) return false;
        if (code < 16) workspace.lengths[index++] = static_cast<Byte>(code);
        else {
            U32 count, value = 0;
            if (code == 16) {
                if (!index) {
                    bits.status = Status::deflate;
                    return false;
                }
                value = workspace.lengths[index - 1];
                if (!bits.take(2, count)) return false;
                count += 3;
            } else if (code == 17) {
                if (!bits.take(3, count)) return false;
                count += 3;
            } else if (code == 18) {
                if (!bits.take(7, count)) return false;
                count += 11;
            } else {
                bits.status = Status::deflate;
                return false;
            }
            if (count > total - index) {
                bits.status = Status::deflate;
                return false;
            }
            while (count--) workspace.lengths[index++] = static_cast<Byte>(value);
        }
    }
    if (!workspace.lengths[256] || !build(workspace.literal, workspace.lengths, literals, false, false, literal_root) ||
        !build(workspace.distance, workspace.lengths + literals, distances, true, false, distance_root)) {
        bits.status = Status::deflate;
        return false;
    }
    return true;
}

const unsigned short length_base[29] = {3,  4,  5,  6,  7,  8,  9,  10, 11,  13,  15,  17,  19,  23, 27,
                                        31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258};
const Byte length_extra[29] = {0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0};
// The first four distance codes each encode one distance. Later pairs encode two equally sized ranges, doubling on
// every pair: a code's extra bits, and the distance before them (computed: a table of them has the bytes of the
// stock bootloader's own, which the build refuses, #178).
const Byte distance_extra[30] = {0, 0, 0, 0, 1, 1, 2, 2,  3,  3,  4,  4,  5,  5,  6,
                                 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13};
U32 distance_base(U32 code, U32 shift) { return code < 4 ? code + 1 : ((2 + (code & 1u)) << shift) + 1; }

// A match's distance, then its bytes, the careful way.
bool match(Bits& bits, Output& output, const InflateWorkspace& workspace, U32 length) {
    U32 code, extra;
    if (!symbol(bits, workspace.distance, code)) return false;
    if (code > 29) {
        bits.status = Status::deflate;
        return false;
    }
    if (!bits.take(distance_extra[code], extra)) return false;
    const U32 distance = distance_base(code, distance_extra[code]) + extra;
    if (distance > output.produced || length > output.expected - output.produced) {
        bits.status = Status::bounds;
        return false;
    }
    for (U32 i = 0; i != length; ++i) {
        Byte value = workspace.window[(output.produced - distance) & 32767u];
        if (!output.byte(value)) return false;
    }
    return true;
}

// #279: whole symbols with no call per bit or per byte, while the input buffer holds a symbol's worst case
// (fast_input bytes) and the window has room before its flush. Run's state, in and out of run().
const U32 fast_input = 12;
struct Run {
    U32 value, count, history, length, distance;           // history: the bytes produced before window[0]
    const Byte *in, *in_last;
    Byte *out, *end;                                       // end: the window's last byte, or the member's end
    Byte* window;
    const unsigned short *literals, *distances;
    enum Next { input_low, block_end, careful, careful_literal, careful_match, careful_copy, deflate, bounds } next;
};
// What it decodes, and its checks, are the careful loop's in compressed(); a symbol it cannot finish (a code the
// tables leave out, the window's or the member's end) it hands back half done (next). Each refill adds two bytes
// when fewer than 16 bits are left: a literal takes at most one, a match four (fast_input covers them).
__attribute__((always_inline)) inline void refill16(U32& value, U32& count, const Byte*& in) {
    value |= (static_cast<U32>(in[0]) | static_cast<U32>(in[1]) << 8) << count;
    in += 2;
    count += 16;
}
// A match's bytes, from distance back (no further back than the window's start).
__attribute__((always_inline)) inline Byte* copy(Byte* out, const Byte* from, U32 length) {
    for (U32 i = 0; i != length; ++i) out[i] = from[i];
    return out + length;
}
__attribute__((noinline)) void run(Run& r) {
    U32 value = r.value, count = r.count;
    const Byte* in = r.in;
    const Byte* const in_last = r.in_last;
    Byte* out = r.out;
    Byte* const end = r.end;
    const unsigned short* const literals = r.literals;
    Run::Next next = Run::input_low;
    while (in <= in_last) {
        if (count < 16) refill16(value, count, in);
        U32 entry = literals[value & ((1u << literal_root) - 1u)];
        if (entry >= table_link)
            entry = literals[(entry >> 3 & 0xfffu) + (value >> literal_root & ((1u << (entry & 7u)) - 1u))];
        if (!entry) {
            next = Run::careful;
            break;
        }
        value >>= entry & 15u;
        count -= entry & 15u;
        const U32 symbol = entry >> 4;
        if (symbol < 256) {
            if (out == end) {
                r.length = symbol;
                next = Run::careful_literal;
                break;
            }
            *out++ = static_cast<Byte>(symbol);
            continue;
        }
        if (symbol == 256) {
            next = Run::block_end;
            break;
        }
        if (symbol > 285) {
            next = Run::deflate;
            break;
        }
        if (count < 16) refill16(value, count, in);
        const U32 extra = length_extra[symbol - 257];
        U32 length = length_base[symbol - 257] + (value & ((1u << extra) - 1u));
        value >>= extra;
        count -= extra;
        if (count < 16) refill16(value, count, in);
        entry = r.distances[value & ((1u << distance_root) - 1u)];
        if (entry >= table_link)
            entry = r.distances[(entry >> 3 & 0xfffu) + (value >> distance_root & ((1u << (entry & 7u)) - 1u))];
        if (!entry) {
            r.length = length;
            next = Run::careful_match;
            break;
        }
        value >>= entry & 15u;
        count -= entry & 15u;
        const U32 code = entry >> 4;
        if (code > 29) {
            next = Run::deflate;
            break;
        }
        const U32 shift = distance_extra[code];
        if (count < 16) refill16(value, count, in);
        const U32 distance = distance_base(code, shift) + (value & ((1u << shift) - 1u));
        value >>= shift;
        count -= shift;
        const U32 at = static_cast<U32>(out - r.window);
        if (distance > r.history + at) {
            next = Run::bounds;
            break;
        }
        if (length > static_cast<U32>(end - out)) {            // the window's or the member's end: byte by byte
            r.length = length;
            r.distance = distance;
            next = Run::careful_copy;
            break;
        }
        if (at >= distance) out = copy(out, out - distance, length);
        else {                                                 // the history wraps around the window's end
            const U32 first = distance - at;                   // the bytes before the window's end
            const Byte* const from = r.window + (32768u - first);
            if (first >= length) out = copy(out, from, length);
            else out = copy(copy(out, from, first), r.window, length - first);
        }
    }
    r.value = value;
    r.count = count;
    r.in = in;
    r.out = out;
    r.next = next;
}

// 1: the block's end; -1: an error (bits.status or output.status set); 0: compressed() takes the next symbol.
int fast(Bits& bits, Output& output, InflateWorkspace& workspace) {
    Run r;
    r.window = workspace.window;
    r.literals = workspace.literal.table;
    r.distances = workspace.distance.table;
    for (;;) {
        if (bits.buffered - bits.cursor < fast_input) {
            if (bits.consumed + (bits.buffered - bits.cursor) == bits.size) return 0;     // the input's end
            if (!bits.refill()) return -1;
            if (bits.buffered - bits.cursor < fast_input) return 0;
        }
        // The window's last byte is left to Output::byte, which flushes the window when it is full.
        const U32 at = output.produced & 32767u, room = output.expected - output.produced;
        r.value = bits.value;
        r.count = bits.count;
        r.history = output.produced - at;
        r.in = workspace.input + bits.cursor;
        r.in_last = workspace.input + (bits.buffered - fast_input);
        r.out = workspace.window + at;
        r.end = workspace.window + (room < 32767u - at ? at + room : 32767u);
        run(r);
        const U32 taken = static_cast<U32>(r.in - (workspace.input + bits.cursor));
        bits.value = r.value;
        bits.count = r.count;
        bits.cursor += taken;
        bits.consumed += taken;
        output.produced += static_cast<U32>(r.out - (workspace.window + at));
        switch (r.next) {
        case Run::input_low: break;
        case Run::block_end: return 1;
        case Run::careful: return 0;
        case Run::careful_literal:
            if (!output.byte(static_cast<Byte>(r.length))) return -1;
            break;
        case Run::careful_match:
            if (!match(bits, output, workspace, r.length)) return -1;
            break;
        case Run::careful_copy:
            if (r.length > output.expected - output.produced) {
                bits.status = Status::bounds;
                return -1;
            }
            for (U32 i = 0; i != r.length; ++i)
                if (!output.byte(workspace.window[(output.produced - r.distance) & 32767u])) return -1;
            break;
        case Run::deflate: bits.status = Status::deflate; return -1;
        case Run::bounds: bits.status = Status::bounds; return -1;
        }
    }
}

bool compressed(Bits& bits, Output& output, InflateWorkspace& workspace) {
    for (;;) {
        const int quick = fast(bits, output, workspace);
        if (quick) return quick > 0;
        U32 code;
        if (!symbol(bits, workspace.literal, code)) return false;
        if (code < 256) {
            if (!output.byte(static_cast<Byte>(code))) return false;
            continue;
        }
        if (code == 256) return true;
        if (code > 285) {
            bits.status = Status::deflate;
            return false;
        }
        U32 extra, length_index = code - 257;
        if (!bits.take(length_extra[length_index], extra)) return false;
        if (!match(bits, output, workspace, length_base[length_index] + extra)) return false;
    }
}
}

// Running (pre-inverted) CRC-32 state. Boot images are checked from uncached memory:
// aligned input is read a word at a time (byte table, one data load per 4 bytes).
static_assert(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__, "word loads assume little-endian");
#if defined(XYF_SMALL_CRC)
// Narrow offline cold layouts: the 64-byte nibble table instead of the 1 KiB byte table.
U32 crc32_update(U32 running, const Byte* data, U32 count) { return crc::small_update(running, data, count); }
#else
U32 crc32_update(U32 running, const Byte* data, U32 count) {
    static const U32 table[256] = {
        0x00000000u, 0x77073096u, 0xee0e612cu, 0x990951bau, 0x076dc419u, 0x706af48fu, 0xe963a535u, 0x9e6495a3u,
        0x0edb8832u, 0x79dcb8a4u, 0xe0d5e91eu, 0x97d2d988u, 0x09b64c2bu, 0x7eb17cbdu, 0xe7b82d07u, 0x90bf1d91u,
        0x1db71064u, 0x6ab020f2u, 0xf3b97148u, 0x84be41deu, 0x1adad47du, 0x6ddde4ebu, 0xf4d4b551u, 0x83d385c7u,
        0x136c9856u, 0x646ba8c0u, 0xfd62f97au, 0x8a65c9ecu, 0x14015c4fu, 0x63066cd9u, 0xfa0f3d63u, 0x8d080df5u,
        0x3b6e20c8u, 0x4c69105eu, 0xd56041e4u, 0xa2677172u, 0x3c03e4d1u, 0x4b04d447u, 0xd20d85fdu, 0xa50ab56bu,
        0x35b5a8fau, 0x42b2986cu, 0xdbbbc9d6u, 0xacbcf940u, 0x32d86ce3u, 0x45df5c75u, 0xdcd60dcfu, 0xabd13d59u,
        0x26d930acu, 0x51de003au, 0xc8d75180u, 0xbfd06116u, 0x21b4f4b5u, 0x56b3c423u, 0xcfba9599u, 0xb8bda50fu,
        0x2802b89eu, 0x5f058808u, 0xc60cd9b2u, 0xb10be924u, 0x2f6f7c87u, 0x58684c11u, 0xc1611dabu, 0xb6662d3du,
        0x76dc4190u, 0x01db7106u, 0x98d220bcu, 0xefd5102au, 0x71b18589u, 0x06b6b51fu, 0x9fbfe4a5u, 0xe8b8d433u,
        0x7807c9a2u, 0x0f00f934u, 0x9609a88eu, 0xe10e9818u, 0x7f6a0dbbu, 0x086d3d2du, 0x91646c97u, 0xe6635c01u,
        0x6b6b51f4u, 0x1c6c6162u, 0x856530d8u, 0xf262004eu, 0x6c0695edu, 0x1b01a57bu, 0x8208f4c1u, 0xf50fc457u,
        0x65b0d9c6u, 0x12b7e950u, 0x8bbeb8eau, 0xfcb9887cu, 0x62dd1ddfu, 0x15da2d49u, 0x8cd37cf3u, 0xfbd44c65u,
        0x4db26158u, 0x3ab551ceu, 0xa3bc0074u, 0xd4bb30e2u, 0x4adfa541u, 0x3dd895d7u, 0xa4d1c46du, 0xd3d6f4fbu,
        0x4369e96au, 0x346ed9fcu, 0xad678846u, 0xda60b8d0u, 0x44042d73u, 0x33031de5u, 0xaa0a4c5fu, 0xdd0d7cc9u,
        0x5005713cu, 0x270241aau, 0xbe0b1010u, 0xc90c2086u, 0x5768b525u, 0x206f85b3u, 0xb966d409u, 0xce61e49fu,
        0x5edef90eu, 0x29d9c998u, 0xb0d09822u, 0xc7d7a8b4u, 0x59b33d17u, 0x2eb40d81u, 0xb7bd5c3bu, 0xc0ba6cadu,
        0xedb88320u, 0x9abfb3b6u, 0x03b6e20cu, 0x74b1d29au, 0xead54739u, 0x9dd277afu, 0x04db2615u, 0x73dc1683u,
        0xe3630b12u, 0x94643b84u, 0x0d6d6a3eu, 0x7a6a5aa8u, 0xe40ecf0bu, 0x9309ff9du, 0x0a00ae27u, 0x7d079eb1u,
        0xf00f9344u, 0x8708a3d2u, 0x1e01f268u, 0x6906c2feu, 0xf762575du, 0x806567cbu, 0x196c3671u, 0x6e6b06e7u,
        0xfed41b76u, 0x89d32be0u, 0x10da7a5au, 0x67dd4accu, 0xf9b9df6fu, 0x8ebeeff9u, 0x17b7be43u, 0x60b08ed5u,
        0xd6d6a3e8u, 0xa1d1937eu, 0x38d8c2c4u, 0x4fdff252u, 0xd1bb67f1u, 0xa6bc5767u, 0x3fb506ddu, 0x48b2364bu,
        0xd80d2bdau, 0xaf0a1b4cu, 0x36034af6u, 0x41047a60u, 0xdf60efc3u, 0xa867df55u, 0x316e8eefu, 0x4669be79u,
        0xcb61b38cu, 0xbc66831au, 0x256fd2a0u, 0x5268e236u, 0xcc0c7795u, 0xbb0b4703u, 0x220216b9u, 0x5505262fu,
        0xc5ba3bbeu, 0xb2bd0b28u, 0x2bb45a92u, 0x5cb36a04u, 0xc2d7ffa7u, 0xb5d0cf31u, 0x2cd99e8bu, 0x5bdeae1du,
        0x9b64c2b0u, 0xec63f226u, 0x756aa39cu, 0x026d930au, 0x9c0906a9u, 0xeb0e363fu, 0x72076785u, 0x05005713u,
        0x95bf4a82u, 0xe2b87a14u, 0x7bb12baeu, 0x0cb61b38u, 0x92d28e9bu, 0xe5d5be0du, 0x7cdcefb7u, 0x0bdbdf21u,
        0x86d3d2d4u, 0xf1d4e242u, 0x68ddb3f8u, 0x1fda836eu, 0x81be16cdu, 0xf6b9265bu, 0x6fb077e1u, 0x18b74777u,
        0x88085ae6u, 0xff0f6a70u, 0x66063bcau, 0x11010b5cu, 0x8f659effu, 0xf862ae69u, 0x616bffd3u, 0x166ccf45u,
        0xa00ae278u, 0xd70dd2eeu, 0x4e048354u, 0x3903b3c2u, 0xa7672661u, 0xd06016f7u, 0x4969474du, 0x3e6e77dbu,
        0xaed16a4au, 0xd9d65adcu, 0x40df0b66u, 0x37d83bf0u, 0xa9bcae53u, 0xdebb9ec5u, 0x47b2cf7fu, 0x30b5ffe9u,
        0xbdbdf21cu, 0xcabac28au, 0x53b39330u, 0x24b4a3a6u, 0xbad03605u, 0xcdd70693u, 0x54de5729u, 0x23d967bfu,
        0xb3667a2eu, 0xc4614ab8u, 0x5d681b02u, 0x2a6f2b94u, 0xb40bbe37u, 0xc30c8ea1u, 0x5a05df1bu, 0x2d02ef8du};
    typedef U32 __attribute__((may_alias)) Word;
    U32 i = 0;
    for (; i != count && (reinterpret_cast<unsigned long>(data + i) & 3u); ++i)
        running = table[(running ^ data[i]) & 0xffu] ^ (running >> 8);
    for (; count - i >= 4; i += 4) {
        running ^= *reinterpret_cast<const Word*>(data + i);
        running = table[running & 0xffu] ^ (running >> 8);
        running = table[running & 0xffu] ^ (running >> 8);
        running = table[running & 0xffu] ^ (running >> 8);
        running = table[running & 0xffu] ^ (running >> 8);
    }
    for (; i != count; ++i) running = table[(running ^ data[i]) & 0xffu] ^ (running >> 8);
    return running;
}
#endif

Status inflate_raw(const Reader& reader, U32 offset, U32 compressed_size, U32 expected_size,
                   InflateWorkspace& workspace, const Sink& sink, Extraction& result, Work* work) {
    result = Extraction{0, 0, 0};
    if (!reader.read) return Status::argument;
    if (offset > reader.size || compressed_size > reader.size - offset) return Status::bounds;
    if (!checkpoint(work)) return Status::cancelled;
    Bits bits(reader, workspace, offset, compressed_size, work);
    Output output(workspace, sink, expected_size, work);
    U32 final = 0;
    while (!final) {
        if (!checkpoint(work)) return Status::cancelled;
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
            if (!compressed(bits, output, workspace)) return bits.status != Status::ok ? bits.status : output.status;
        }
    }
    if (bits.used() != compressed_size) return Status::deflate;
    if (!output.finish()) return output.status;
    if (!checkpoint(work)) return Status::cancelled;
    result = Extraction{output.produced, output.crc ^ 0xffffffffu, bits.used()};
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
    case Status::cancelled: return "cancelled";
    }
    return "unknown";
}
}}

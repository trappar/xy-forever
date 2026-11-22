// ZIP32 subset from PKWARE APPNOTE 6.3.10. Original implementation.
#include "zip.hpp"

namespace xyf_core { namespace zip {
namespace {
U32 word(const Byte* p) { return static_cast<U32>(p[0]) | (static_cast<U32>(p[1]) << 8); }
U32 dword(const Byte* p) { return word(p) | (word(p + 2) << 16); }
bool read(const Reader& reader, U32 offset, Byte* output, U32 count) {
    return offset <= reader.size && count <= reader.size - offset && reader.read(reader.context, offset, output, count);
}
char lower(char c) { return c >= 'A' && c <= 'Z' ? static_cast<char>(c + ('a' - 'A')) : c; }
bool same_prefix(const char* a, const char* b, U32 count) {
    for (U32 i = 0; i != count; ++i)
        if (lower(a[i]) != lower(b[i])) return false;
    return true;
}
bool ratio_exceeded(U32 expanded, U32 compressed, U32 ratio) {
    // Saturate at expanded using only word arithmetic. Variable division or
    // 64-bit multiplication could introduce runtime helpers on Blackfin.
    U32 product = 0;
    while (ratio) {
        if (ratio & 1u) {
            if (compressed > expanded - product) return false;
            product += compressed;
        }
        ratio >>= 1;
        if (!ratio) break;
        if (compressed > expanded / 2u) return false;
        compressed <<= 1;
    }
    return product < expanded;
}
bool safe(const char* name, U32 size, bool directory) {
    if (!size || size > 255) return false;
    U32 end = directory ? size - 1 : size, component = 0;
    if (!end) return false;
    for (U32 i = 0; i <= end; ++i) {
        if (i == end || name[i] == '/') {
            U32 length = i - component;
            if (!length || (length == 1 && name[component] == '.') ||
                (length == 2 && name[component] == '.' && name[component + 1] == '.') || name[i - 1] == '.' ||
                name[i - 1] == ' ')
                return false;
            component = i + 1;
        } else {
            unsigned char c = static_cast<unsigned char>(name[i]);
            if (c < 32 || c >= 127 || c == '\\' || c == ':') return false;
        }
    }
    return true;
}
Status extras(const Reader& reader, U32 offset, U32 count, Work* work) {
    Byte header[4];
    while (count) {
        if (!checkpoint(work)) return Status::cancelled;
        if (count < 4 || !read(reader, offset, header, 4)) return Status::layout;
        U32 tag = word(header), size = word(header + 2);
        if (size > count - 4) return Status::layout;
        if (tag == 1 || tag == 0x7075 || tag == 0x6375) return Status::unsupported;
        count -= size + 4;
        offset += size + 4;
    }
    return Status::ok;
}
Status local(const Reader& reader, Entry& entry, U32 directory_offset, Work* work) {
    Byte header[30], name[256];
    if (!read(reader, entry.local_offset, header, 30)) return Status::truncated;
    if (dword(header) != 0x04034b50u) return Status::layout;
    if (word(header + 4) > 20 || word(header + 6) != entry.flags || word(header + 8) != entry.method ||
        word(header + 26) != entry.name_size)
        return Status::layout;
    U32 crc = dword(header + 14), compressed = dword(header + 18), size = dword(header + 22);
    if (entry.flags & 8u) {
        if ((crc && crc != entry.crc32) || (compressed && compressed != entry.compressed_size) ||
            (size && size != entry.size))
            return Status::layout;
    } else if (crc != entry.crc32 || compressed != entry.compressed_size || size != entry.size) return Status::layout;
    U32 name_offset = entry.local_offset + 30, extra_size = word(header + 28);
    if (name_offset > directory_offset || entry.name_size > directory_offset - name_offset) return Status::layout;
    if (!read(reader, name_offset, name, entry.name_size)) return Status::io;
    for (U32 i = 0; i != entry.name_size; ++i)
        if (name[i] != static_cast<Byte>(entry.name[i])) return Status::layout;
    U32 extra_offset = name_offset + entry.name_size;
    if (extra_size > directory_offset - extra_offset) return Status::layout;
    Status status = extras(reader, extra_offset, extra_size, work);
    if (status != Status::ok) return status;
    entry.data_offset = extra_offset + extra_size;
    if (entry.compressed_size > directory_offset - entry.data_offset) return Status::layout;
    entry.local_end = entry.data_offset + entry.compressed_size;
    if (entry.flags & 8u) {
        Byte descriptor[16];
        if (directory_offset - entry.local_end < 12 || !read(reader, entry.local_end, descriptor, 12))
            return Status::truncated;
        U32 shift = 0, length = 12;
        if (dword(descriptor) == 0x08074b50u) {
            if (directory_offset - entry.local_end < 16 || !read(reader, entry.local_end, descriptor, 16))
                return Status::truncated;
            shift = 4;
            length = 16;
        }
        if (dword(descriptor + shift) != entry.crc32 || dword(descriptor + shift + 4) != entry.compressed_size ||
            dword(descriptor + shift + 8) != entry.size)
            return Status::layout;
        entry.local_end += length;
    }
    return Status::ok;
}
Status scan(const Reader& reader, const Limits& limits, Entry* entries, U32 capacity, Archive& archive, Work* work) {
    archive.entries = 0;
    archive.count = 0;
    archive.total_bytes = 0;
    archive.directory_offset = 0;
    if (!reader.read || !entries || !limits.entries || !limits.member_bytes || !limits.total_bytes ||
        !limits.archive_bytes || !limits.expansion_ratio)
        return Status::argument;
    if (reader.size < 22) return Status::truncated;
    if (reader.size > limits.archive_bytes) return Status::bounds;
    Byte end[22];
    U32 offset = reader.size - 22, minimum = reader.size > 65557 ? reader.size - 65557 : 0;
    bool found = false;
    for (;;) {
        if (!checkpoint(work)) return Status::cancelled;
        if (!read(reader, offset, end, 22)) return Status::io;
        if (dword(end) == 0x06054b50u && word(end + 20) == reader.size - offset - 22) {
            found = true;
            break;
        }
        if (offset == minimum) break;
        --offset;
    }
    if (!found) return Status::layout;
    if (word(end + 4) || word(end + 6) || word(end + 8) != word(end + 10)) return Status::unsupported;
    U32 count = word(end + 10), directory_size = dword(end + 12), directory_offset = dword(end + 16);
    if (count == 65535 || directory_size == 0xffffffffu || directory_offset == 0xffffffffu) return Status::unsupported;
    if (!count || count > limits.entries) return Status::bounds;
    if (count > capacity) return Status::capacity;
    if (directory_offset > offset || directory_size != offset - directory_offset) return Status::layout;
    U32 cursor = directory_offset, total = 0, local_bytes = 0;
    for (U32 index = 0; index != count; ++index) {
        if (!checkpoint(work)) return Status::cancelled;
        Byte header[46];
        if (cursor > offset || offset - cursor < 46 || !read(reader, cursor, header, 46)) return Status::truncated;
        if (dword(header) != 0x02014b50u) return Status::layout;
        Entry& entry = entries[index];
        entry.flags = word(header + 8);
        entry.method = word(header + 10);
        if (word(header + 6) > 20 || (entry.flags & ~0x080eu) || (entry.method != 0 && entry.method != 8) ||
            (!entry.method && (entry.flags & 6u)) || word(header + 34))
            return Status::unsupported;
        entry.crc32 = dword(header + 16);
        entry.compressed_size = dword(header + 20);
        entry.size = dword(header + 24);
        entry.name_size = word(header + 28);
        entry.local_offset = dword(header + 42);
        U32 extra_size = word(header + 30), comment_size = word(header + 32);
        if (entry.size == 0xffffffffu || entry.compressed_size == 0xffffffffu || entry.local_offset == 0xffffffffu)
            return Status::unsupported;
        if (!entry.name_size || entry.name_size > 255) return Status::name;
        U32 variable = entry.name_size + extra_size + comment_size;
        if (variable > offset - cursor - 46) return Status::truncated;
        if (!read(reader, cursor + 46, reinterpret_cast<Byte*>(entry.name), entry.name_size)) return Status::io;
        entry.name[entry.name_size] = 0;
        entry.directory = entry.name[entry.name_size - 1] == '/';
        if (!safe(entry.name, entry.name_size, entry.directory)) return Status::name;
        U32 mode = dword(header + 38) >> 16, kind = mode & 0170000u;
        if ((kind && kind != 0100000u && kind != 0040000u) || (kind == 0040000u && !entry.directory) ||
            (kind == 0100000u && entry.directory))
            return Status::unsupported;
        if (entry.size > limits.member_bytes || entry.size > limits.total_bytes - total) return Status::bounds;
        if (!entry.compressed_size && entry.size) return Status::layout;
        if (entry.compressed_size && ratio_exceeded(entry.size, entry.compressed_size, limits.expansion_ratio))
            return Status::bounds;
        if (!entry.method && entry.compressed_size != entry.size) return Status::layout;
        if (entry.directory && (entry.size || entry.crc32)) return Status::layout;
        total += entry.size;
        Status status = extras(reader, cursor + 46 + entry.name_size, extra_size, work);
        if (status != Status::ok) return status;
        status = local(reader, entry, directory_offset, work);
        if (status != Status::ok) return status;
        if (entry.local_offset >= entry.local_end) return Status::layout;
        U32 span = entry.local_end - entry.local_offset;
        if (span > directory_offset - local_bytes) return Status::layout;
        local_bytes += span;
        for (U32 previous = 0; previous != index; ++previous) {
            if ((previous & 31u) == 31u && !checkpoint(work)) return Status::cancelled;
            const Entry& other = entries[previous];
            if (entry.local_offset < other.local_end && other.local_offset < entry.local_end) return Status::layout;
            U32 a = entry.name_size - (entry.directory ? 1 : 0), b = other.name_size - (other.directory ? 1 : 0);
            U32 common = a < b ? a : b;
            if (!same_prefix(entry.name, other.name, common)) continue;
            if (a == b) return Status::duplicate;
            if ((a < b && !entry.directory && other.name[a] == '/') ||
                (b < a && !other.directory && entry.name[b] == '/'))
                return Status::layout;
        }
        cursor += 46 + variable;
    }
    if (cursor != offset || local_bytes != directory_offset) return Status::layout;
    if (!checkpoint(work)) return Status::cancelled;
    archive.reader = reader;
    // Individual stores avoid the target compiler lowering a Limits aggregate
    // copy to an external memcpy call.
    archive.limits.entries = limits.entries;
    archive.limits.member_bytes = limits.member_bytes;
    archive.limits.total_bytes = limits.total_bytes;
    archive.limits.archive_bytes = limits.archive_bytes;
    archive.limits.expansion_ratio = limits.expansion_ratio;
    archive.entries = entries;
    archive.count = count;
    archive.total_bytes = total;
    archive.directory_offset = directory_offset;
    return Status::ok;
}
}

Status open(const Reader& reader, const Limits& limits, Entry* entries, U32 capacity, Archive& archive, Work* work) {
    const Status status = scan(reader, limits, entries, capacity, archive, work);
    return cancelled(work) ? Status::cancelled : status;
}

Status extract(const Archive& archive, U32 index, InflateWorkspace& workspace, const Sink& sink, Extraction& result,
               Work* work) {
    result = Extraction{0, 0, 0};
    if (!archive.entries || index >= archive.count) return Status::argument;
    const Entry& entry = archive.entries[index];
    if (entry.directory) return Status::argument;
    if (entry.size > archive.limits.member_bytes || entry.data_offset > archive.directory_offset ||
        entry.compressed_size > archive.directory_offset - entry.data_offset)
        return Status::bounds;
    Extraction extracted = {0, 0, 0};
    if (entry.method == 8) {
        Status status = inflate_raw(archive.reader, entry.data_offset, entry.compressed_size, entry.size, workspace,
                                    sink, extracted, work);
        if (status != Status::ok) return status;
    } else if (entry.method == 0) {
        if (entry.compressed_size != entry.size) return Status::layout;
        U32 offset = 0, running = 0xffffffffu;
        while (offset < entry.size) {
            U32 chunk = entry.size - offset;
            if (chunk > sizeof(workspace.window)) chunk = sizeof(workspace.window);
            for (U32 copied = 0; copied < chunk;) {
                if (!checkpoint(work)) return Status::cancelled;
                U32 count = chunk - copied;
                if (work && count > sizeof(workspace.input)) count = sizeof(workspace.input);
                Byte* out = workspace.window + copied;
                if (!read(archive.reader, entry.data_offset + offset + copied, out, count))
                    return cancelled(work) ? Status::cancelled : Status::io;
                running = crc32_update(running, out, count);
                copied += count;
            }
            if (!checkpoint(work)) return Status::cancelled;
            if (sink.write && !sink.write(sink.context, workspace.window, chunk)) return Status::sink;
            offset += chunk;
        }
        if (!checkpoint(work)) return Status::cancelled;
        extracted = Extraction{entry.size, running ^ 0xffffffffu, entry.compressed_size};
    } else return Status::unsupported;
    if (extracted.crc32 != entry.crc32) return Status::checksum;
    result = extracted;
    return Status::ok;
}
}}

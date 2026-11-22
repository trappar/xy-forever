#include "tfw.hpp"
namespace xyf_core {
namespace tfw { namespace {
U32 little16(const Byte* p) { return U32(p[0]) | U32(p[1]) << 8; }
U32 little32(const Byte* p) { return little16(p) | little16(p + 2) << 16; }
bool magic(const Byte* p, Byte a, Byte b, Byte c, Byte d) { return p[0] == a && p[1] == b && p[2] == c && p[3] == d; }
void erase(Byte* p, U32 count) {
    volatile Byte* wipe = p;
    for (U32 i = 0; i < count; ++i) wipe[i] = 0;
}
// The calls through the caller's seams, out of line: the firmware builder's call audit
// (xyf_build.py call_targets) accepts an indirect call only with its pointer loaded beside it.
__attribute__((noinline)) bool call(const input::Reader& reader, U32 offset, Byte* destination, U32 count) {
    return reader.read(reader.context, offset, destination, count);
}
__attribute__((noinline)) bool get_key(const KeyProvider& keys, U32 index, Byte* key) {
    return keys.read(keys.context, index, key);
}
__attribute__((noinline)) bool verified(const HeaderCheck& check, const Byte* header, U32 size) {
    return check.verify(check.context, header, size);
}
}}
namespace input {
bool read(const Reader& reader, U32 offset, Byte* destination, U32 count) {
    return reader.read && (destination || !count) && offset <= reader.size && count <= reader.size - offset &&
           tfw::call(reader, offset, destination, count);
}
}
namespace tfw {
Archive::Archive() : work_(nullptr), ready_(false) {
    filename_[0] = 0;
    source_ = {0, 0, 0};
    header_ = {};
}
Archive::~Archive() { close(); }
void Archive::close() {
    aes_.erase();
    ready_ = false;
    filename_[0] = 0;
    source_ = {0, 0, 0};
    work_ = nullptr;
}
namespace {
// 0x80 for babecafe + beefcafe, 0x40 for a bare beefcafe preamble, 0 for neither.
U32 wrapper(const Byte* raw) {
    if (magic(raw, 0xba, 0xbe, 0xca, 0xfe) && magic(raw + 0x40, 0xbe, 0xef, 0xca, 0xfe)) return 0x80;
    return magic(raw, 0xbe, 0xef, 0xca, 0xfe) ? 0x40 : 0;
}
// Structural checks of the first 320 bytes; trusts nothing (no key, no hash yet).
Status parse(const Byte* raw, U32 source_size, U32 expected_sku, Header& h) {
    const U32 base = wrapper(raw);
    if (!base) return Status::header;
    if (!magic(raw + base + 0x84, 'T', 'E', 'S', 'G') || little16(raw + base + 0x88) != 1 ||
        little16(raw + base + 0x8a) != 1)
        return Status::manifest;
    h = Header();
    h.crypto_offset = base;
    h.ciphertext_offset = base + 0x300;
    h.archive_size = little32(raw + base + 0x80);
    h.ciphertext_size = little32(raw + base + 0x98);
    h.key_index = raw[base + 4];
    h.sku = little32(raw + base + 0x8c);
    if (!h.archive_size || h.ciphertext_offset > source_size ||
        h.ciphertext_size != source_size - h.ciphertext_offset || h.archive_size > source_size - 256)
        return Status::size;
    const U32 expected = 256 + h.archive_size + (h.key_index == 0xfe ? 0 : 16 - (h.archive_size & 15));
    if (h.ciphertext_size != expected) return Status::size;
    if (h.sku != expected_sku) return Status::sku;
    for (U32 i = 0; i < 4; ++i) h.version.field[i] = little16(raw + base + 0x90 + 2 * i);
    for (U32 i = 0; i < 32; ++i) h.ciphertext_hash.bytes[i] = raw[base + 0x9c + i];
    for (U32 i = 0; i < 16; ++i) h.iv[i] = raw[base + 0x70 + i];
    return Status::ok;
}
bool usable(const input::Reader& source) {
    return source.read && source.size >= prefix_bytes && source.size <= max_package_bytes;
}
}
Status ciphertext_span(const Byte* prefix, U32& start, U32& end, U32 expected_sku) {
    const U32 base = wrapper(prefix);
    if (!base) return Status::header;
    const U32 offset = base + crypto_header_bytes;
    const U32 size = little32(prefix + base + 0x98);
    if (size > max_package_bytes - offset) return Status::size;
    Header h;
    const Status parsed = parse(prefix, offset + size, expected_sku, h);
    if (parsed == Status::ok) {
        start = offset;
        end = offset + size;
    }
    return parsed;
}
Status Archive::open(const input::Reader& source, const KeyProvider& keys, U32 expected_sku) {
    close();
    if (!usable(source)) return Status::size;
    Byte raw[prefix_bytes];
    if (!input::read(source, 0, raw, sizeof(raw))) return Status::io;
    Header h;
    const Status parsed = parse(raw, source.size, expected_sku, h);
    if (parsed != Status::ok) return parsed;
    hash::Sha256 hasher;
    Byte buffer[512];
    for (U32 offset = 0; offset < h.ciphertext_size;) {
        const U32 amount = h.ciphertext_size - offset < sizeof(buffer) ? h.ciphertext_size - offset : sizeof(buffer);
        if (!input::read(source, h.ciphertext_offset + offset, buffer, amount)) return Status::io;
        if (!hasher.update(buffer, amount)) return Status::size;
        offset += amount;
    }
    hash::Digest digest;
    if (!hasher.finish(digest) || !hash::equal(digest, h.ciphertext_hash)) return Status::ciphertext_hash;
    source_ = source;
    return finish(h, keys);
}
Status Archive::open_digested(const input::Reader& source, const KeyProvider& keys,
                              const hash::Digest& ciphertext_sha256, const HeaderCheck& signature, Byte* header,
                              U32 expected_sku, Work* work) {
    close();
    if (!header || !signature.verify) return Status::argument;
    if (!usable(source)) return Status::size;
    if (!checkpoint(work)) return Status::cancelled;
    if (!input::read(source, 0, header, prefix_bytes)) return Status::io;
    const U32 base = wrapper(header);
    if (!base) return Status::header;
    if (source.size < base + crypto_header_bytes) return Status::size;
    for (U32 at = prefix_bytes; at < base + crypto_header_bytes;) {
        if (!checkpoint(work)) return Status::cancelled;
        U32 amount = base + crypto_header_bytes - at;
        if (work && amount > 512) amount = 512;
        if (!input::read(source, at, header + at, amount)) return Status::io;
        at += amount;
    }
    Header h;
    const Status parsed = parse(header, source.size, expected_sku, h);
    if (parsed != Status::ok) return parsed;
    if (h.key_index == 0xfe) return Status::key;
    // The seam: nothing below this line runs for a header the check refuses.
    if (!checkpoint(work)) return Status::cancelled;
    if (!verified(signature, header + base, crypto_header_bytes)) return Status::signature;
    if (!checkpoint(work)) return Status::cancelled;
    if (!hash::equal(ciphertext_sha256, h.ciphertext_hash)) return Status::ciphertext_hash;
    source_ = source;
    work_ = work;
    const Status status = finish(h, keys);
    return cancelled(work) ? Status::cancelled : status;
}
Status Archive::finish(const Header& h, const KeyProvider& keys) {
    header_ = h;
    Byte buffer[256];
    if (h.key_index != 0xfe) {
        Byte key[32] = {};
        const bool received = keys.read && get_key(keys, h.key_index, key);
        const bool configured = received && aes_.initialize(key);
        erase(key, sizeof(key));
        if (!configured) {
            close();
            return Status::key;
        }
        Byte tail[16];
        if (!raw_read(h.ciphertext_size - 16, tail, 16)) {
            close();
            return Status::io;
        }
        const U32 padding = 16 - (h.archive_size & 15);
        for (U32 i = 16 - padding; i < 16; ++i)
            if (tail[i] != padding) {
                close();
                return Status::padding;
            }
    }
    if (!raw_read(0, buffer, 256)) {
        close();
        return Status::io;
    }
    U32 name_size = 0;
    while (name_size < 128 && buffer[name_size]) {
        const Byte c = buffer[name_size];
        if (c < 32 || c >= 127 || c == '/' || c == '\\' || c == ':') {
            close();
            return Status::filename;
        }
        ++name_size;
    }
    if (name_size < 5 || buffer[name_size - 4] != '.' || (buffer[name_size - 3] | 32) != 'z' ||
        (buffer[name_size - 2] | 32) != 'i' || (buffer[name_size - 1] | 32) != 'p') {
        close();
        return Status::filename;
    }
    if (!checkpoint(work_)) {
        close();
        return Status::cancelled;
    }
    for (U32 i = 0; i < name_size; ++i) filename_[i] = char(buffer[i]);
    filename_[name_size] = 0;
    ready_ = true;
    return Status::ok;
}
bool Archive::raw_read(U32 offset, Byte* destination, U32 count) {
    if ((!destination && count) || offset > header_.ciphertext_size || count > header_.ciphertext_size - offset)
        return false;
    if (header_.key_index == 0xfe) return input::read(source_, header_.ciphertext_offset + offset, destination, count);
    if (!count) return true;
    Byte previous[16], cipher[512], clear[16];
    U32 block = offset & ~15u, skip = offset & 15;
    if (!block)
        for (U32 i = 0; i < 16; ++i) previous[i] = header_.iv[i];
    else if (!checkpoint(work_) || !input::read(source_, header_.ciphertext_offset + block - 16, previous, 16))
        return false;
    while (count) {
        const U32 amount = count + skip > sizeof(cipher) ? sizeof(cipher) : (count + skip + 15) & ~15u;
        if (!checkpoint(work_) || amount > header_.ciphertext_size - block ||
            !input::read(source_, header_.ciphertext_offset + block, cipher, amount))
            return false;
        for (U32 at = 0; at < amount; at += 16) {
            if (!aes_.decrypt(cipher + at, clear)) return false;
            for (U32 i = skip; i < 16 && count; ++i) {
                *destination++ = clear[i] ^ previous[i];
                --count;
            }
            for (U32 i = 0; i < 16; ++i) previous[i] = cipher[at + i];
            skip = 0;
        }
        block += amount;
    }
    return true;
}
bool Archive::zip_read(void* context, U32 offset, Byte* destination, U32 count) {
    Archive& self = *static_cast<Archive*>(context);
    return self.ready_ && offset <= self.header_.archive_size && count <= self.header_.archive_size - offset &&
           self.raw_read(offset + 256, destination, count);
}
input::Reader Archive::reader() {
    return ready_ ? input::Reader{this, header_.archive_size, zip_read} : input::Reader{0, 0, 0};
}
Status ciphertext_digest(const input::Reader& source, Byte* buffer, U32 capacity, hash::Digest& digest,
                         U32 expected_sku, Work* work) {
    if (!buffer || capacity < header_bytes) return Status::argument;
    if (!usable(source)) return Status::size;
    if (!checkpoint(work)) return Status::cancelled;
    if (!input::read(source, 0, buffer, prefix_bytes)) return Status::io;
    Header h;
    const Status parsed = parse(buffer, source.size, expected_sku, h);
    if (parsed != Status::ok) return parsed;
    hash::Sha256 hasher;
    const U32 chunk = work && capacity > 512 ? 512 : capacity;
    for (U32 offset = 0; offset < h.ciphertext_size;) {
        if (!checkpoint(work)) return Status::cancelled;
        const U32 amount = h.ciphertext_size - offset < chunk ? h.ciphertext_size - offset : chunk;
        if (!input::read(source, h.ciphertext_offset + offset, buffer, amount)) return Status::io;
        if (!hasher.update(buffer, amount)) return Status::size;
        offset += amount;
    }
    if (!checkpoint(work)) return Status::cancelled;
    return hasher.finish(digest) ? Status::ok : Status::size;
}
const char* status_name(Status status) {
    switch (status) {
    case Status::ok: return "ok";
    case Status::io: return "io";
    case Status::header: return "header";
    case Status::manifest: return "manifest";
    case Status::size: return "size";
    case Status::sku: return "sku";
    case Status::ciphertext_hash: return "ciphertext_hash";
    case Status::key: return "key";
    case Status::padding: return "padding";
    case Status::filename: return "filename";
    case Status::signature: return "signature";
    case Status::argument: return "argument";
    case Status::cancelled: return "cancelled";
    }
    return "unknown";
}
}
}

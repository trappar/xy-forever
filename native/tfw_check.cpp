// The tfw.check verdict. ZIP32 fields from PKWARE APPNOTE 6.3.10; record rules as in
// tfw_os.cpp (the stock packages' plain subset).
#include "tfw_check.hpp"
#include "tfw_signature.hpp"
namespace xyf_core { namespace tfw_check {
namespace {
const char os_path[] = "firmware/blackfin/bin/os.ldr";
U32 le16(const Byte* p) { return U32(p[0]) | U32(p[1]) << 8; }
U32 le32(const Byte* p) { return le16(p) | le16(p + 2) << 16; }
void erase(void* p, U32 count) {
    volatile Byte* wipe = static_cast<Byte*>(p);
    for (U32 i = 0; i != count; ++i) wipe[i] = 0;
}
bool same(const Byte* name, U32 size, const char* text) {
    U32 n = 0;
    while (text[n]) ++n;
    if (size != n) return false;
    for (U32 i = 0; i != size; ++i)
        if (name[i] != Byte(text[i])) return false;
    return true;
}

// The package as the extractor sees it: the file itself, or the package inside a cut. A read
// the file answers short sets short_read; an error, or a byte outside the cut, sets error.
struct View {
    File file;
    U32 head, tail, size;
    bool cut, error, short_read;
};
// The calls through the device's seams, out of line (as input::call).
__attribute__((noinline)) int file_read(const File& file, U32 at, Byte* out, U32 count) {
    return file.read(file.context, at, out, count);
}
__attribute__((noinline)) bool public_key(const Keys& keys, Byte* out) { return keys.public_key(keys.context, out); }
__attribute__((noinline)) bool aes(const tfw::KeyProvider& keys, U32 index, Byte* out) {
    return keys.read(keys.context, index, out);
}
bool fetch(View& v, U32 at, Byte* out, U32 count) {
    const int got = file_read(v.file, at, out, count);
    if (got < 0) {
        v.error = true;
        return false;
    }
    if (U32(got) != count) {
        v.short_read = true;
        return false;
    }
    return true;
}
bool view_read(void* context, U32 offset, Byte* out, U32 count) {
    View& v = *static_cast<View*>(context);
    if (!count) return true;
    if (!v.cut) return fetch(v, offset, out, count);
    if (offset <= v.head && count <= v.head - offset) return fetch(v, cut_header_bytes + offset, out, count);
    if (offset >= v.tail) return fetch(v, cut_header_bytes + v.head + (offset - v.tail), out, count);
    v.error = true;
    return false;
}
// The file ends exactly at `bytes` (a ranged read there returns the last 16 bytes and no more).
bool ends_at(View& v, U32 bytes) {
    Byte probe[32];
    if (bytes < 16) return false;
    const int got = file_read(v.file, bytes - 16, probe, sizeof(probe));
    if (got < 0) v.error = true;
    return got == 16;
}

// One check's key requests and why the last one failed.
struct Session {
    const Keys* keys;
    Result* result;
    Reason reason;
};
// The extractor's HeaderCheck (after the header's structure passed): the signature key index
// first, so a header that names another key never reaches the device's memory; then the public
// key into a buffer erased here, its SHA-256 for the reply, and the stock signature rule.
bool header_check(void* context, const Byte* header, U32 size) {
    Session& s = *static_cast<Session*>(context);
    if (size != tfw::crypto_header_bytes) {
        s.reason = Reason::not_package;
        return false;
    }
    if (header[tfw::signature_key_index]) {
        s.reason = Reason::key_index;
        return false;
    }
    Byte key[tfw::signature_public_key_bytes];
    erase(key, sizeof(key));
    if (!s.keys->public_key || !public_key(*s.keys, key)) {
        erase(key, sizeof(key));
        s.reason = Reason::no_otp;
        return false;
    }
    s.result->public_key_read = true;
    hash::Sha256 hasher;
    hasher.update(key, sizeof(key));
    hasher.finish(s.result->public_key_sha256);
    erase(&hasher, sizeof(hasher));
    const tfw::Signature verdict = tfw::verify_header(header, size, key);
    erase(key, sizeof(key));
    if (verdict == tfw::Signature::valid) return true;
    s.reason = verdict == tfw::Signature::key       ? Reason::public_key :
               verdict == tfw::Signature::key_index ? Reason::key_index :
                                                      Reason::signature;
    return false;
}
// The extractor's KeyProvider: key index 0 only (OTP selectors 3/4); Archive erases the key.
bool aes_key(void* context, U32 index, Byte key[32]) {
    Session& s = *static_cast<Session*>(context);
    if (index) {
        s.reason = Reason::key_index;
        return false;
    }
    if (!s.keys->aes.read || !aes(s.keys->aes, index, key)) {
        s.reason = Reason::no_otp;
        return false;
    }
    return true;
}

Reason opened(const View& v, const Session& s, tfw::Status status) {
    switch (status) {
    case tfw::Status::ok: return Reason::pass;
    case tfw::Status::io: return v.error || v.short_read ? Reason::read : Reason::not_package;
    case tfw::Status::signature: return s.reason;
    case tfw::Status::key: return s.reason == Reason::pass ? Reason::key_index : s.reason; // 0xfe: the clear container
    case tfw::Status::padding:
    case tfw::Status::filename: return Reason::decrypt;
    default: return Reason::not_package;
    }
}

// A directory read that failed: the storage or the cut, or a directory that points past the package.
Reason missed(const View& v) { return v.error || v.short_read ? Reason::read : Reason::directory; }
// The end record (no comment) and every central directory record, decrypted piece by piece.
Reason directory(const input::Reader& zip, const View& v, Byte* e, Result& r) {
    const Reason broken = Reason::directory;
    if (zip.size < 22) return broken;
    const U32 end = zip.size - 22;
    if (!input::read(zip, end, e, 22)) return missed(v);
    if (le32(e) != 0x06054b50u || le16(e + 4) || le16(e + 6) || le16(e + 8) != le16(e + 10) || le16(e + 20))
        return broken;
    const U32 count = le16(e + 10), size = le32(e + 12), offset = le32(e + 16);
    if (!count || count == 0xffffu || offset > end || size != end - offset) return broken;
    U32 cursor = 0, os = 0;
    for (U32 i = 0; i != count; ++i) {
        if (size - cursor < 46) return broken;
        if (!input::read(zip, offset + cursor, e, 46)) return missed(v);
        const U32 n = le16(e + 28);
        if (le32(e) != 0x02014b50u || le16(e + 6) > 20 || le16(e + 8) || le16(e + 10) != 8 || le16(e + 30) ||
            le16(e + 32) || le16(e + 34) || le32(e + 20) == 0xffffffffu || le32(e + 24) == 0xffffffffu ||
            le32(e + 42) == 0xffffffffu || !n || n > 255 || n > size - cursor - 46)
            return broken;
        const U32 local = le32(e + 42), compressed = le32(e + 20);
        if (!input::read(zip, offset + cursor + 46, e + 46, n)) return missed(v);
        for (U32 k = 0; k != n; ++k)
            if (e[46 + k] < 0x20 || e[46 + k] > 0x7e || e[46 + k] == '\\') return broken;
        if (local > offset || 30 + n > offset - local || compressed > offset - local - 30 - n) return broken;
        if (same(e + 46, n, os_path)) ++os;
        cursor += 46 + n;
        ++r.entries;
    }
    return cursor == size && os == 1 ? Reason::pass : broken;
}

Reason run(const File& file, const Keys& keys, Workspace& w, Result& r) {
    View v = {file, 0, 0, 0, false, false, false};
    Byte* h = w.header;
    if (!fetch(v, 0, h, 16)) return v.error ? Reason::read : Reason::not_package;
    if (le32(h) == cut_magic) {
        const U32 package = le32(h + 4), head = le32(h + 8), tail = le32(h + 12);
        if (head > tail || tail > package || head > 0xffffffffu - cut_header_bytes - (package - tail))
            return Reason::not_package;
        v.cut = true;
        v.head = head;
        v.tail = tail;
        v.size = package;
        if (!ends_at(v, cut_header_bytes + head + (package - tail)))
            return v.error ? Reason::read : Reason::not_package;
    }
    // The header's own sizes say how long the package is; the file (or the cut) must agree.
    if (!view_read(&v, 0, h, 320)) return v.error ? Reason::read : Reason::not_package;
    const bool outer = le32(h) == 0xfecabebau && le32(h + 0x40) == 0xfecaefbeu;
    const U32 base = outer ? 0x80u : le32(h) == 0xfecaefbeu ? 0x40u : 0u;
    if (!base) return Reason::not_package;
    const U32 ciphertext = le32(h + base + 0x98);
    if (ciphertext > 0xffffffffu - base - tfw::crypto_header_bytes) return Reason::not_package;
    const U32 declared = base + tfw::crypto_header_bytes + ciphertext;
    if (v.cut ? declared != v.size : !ends_at(v, declared)) return v.error ? Reason::read : Reason::not_package;
    v.size = declared;
    hash::Digest digest; // the header's own: tfw.check does not hash the payload
    for (U32 i = 0; i != 32; ++i) digest.bytes[i] = h[base + 0x9c + i];
    Session s = {&keys, &r, Reason::pass};
    const input::Reader reader = {&v, v.size, view_read};
    const tfw::KeyProvider aes = {&s, aes_key};
    const tfw::HeaderCheck signature = {&s, header_check};
    const tfw::Status status = w.archive.open_digested(reader, aes, digest, signature, h);
    r.container = status;
    const Reason verdict = opened(v, s, status);
    return verdict == Reason::pass ? directory(w.archive.reader(), v, w.record, r) : verdict;
}
}

Reason check(const File& file, const Keys& keys, Workspace& w, Result& r) {
    r.reason = Reason::read;
    r.public_key_read = false;
    erase(r.public_key_sha256.bytes, sizeof(r.public_key_sha256.bytes));
    r.container = tfw::Status::ok;
    r.entries = 0;
    r.reason = file.read ? run(file, keys, w, r) : Reason::read;
    w.archive.close(); // erases the AES schedule
    erase(w.header, sizeof(w.header));
    erase(w.record, sizeof(w.record));
    return r.reason;
}

const char* reason_name(Reason reason) {
    switch (reason) {
    case Reason::pass: return "pass";
    case Reason::read: return "read";
    case Reason::not_package: return "not_package";
    case Reason::key_index: return "key_index";
    case Reason::no_otp: return "no_otp";
    case Reason::public_key: return "public_key";
    case Reason::signature: return "signature";
    case Reason::decrypt: return "decrypt";
    case Reason::directory: return "directory";
    }
    return "unknown";
}
}}

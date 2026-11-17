// OS-only .tfw extractor. ZIP32 fields from PKWARE APPNOTE 6.3.10.
#include "tfw_os.hpp"
namespace xyf_core { namespace tfw_os {
namespace {
const char os_path[] = "firmware/blackfin/bin/os.ldr";
const char version_path[] = "firmware/blackfin/bin/os.version";
const U32 absent = 0xffffffffu, chunk = 32768;
U32 le16(const Byte* p) { return U32(p[0]) | U32(p[1]) << 8; }
U32 le32(const Byte* p) { return le16(p) | le16(p + 2) << 16; }
Byte lower(Byte c) { return c >= 'A' && c <= 'Z' ? Byte(c + 32) : c; }
U32 length(const char* text) { U32 n = 0; while (text[n]) ++n; return n; }
bool same(const Byte* name, U32 size, const char* text) {
    if (size != length(text)) return false;
    for (U32 i = 0; i != size; ++i) if (name[i] != Byte(text[i])) return false;
    return true;
}
// Case-insensitive: a decoy such as content/OS.LDR is refused, not ignored.
bool basename_is(const Byte* name, U32 size, const char* base) {
    U32 start = 0;
    for (U32 i = 0; i != size; ++i) if (name[i] == '/') start = i + 1;
    const U32 n = length(base);
    if (size - start != n) return false;
    for (U32 i = 0; i != n; ++i) if (lower(name[start + i]) != Byte(base[i])) return false;
    return true;
}
void erase(Byte* p, U32 count) { volatile Byte* wipe = p; for (U32 i = 0; i != count; ++i) wipe[i] = 0; }
U32 round4(U32 n) { return (n + 3u) & ~3u; }

// The decrypted central directory and four per-entry columns in scratch.
struct Table {
    const Byte* directory;
    U32 *at, *start, *end, *order;  // record offset, local header offset, declared end, sort order
};
const Byte* record(const Table& t, U32 i) { return t.directory + t.at[i]; }
U32 name_size(const Table& t, U32 i) { return le16(record(t, i) + 28); }
const Byte* name(const Table& t, U32 i) { return record(t, i) + 46; }
int compare_names(const Table& t, U32 a, U32 b) {
    const U32 an = name_size(t, a), bn = name_size(t, b);
    const Byte *x = name(t, a), *y = name(t, b);
    for (U32 i = 0; i != an && i != bn; ++i) {
        const Byte p = lower(x[i]), q = lower(y[i]);
        if (p != q) return p < q ? -1 : 1;
    }
    return an == bn ? 0 : an < bn ? -1 : 1;
}
bool by_name(const Table& t, U32 a, U32 b) { return compare_names(t, a, b) < 0; }
bool by_start(const Table& t, U32 a, U32 b) { return t.start[a] < t.start[b]; }
typedef bool (*Less)(const Table&, U32, U32);
void sift(U32* v, U32 root, U32 n, const Table& t, Less less) {
    for (;;) {
        U32 child = 2 * root + 1;
        if (child >= n) return;
        if (child + 1 < n && less(t, v[child], v[child + 1])) ++child;
        if (!less(t, v[root], v[child])) return;
        const U32 swap = v[root]; v[root] = v[child]; v[child] = swap;
        root = child;
    }
}
void heapsort(U32* v, U32 n, const Table& t, Less less) {
    for (U32 i = n / 2; i;) sift(v, --i, n, t, less);
    for (U32 end = n; end > 1;) {
        --end;
        const U32 swap = v[0]; v[0] = v[end]; v[end] = swap;
        sift(v, 0, end, t, less);
    }
}

struct Copy { Byte* memory; U32 capacity, count; };
bool copy(void* context, const Byte* bytes, U32 count) {
    Copy& out = *static_cast<Copy*>(context);
    if (count > out.capacity - out.count) return false;
    for (U32 i = 0; i != count; ++i) out.memory[out.count + i] = bytes[i];
    out.count += count;
    return true;
}
Status member_status(zip::Status status) {
    return status == zip::Status::io ? Status::io : status == zip::Status::checksum ? Status::checksum : Status::member;
}
Status container_status(tfw::Status status) {
    switch (status) {
    case tfw::Status::io: return Status::io;
    case tfw::Status::signature: return Status::signature;
    case tfw::Status::ciphertext_hash: return Status::ciphertext_hash;
    case tfw::Status::key: return Status::key;
    case tfw::Status::argument: return Status::argument;
    default: return Status::container;
    }
}
// The local header must repeat the directory record exactly: same name, no flags, no
// extra field, deflate, same CRC and sizes. Returns the data offset in `data`.
Status locate(const input::Reader& zip, const Table& t, U32 i, Workspace& w, U32& data) {
    const Byte* central = record(t, i);
    const U32 n = name_size(t, i);
    if (!input::read(zip, t.start[i], w.local, 30 + n)) return Status::io;
    const Byte* local = w.local;
    if (le32(local) != 0x04034b50u || le16(local + 4) > 20 || le16(local + 6) || le16(local + 8) != 8 ||
        le32(local + 14) != le32(central + 16) || le32(local + 18) != le32(central + 20) ||
        le32(local + 22) != le32(central + 24) || le16(local + 26) != n || le16(local + 28)) return Status::layout;
    for (U32 k = 0; k != n; ++k) if (local[30 + k] != name(t, i)[k]) return Status::layout;
    data = t.start[i] + 30 + n;
    return Status::ok;
}
Status inflate(const input::Reader& zip, const Table& t, U32 i, U32 data, Workspace& w,
               Copy& out, Result& result) {
    const Byte* central = record(t, i);
    const zip::Reader source = {zip.context, zip.size, zip.read};
    const zip::Sink sink = {&out, copy};
    zip::Extraction extracted;
    const zip::Status status = zip::inflate_raw(source, data, le32(central + 20), le32(central + 24),
                                                w.inflate, sink, extracted);
    result.inflate = status;
    if (status != zip::Status::ok) return member_status(status);
    if (extracted.crc32 != le32(central + 16) || out.count != le32(central + 24)) {
        result.inflate = zip::Status::checksum;
        return Status::checksum;
    }
    return Status::ok;
}

Status run(const input::Reader& package, const tfw::KeyProvider& keys, const hash::Digest& digest,
           const tfw::HeaderCheck& signature, const Policy& policy, U32 entry_point,
           const Limits& limits, Workspace& w, const Writer& output, Result& result) {
    tfw::Archive& archive = w.archive;
    const tfw::Status opened = archive.open_digested(package, keys, digest, signature, w.header);
    result.container = opened;
    if (opened != tfw::Status::ok) return container_status(opened);
    for (U32 i = 0; i != 4; ++i) result.package_version.field[i] = archive.header().version.field[i];
    const input::Reader zip = archive.reader();

    // End record: exactly the last 22 bytes (an archive comment is refused).
    if (zip.size < 22) return Status::directory;
    const U32 end_offset = zip.size - 22;
    Byte* end = w.local;
    if (!input::read(zip, end_offset, end, 22)) return Status::io;
    if (le32(end) != 0x06054b50u) return Status::directory;
    if (le16(end + 4) || le16(end + 6) || le16(end + 8) != le16(end + 10) || le16(end + 20) ||
        le16(end + 10) == 0xffffu || le32(end + 12) == 0xffffffffu || le32(end + 16) == 0xffffffffu)
        return Status::unsupported;
    const U32 count = le16(end + 10), directory_size = le32(end + 12), directory_offset = le32(end + 16);
    if (!count || directory_offset > end_offset || directory_size != end_offset - directory_offset)
        return Status::directory;
    if (count > limits.entries || directory_size > limits.directory_bytes) return Status::capacity;

    Byte* image = w.scratch;
    Byte* directory = image + limits.image_bytes;
    U32* columns = reinterpret_cast<U32*>(directory + round4(limits.directory_bytes));
    Table t = {directory, columns, columns + limits.entries, columns + 2 * limits.entries,
               columns + 3 * limits.entries};
    if (!input::read(zip, directory_offset, directory, directory_size)) return Status::io;

    // Every record: the stock packages' plain ZIP32 subset, a known safe name, no decoy.
    U32 cursor = 0, os = absent, version = absent;
    package::Destination destination;
    for (U32 i = 0; i != count; ++i) {
        if (directory_size - cursor < 46) return Status::directory;
        const Byte* h = directory + cursor;
        if (le32(h) != 0x02014b50u) return Status::directory;
        const U32 n = le16(h + 28);
        if (le16(h + 6) > 20 || le16(h + 8) || le16(h + 10) != 8 || le16(h + 30) || le16(h + 32) ||
            le16(h + 34) || le32(h + 20) == 0xffffffffu || le32(h + 24) == 0xffffffffu ||
            le32(h + 42) == 0xffffffffu) return Status::unsupported;
        if ((le16(h + 4) >> 8) == 3 && (le32(h + 38) >> 16 & 0170000u) != 0100000u) return Status::unsupported;
        if (!n || n > 255) return Status::name;
        if (n > directory_size - cursor - 46) return Status::directory;
        const Byte* text = h + 46;
        if (package::classify(reinterpret_cast<const char*>(text), n, destination) != package::Status::ok)
            return Status::name;
        if (same(text, n, os_path)) {
            if (os != absent) return Status::duplicate;
            os = i;
        } else if (same(text, n, version_path)) {
            if (version != absent) return Status::duplicate;
            version = i;
        } else if (basename_is(text, n, "os.ldr") || basename_is(text, n, "os.version")) return Status::name;
        const U32 local = le32(h + 42), compressed = le32(h + 20);
        if (local > directory_offset || 30 + n > directory_offset - local ||
            compressed > directory_offset - local - 30 - n) return Status::layout;
        t.at[i] = cursor; t.start[i] = local; t.end[i] = local + 30 + n + compressed; t.order[i] = i;
        cursor += 46 + n;
    }
    if (cursor != directory_size) return Status::directory;
    if (os == absent || version == absent) return Status::missing;

    // Names differ even ignoring case; members tile [0, directory) exactly as declared
    // (no gap, no overlap, nothing hidden), which bounds both wanted members.
    heapsort(t.order, count, t, by_name);
    for (U32 k = 1; k < count; ++k) if (!compare_names(t, t.order[k - 1], t.order[k])) return Status::duplicate;
    for (U32 k = 0; k != count; ++k) t.order[k] = k;
    heapsort(t.order, count, t, by_start);
    U32 expected = 0;
    for (U32 k = 0; k != count; ++k) {
        if (t.start[t.order[k]] != expected) return Status::layout;
        expected = t.end[t.order[k]];
    }
    if (expected != directory_offset) return Status::layout;

    // os.version: at most 128 bytes, "M.m.p+b[@commit]".
    U32 data = 0;
    Status status = locate(zip, t, version, w, data);
    if (status != Status::ok) return status;
    if (le32(record(t, version) + 24) > version_bytes) return Status::version;
    Copy text = {w.text, version_bytes, 0};
    status = inflate(zip, t, version, data, w, text, result);
    if (status != Status::ok) return status;
    if (package::parse_version(w.text, text.count, result.os_version) != package::Status::ok) return Status::version;

    // os.ldr: inflated into scratch, CRC and size from the directory, then the LDR policy.
    const U32 size = le32(record(t, os) + 24);
    if (size > limits.image_bytes) return Status::capacity;
    if (size < 32) return Status::image;
    status = locate(zip, t, os, w, data);
    if (status != Status::ok) return status;
    Copy out = {image, size, 0};
    status = inflate(zip, t, os, data, w, out, result);
    if (status != Status::ok) return status;
    Summary summary;
    result.ldr = inspect(image, size, policy, summary);
    if (result.ldr != Error::ok || summary.entry != entry_point) return Status::image;
    hash::Sha256 hasher;
    if (!hasher.update(image, size) || !hasher.finish(result.sha256)) return Status::image;
    archive.close();
    result.bytes = size; result.crc32 = le32(record(t, os) + 16);
    result.entry = summary.entry; result.records = summary.records;

    // Checked: hand the image to the writer.
    for (U32 at = 0; at < size;) {
        const U32 amount = size - at < chunk ? size - at : chunk;
        if (!output.write(output.context, image + at, amount)) return Status::write;
        at += amount; result.written = at;
    }
    return Status::ok;
}
}

U32 scratch_bytes(const Limits& limits) {
    if (!limits.image_bytes || limits.image_bytes > max_limit_bytes || (limits.image_bytes & 3u) ||
        !limits.directory_bytes || limits.directory_bytes > max_limit_bytes ||
        !limits.entries || limits.entries > max_entries) return 0;
    return limits.image_bytes + round4(limits.directory_bytes) + 16u * limits.entries;
}

Status extract(const input::Reader& package, const tfw::KeyProvider& keys,
               const hash::Digest& ciphertext_sha256, const tfw::HeaderCheck& signature,
               const Policy& ram_policy, U32 entry_point, const Limits& limits,
               Workspace& workspace, const Writer& output, Result& result) {
    result.bytes = 0; result.crc32 = 0; result.entry = 0; result.records = 0; result.written = 0;
    for (U32 i = 0; i != 32; ++i) result.sha256.bytes[i] = 0;
    for (U32 i = 0; i != 4; ++i) { result.os_version.field[i] = 0; result.package_version.field[i] = 0; }
    result.container = tfw::Status::ok; result.inflate = zip::Status::ok; result.ldr = Error::ok;
    const U32 needed = scratch_bytes(limits);
    if (!needed || !workspace.scratch || workspace.capacity < needed ||
        (reinterpret_cast<unsigned long>(workspace.scratch) & 3u) || !output.write || !signature.verify ||
        !keys.read) return Status::argument;
    const Status status = run(package, keys, ciphertext_sha256, signature, ram_policy, entry_point, limits,
                              workspace, output, result);
    workspace.archive.close();
    erase(workspace.local, sizeof(workspace.local));
    if (status != Status::ok && status != Status::write) {
        result.bytes = 0; result.crc32 = 0; result.entry = 0; result.records = 0;
        for (U32 i = 0; i != 32; ++i) result.sha256.bytes[i] = 0;
    }
    return status;
}

U32 version_text(const package::Version& version, char* out, U32 capacity) {
    static const U32 powers[5] = {10000, 1000, 100, 10, 1};
    U32 n = 0;
    for (U32 f = 0; f != 4; ++f) {
        U32 value = version.field[f];
        if (value > 65535) return 0;
        if (f) { if (n + 1 >= capacity) return 0; out[n++] = f == 3 ? '+' : '.'; }
        bool started = false;
        for (U32 p = 0; p != 5; ++p) {
            U32 digit = 0;
            while (value >= powers[p]) { value -= powers[p]; ++digit; }
            if (!digit && !started && p != 4) continue;
            if (n + 1 >= capacity) return 0;
            out[n++] = char('0' + digit); started = true;
        }
    }
    if (n >= capacity) return 0;
    out[n] = 0;
    return n;
}

const char* status_name(Status status) {
    switch (status) {
    case Status::ok: return "ok";
    case Status::argument: return "argument";
    case Status::capacity: return "capacity";
    case Status::io: return "io";
    case Status::container: return "container";
    case Status::signature: return "signature";
    case Status::ciphertext_hash: return "ciphertext_hash";
    case Status::key: return "key";
    case Status::directory: return "directory";
    case Status::unsupported: return "unsupported";
    case Status::name: return "name";
    case Status::duplicate: return "duplicate";
    case Status::layout: return "layout";
    case Status::missing: return "missing";
    case Status::member: return "member";
    case Status::checksum: return "checksum";
    case Status::version: return "version";
    case Status::image: return "image";
    case Status::write: return "write";
    }
    return "unknown";
}
} }

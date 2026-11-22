#include "package.hpp"

namespace xyf_core { namespace package {
namespace {
const U32 absent = 0xffffffffu;
U32 length(const char* text) {
    U32 result = 0;
    while (result < 257 && text[result]) ++result;
    return result;
}
bool same(const char* a, const char* b) {
    U32 i = 0;
    while (i < 257 && a[i] && a[i] == b[i]) ++i;
    return i < 257 && a[i] == b[i];
}
void copy_digest(hash::Digest& out, const hash::Digest& in) {
    for (U32 i = 0; i != 32; ++i) out.bytes[i] = in.bytes[i];
}
void copy_version(Version& out, const Version& in) {
    for (U32 i = 0; i != 4; ++i) out.field[i] = in.field[i];
}
void zero_version(Version& out) {
    out.field[0] = 0;
    out.field[1] = 0;
    out.field[2] = 0;
    out.field[3] = 0;
}
bool excluded(Role role) { return role == Role::excluded_bootloader || role == Role::excluded_host; }
bool image(Role role) { return role == Role::os || role == Role::backplane || role == Role::ble; }
bool archive_hash(const zip::Archive& archive, Workspace& workspace, hash::Digest& result) {
    hash::Sha256 hash;
    U32 offset = 0;
    while (offset < archive.reader.size) {
        U32 count = archive.reader.size - offset;
        if (count > sizeof(workspace.inflate.input)) count = sizeof(workspace.inflate.input);
        if (!archive.reader.read(archive.reader.context, offset, workspace.inflate.input, count) ||
            !hash.update(workspace.inflate.input, count))
            return false;
        offset += count;
    }
    return hash.finish(result);
}
struct Output {
    hash::Sha256 hash;
    Byte* memory;
    U32 capacity, count;
    const Writer* writer;
    bool writer_failed;
    Output(Byte* bytes, U32 size, const Writer* out)
        : memory(bytes), capacity(size), count(0), writer(out), writer_failed(false) {}
};
bool sink(void* context, const Byte* bytes, U32 count) {
    Output& out = *static_cast<Output*>(context);
    if (count > out.capacity - out.count || !out.hash.update(bytes, count)) return false;
    if (out.memory)
        for (U32 i = 0; i != count; ++i) out.memory[out.count + i] = bytes[i];
    if (out.writer && !out.writer->write(out.writer->context, bytes, count)) {
        out.writer_failed = true;
        return false;
    }
    out.count += count;
    return true;
}
zip::Status extract(const zip::Archive& archive, U32 index, Workspace& workspace, Output& output) {
    const zip::Entry& entry = archive.entries[index];
    zip::Sink destination = {&output, sink};
    zip::Extraction result;
    if (!entry.directory) return zip::extract(archive, index, workspace.inflate, destination, result);
    if (entry.size || entry.crc32) return zip::Status::layout;
    if (!entry.method) return entry.compressed_size ? zip::Status::layout : zip::Status::ok;
    zip::Status status = zip::inflate_raw(archive.reader, entry.data_offset, entry.compressed_size, 0,
                                          workspace.inflate, destination, result);
    if (status != zip::Status::ok) return status;
    return result.crc32 ? zip::Status::checksum : zip::Status::ok;
}
bool valid_workspace(const zip::Archive& archive, const Workspace& workspace) {
    return archive.entries && archive.count && archive.reader.read && workspace.scratch &&
           workspace.capacity >= 8u * 1024u * 1024u;
}
AdmissionStatus directory(const zip::Archive& archive, U32 index, Workspace& workspace) {
    Output out(0, 0, 0);
    return extract(archive, index, workspace, out) == zip::Status::ok ? AdmissionStatus::ok : AdmissionStatus::archive;
}
AdmissionStatus versions(const Destination& destination, const Byte* data, U32 size, Version& os, Version& backplane,
                         Version& ble, Version& firmware, U32& mask) {
    U32 bit = 0;
    Version* target = 0;
    if (same(destination.relative, "metadata/os.version")) {
        bit = 1;
        target = &os;
    } else if (same(destination.relative, "metadata/backplane.version")) {
        bit = 2;
        target = &backplane;
    } else if (same(destination.relative, "metadata/ble.version")) {
        bit = 4;
        target = &ble;
    } else if (same(destination.relative, "metadata/firmware.version")) {
        bit = 8;
        target = &firmware;
    }
    if (!bit) return AdmissionStatus::ok;
    if ((mask & bit) || parse_version(data, size, *target) != Status::ok) return AdmissionStatus::version;
    mask |= bit;
    return AdmissionStatus::ok;
}
bool is_version(const Destination& destination) {
    return destination.role == Role::metadata && !same(destination.relative, "metadata/versions.txt");
}
}

AdmissionStatus admit(const zip::Archive& archive, const Policy& ram_policy, Workspace& workspace, Item* items,
                      U32 capacity, Manifest& manifest) {
    manifest.items = 0;
    manifest.count = 0;
    if (!valid_workspace(archive, workspace) || !items) return AdmissionStatus::argument;
    if (archive.count > capacity) return AdmissionStatus::capacity;
    hash::Digest original, final;
    if (!archive_hash(archive, workspace, original)) return AdmissionStatus::hash;
    U32 count = 0, os_item = absent, backplane_item = absent, ble_item = absent, version_mask = 0;
    Version os_version, backplane_version, ble_version, firmware_version;
    zero_version(os_version);
    zero_version(backplane_version);
    zero_version(ble_version);
    zero_version(firmware_version);
    for (U32 index = 0; index != archive.count; ++index) {
        const zip::Entry& entry = archive.entries[index];
        if (entry.directory) {
            AdmissionStatus status = directory(archive, index, workspace);
            if (status != AdmissionStatus::ok) return status;
            continue;
        }
        Item& item = items[count];
        if (classify(entry.name, entry.name_size, item.destination) != Status::ok) return AdmissionStatus::policy;
        item.entry_index = index;
        item.size = entry.size;
        bool keep = image(item.destination.role) || is_version(item.destination);
        if (keep && entry.size > workspace.capacity) return AdmissionStatus::capacity;
        Output output(keep ? workspace.scratch : 0, entry.size, 0);
        if (extract(archive, index, workspace, output) != zip::Status::ok) return AdmissionStatus::archive;
        if (!output.hash.finish(item.digest)) return AdmissionStatus::hash;
        if (item.destination.role == Role::os) {
            if (os_item != absent) return AdmissionStatus::policy;
            Summary summary;
            if (inspect(workspace.scratch, entry.size, ram_policy, summary) != Error::ok) return AdmissionStatus::os;
            os_item = count;
        } else if (item.destination.role == Role::backplane) {
            if (backplane_item != absent) return AdmissionStatus::policy;
            images::CyacdSummary summary;
            if (images::inspect_cyacd(workspace.scratch, entry.size, summary) != images::Status::ok)
                return AdmissionStatus::backplane;
            backplane_item = count;
        } else if (item.destination.role == Role::ble) {
            if (ble_item != absent) return AdmissionStatus::policy;
            images::BleSummary summary;
            if (images::inspect_ble(workspace.scratch, entry.size, summary) != images::Status::ok)
                return AdmissionStatus::ble;
            ble_item = count;
        } else if (is_version(item.destination)) {
            AdmissionStatus status = versions(item.destination, workspace.scratch, entry.size, os_version,
                                              backplane_version, ble_version, firmware_version, version_mask);
            if (status != AdmissionStatus::ok) return status;
        }
        ++count;
    }
    if (os_item == absent) return AdmissionStatus::policy;
    if ((backplane_item != absent) != bool(version_mask & 2u) || (ble_item != absent) != bool(version_mask & 4u))
        return AdmissionStatus::version;
    if (!archive_hash(archive, workspace, final)) return AdmissionStatus::hash;
    if (!hash::equal(original, final)) return AdmissionStatus::changed;
    manifest.os_item = os_item;
    manifest.backplane_item = backplane_item;
    manifest.ble_item = ble_item;
    manifest.has_os_version = version_mask & 1u;
    manifest.has_backplane_version = version_mask & 2u;
    manifest.has_ble_version = version_mask & 4u;
    manifest.has_firmware_version = version_mask & 8u;
    copy_version(manifest.os_version, os_version);
    copy_version(manifest.backplane_version, backplane_version);
    copy_version(manifest.ble_version, ble_version);
    copy_version(manifest.firmware_version, firmware_version);
    copy_digest(manifest.archive_digest, original);
    manifest.items = items;
    manifest.count = count;
    return AdmissionStatus::ok;
}

AdmissionStatus stage(const zip::Archive& archive, const Manifest& manifest, Workspace& workspace,
                      const Writer& writer) {
    if (!valid_workspace(archive, workspace) || !manifest.items || !manifest.count || manifest.count > archive.count ||
        !writer.begin || !writer.write || !writer.end || !writer.abort || !writer.publish)
        return AdmissionStatus::argument;
    hash::Digest current;
    if (!archive_hash(archive, workspace, current) || !hash::equal(current, manifest.archive_digest)) {
        writer.abort(writer.context);
        return AdmissionStatus::changed;
    }
    // Validate the complete mapping before the first backend callback.
    U32 next = 0;
    for (U32 index = 0; index != archive.count; ++index) {
        const zip::Entry& entry = archive.entries[index];
        if (entry.directory) continue;
        if (next >= manifest.count) {
            writer.abort(writer.context);
            return AdmissionStatus::policy;
        }
        const Item& item = manifest.items[next++];
        Destination expected;
        if (item.entry_index != index || item.size != entry.size ||
            classify(entry.name, entry.name_size, expected) != Status::ok || expected.role != item.destination.role ||
            !same(expected.relative, item.destination.relative)) {
            writer.abort(writer.context);
            return AdmissionStatus::policy;
        }
        if (!excluded(expected.role) && (!length(expected.relative) || length(expected.relative) > 256)) {
            writer.abort(writer.context);
            return AdmissionStatus::policy;
        }
    }
    if (next != manifest.count) {
        writer.abort(writer.context);
        return AdmissionStatus::policy;
    }
    next = 0;
    for (U32 index = 0; index != archive.count; ++index) {
        const zip::Entry& entry = archive.entries[index];
        if (entry.directory) {
            AdmissionStatus status = directory(archive, index, workspace);
            if (status != AdmissionStatus::ok) {
                writer.abort(writer.context);
                return status;
            }
            continue;
        }
        const Item& item = manifest.items[next++];
        bool write = !excluded(item.destination.role);
        if (write && !writer.begin(writer.context, item.destination.relative, item.size, item.digest)) {
            writer.abort(writer.context);
            return AdmissionStatus::write;
        }
        Output output(0, item.size, write ? &writer : 0);
        if (extract(archive, index, workspace, output) != zip::Status::ok || !output.hash.finish(current) ||
            !hash::equal(current, item.digest)) {
            writer.abort(writer.context);
            return output.writer_failed ? AdmissionStatus::write : AdmissionStatus::changed;
        }
        if (write && !writer.end(writer.context)) {
            writer.abort(writer.context);
            return AdmissionStatus::write;
        }
    }
    if (!archive_hash(archive, workspace, current) || !hash::equal(current, manifest.archive_digest)) {
        writer.abort(writer.context);
        return AdmissionStatus::changed;
    }
    if (!writer.publish(writer.context, manifest)) {
        writer.abort(writer.context);
        return AdmissionStatus::finalize;
    }
    return AdmissionStatus::ok;
}

const char* admission_status_name(AdmissionStatus status) {
    switch (status) {
    case AdmissionStatus::ok: return "ok";
    case AdmissionStatus::argument: return "argument";
    case AdmissionStatus::capacity: return "capacity";
    case AdmissionStatus::archive: return "archive";
    case AdmissionStatus::policy: return "policy";
    case AdmissionStatus::hash: return "hash";
    case AdmissionStatus::os: return "os";
    case AdmissionStatus::backplane: return "backplane";
    case AdmissionStatus::ble: return "ble";
    case AdmissionStatus::version: return "version";
    case AdmissionStatus::changed: return "changed";
    case AdmissionStatus::write: return "write";
    case AdmissionStatus::finalize: return "finalize";
    }
    return "unknown";
}
}}

#ifndef XYF_PACKAGE_HPP
#define XYF_PACKAGE_HPP
#include "zip.hpp"
#include "hash.hpp"
#include "images.hpp"
#include "package_policy.hpp"

namespace xyf_core { namespace package {
enum class AdmissionStatus {
    ok,
    argument,
    capacity,
    archive,
    policy,
    hash,
    os,
    backplane,
    ble,
    version,
    changed,
    write,
    finalize
};
struct Item {
    U32 entry_index, size;
    Destination destination;
    hash::Digest digest;
};
// Caller owns items, which remain immutable after successful admission. No
// firmware bytes are retained in this descriptor, only identities and versions.
struct Manifest {
    const Item* items;
    U32 count, os_item, backplane_item, ble_item;
    hash::Digest archive_digest;
    bool has_os_version, has_backplane_version, has_ble_version, has_firmware_version;
    Version os_version, backplane_version, ble_version, firmware_version;
};
struct Workspace {
    zip::InflateWorkspace inflate;
    Byte* scratch;
    U32 capacity;
};
// No write callbacks exist in admission. Every member, including exclusions,
// is fully CRC-checked and SHA-256 hashed before any staging can begin.
AdmissionStatus admit(const zip::Archive& archive, const Policy& ram_policy, Workspace& workspace, Item* items,
                      U32 capacity, Manifest& manifest);
struct Writer {
    void* context;
    bool (*begin)(void*, const char* relative, U32 size, const hash::Digest& expected);
    bool (*write)(void*, const Byte*, U32 count);
    bool (*end)(void*);
    void (*abort)(void*);
    // Publish only the completed immutable generation/journal. This callback
    // must not activate a payload, flash a companion, or touch the bootloader.
    bool (*publish)(void*, const Manifest&);
};
// Recheck the archive identity before the first write, every extracted hash/CRC
// during writes, and archive identity again before publish. On error abort is
// invoked and no publish follows. Backend must keep all output provisional.
AdmissionStatus stage(const zip::Archive& archive, const Manifest& manifest, Workspace& workspace,
                      const Writer& writer);
const char* admission_status_name(AdmissionStatus status);
}}
#endif

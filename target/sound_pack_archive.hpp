#ifndef XYF_SOUND_PACK_ARCHIVE_HPP
#define XYF_SOUND_PACK_ARCHIVE_HPP

#include "file_engine.hpp"
#include "xyf_manifest.hpp"
#include "../native/mod_content.hpp"
#include "../native/zip.hpp"

// The caller authenticates the TFW container and keeps its source immutable.
// This admission pass classifies ZIP members and reads the manifest before staging.
namespace xy_forever { namespace sound_pack_archive {
namespace zip = xyf_core::zip;
using xyf_core::Byte;
using xyf_core::U32;

constexpr char prefix[] = "firmware/blackfin/content/dlc/";
constexpr char marker[] = "firmware/blackfin/content/partial_upgrade";
constexpr U32 manifest_bytes = 4096, name_bytes = 127;

struct Layout {
    char folder[name_bytes + 1];
    U32 manifest, files, bytes, relative_at;
};
struct ManifestBuffer {
    Byte text[manifest_bytes];
    U32 bytes;
};

inline zip::Limits limits() {
    zip::Limits result;
    result.total_bytes = 320u * 1024u * 1024u;
    return result;
}

inline bool starts_with(const char* text, const char* start) {
    while (*start)
        if (*text++ != *start++) return false;
    return true;
}
inline bool same(const char* a, const char* b) {
    while (*a && *a == *b) {
        ++a;
        ++b;
    }
    return *a == *b;
}

inline bool archive_parent(const char* name) {
    return same(name, "firmware/") || same(name, "firmware/blackfin/") || same(name, "firmware/blackfin/content/") ||
           same(name, prefix);
}

inline bool valid_output(const Layout& pack, const char* relative, U32 size, bool directory) {
    // The staging root is the same depth as the live pack root. Its basename is
    // the bounded transaction token, regardless of the vendor pack name.
    const char* const roots[] = {"/yaffs2/content/dlc/", "/yaffs2/xy-forever/tmp/pkg-v1-00000000"};
    for (U32 i = 0; i != 2; ++i) {
        char path[file_engine::max_path];
        U32 end = 0;
        for (const char* p = roots[i]; *p; ++p) path[end++] = *p;
        if (!i)
            for (const char* p = pack.folder; *p; ++p) path[end++] = *p;
        path[end++] = '/';
        if (size >= sizeof(path) - end) return false;
        for (U32 j = 0; j != size; ++j) path[end++] = relative[j];
        path[end] = 0;
        if (file_engine::decide_engine(directory ? file_engine::op_mkdir : file_engine::op_write, path) !=
            file_engine::allow)
            return false;
    }
    return true;
}

inline bool collect_manifest(void* context, const Byte* data, U32 count) {
    auto& manifest = *static_cast<ManifestBuffer*>(context);
    if (count > manifest_bytes - manifest.bytes) return false;
    for (U32 i = 0; i != count; ++i) manifest.text[manifest.bytes++] = data[i];
    return true;
}

// On success, members below prefix + folder start at relative_at. Archive
// parents and the zero-byte marker have no staging destination.
inline zip::Status admit(const zip::Archive& archive, zip::InflateWorkspace& inflate, ManifestBuffer& manifest,
                         Layout& result, xyf_core::Work* work = nullptr) {
    Layout pack = {};
    pack.manifest = ~0u;
    bool saw_marker = false;
    for (U32 i = 0; i != archive.count; ++i) {
        if (!xyf_core::checkpoint(work)) return zip::Status::cancelled;
        const zip::Entry& entry = archive.entries[i];
        if (entry.directory && archive_parent(entry.name)) continue;
        if (same(entry.name, marker)) {
            if (entry.directory || entry.size) return zip::Status::layout;
            saw_marker = true;
            continue;
        }
        if (!starts_with(entry.name, prefix)) return zip::Status::name;
        const char* const id = entry.name + sizeof(prefix) - 1;
        U32 end = 0;
        while (id[end] && id[end] != '/' && end <= name_bytes) ++end;
        if (!end || end > name_bytes || id[end] != '/') return zip::Status::name;
        char folder[name_bytes + 1];
        for (U32 j = 0; j != end; ++j) folder[j] = id[j];
        folder[end] = 0;
        if (!xyf::manifest::pack_name(folder)) return zip::Status::name;
        if (!pack.folder[0]) {
            for (U32 j = 0; j <= end; ++j) pack.folder[j] = folder[j];
            pack.relative_at = sizeof(prefix) + end;
        } else if (!same(folder, pack.folder)) return zip::Status::layout;
        const char* const relative = entry.name + pack.relative_at;
        if (!*relative) {
            if (!entry.directory) return zip::Status::layout;
            continue;
        }
        const U32 size = entry.name_size - pack.relative_at - (entry.directory ? 1 : 0);
        for (U32 pos = 0; pos != size;) {
            U32 e = pos;
            while (e != size && relative[e] != '/') ++e;
            if (e - pos > name_bytes) return zip::Status::name;
            char component[name_bytes + 1];
            for (U32 j = pos; j != e; ++j) component[j - pos] = relative[j];
            component[e - pos] = 0;
            if (!xyf::manifest::pack_name(component)) return zip::Status::name;
            pos = e == size ? e : e + 1;
        }
        if (!valid_output(pack, relative, size, entry.directory)) return zip::Status::bounds;
        if (entry.directory) continue;
        if (same(relative, "manifest.json")) {
            pack.manifest = i;
            if (entry.size > manifest_bytes) return zip::Status::bounds;
        } else if (same(relative, "os.ldr") || same(relative, "info.txt")) return zip::Status::layout;
        ++pack.files;
        if (entry.size > limits().total_bytes - pack.bytes) return zip::Status::bounds;
        pack.bytes += entry.size;
    }
    if (!saw_marker || pack.manifest == ~0u) return zip::Status::layout;
    manifest.bytes = 0;
    zip::Extraction extracted;
    const zip::Status read =
        zip::extract(archive, pack.manifest, inflate, {&manifest, collect_manifest}, extracted, work);
    if (read != zip::Status::ok) return read;
    // The unpacked-pack parser allows an omitted folder. A second check against
    // "/" detects omission because no valid pack folder can match it.
    using xyf_core::mod_content::pack_manifest;
    if (!pack_manifest(manifest.text, manifest.bytes, pack.folder) || pack_manifest(manifest.text, manifest.bytes, "/"))
        return zip::Status::layout;
    result = pack;
    return zip::Status::ok;
}
}}
#endif

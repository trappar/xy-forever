#include "xyf_package_record.hpp"
namespace xyf { namespace package_record {
namespace {
xyf_core::json::Status extract(const xyf_core::json::Document& doc, Workspace& work) {
    using namespace xyf_core::json;
    manifest::begin(work.manifest);
    if (doc.status != ok) return doc.status;
    uint32_t fields = 0, view_bytes = 0;
    for (const json_t* p = doc.root->u.c.child; p; p = p->sibling) {
        if (same(p->name, "schema") || same(p->name, "schema_version")) continue;
        if (++fields > field_count) return capacity;
        uint32_t value_length = 0, name_length = 0;
        const Status status = package_field_size(p, value_length);
        if (status != ok) return status;
        while (p->name[name_length]) ++name_length;
        view_bytes += name_length + 2 + value_length;
        if (view_bytes > manifest::max_text) return capacity;
    }
    if (!get(doc.root, "type")) return value;
    // Validate the entire schema before assigning semantic fields, retaining the
    // original adapter's refusal precedence and empty output on schema errors.
    uint16_t key_line[manifest::key_count] = {};
    fields = 0;
    for (const json_t* p = doc.root->u.c.child; p; p = p->sibling) {
        if (same(p->name, "schema") || same(p->name, "schema_version")) continue;
        ++fields;
        uint32_t name_length = 0, value_length = 0;
        while (p->name[name_length]) ++name_length;
        const manifest::Key key = manifest::find_key(p->name, name_length);
        if (key == manifest::key_unknown) continue;
        // Known semantic fields are text or unsigned integer scalars. Keep the
        // legacy adapter's edge-space trimming, but never serialize/reparse JSON.
        const char* text = p->u.value;
        while (text[value_length]) ++value_length;
        while (value_length && *text == ' ') { ++text; --value_length; }
        while (value_length && text[value_length - 1] == ' ') --value_length;
        if (manifest::field(work.manifest, key, text, value_length, fields) != manifest::ok) return ok;
        key_line[key] = uint16_t(fields);
    }
    manifest::finish(work.manifest, key_line, fields);
    return ok;
}
}
xyf_core::json::Status parse(const char* raw, uint32_t bytes, Workspace& work) {
    return extract(xyf_core::json::parse(raw, bytes, work.json, xyf_core::json::package_schema), work);
}
xyf_core::json::Status view(const char* raw, uint32_t bytes, Workspace& work, manifest::Type expected,
                           char* text, uint32_t& text_bytes) {
    using namespace xyf_core::json;
    const Document doc = xyf_core::json::parse(raw, bytes, work.json, package_schema);
    const Status status = extract(doc, work);
    if (status != ok) return status;
    if (work.manifest.status != manifest::ok || work.manifest.type != expected) return value;
    return package_text(doc, text, manifest::max_text, text_bytes);
}
bool info(const char* raw, uint32_t bytes, Workspace& work, store::Info& out) {
    const xyf_core::json::Status status = parse(raw, bytes, work);
    manifest::legacy_info(work.manifest, out);
    return status == xyf_core::json::ok && work.manifest.status == manifest::ok &&
           work.manifest.type == manifest::type_firmware;
}
} }

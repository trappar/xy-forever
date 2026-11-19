#include "xyf_package_record.hpp"
namespace xyf { namespace package_record {
xyf_core::json::Status parse(const char* raw, uint32_t bytes, Workspace& work) {
    using namespace xyf_core::json;
    // Always clear semantic results, including an earlier successful record.
    manifest::parse("", 0, work.manifest);
    const Document doc = xyf_core::json::parse(raw, bytes, work.json, package_schema);
    if (doc.status != ok) return doc.status;
    uint32_t length = 0;
    const Status status = package_text(doc, work.text, sizeof(work.text), length);
    if (status != ok) return status;
    manifest::parse(work.text, length, work.manifest);
    return ok;
}
bool info(const char* raw, uint32_t bytes, Workspace& work, store::Info& out) {
    const xyf_core::json::Status status = parse(raw, bytes, work);
    manifest::legacy_info(work.manifest, out);
    return status == xyf_core::json::ok && work.manifest.status == manifest::ok &&
           work.manifest.type == manifest::type_firmware;
}
} }

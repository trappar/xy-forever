#ifndef XYF_PACKAGE_RECORD_HPP
#define XYF_PACKAGE_RECORD_HPP
#include "xyf_manifest.hpp"
#include "xyf_store.hpp"
#include "../native/json.hpp"
// The native package semantic reader, owned by its calling scan/job/report. Never put this
// workspace on a firmware stack. Raw bytes remain intact for integrity/identity checks.
namespace xyf { namespace package_record {
struct Workspace {
    xyf_core::json::Workspace json;
    manifest::Manifest manifest;
};
#ifdef __bfin__
static_assert(sizeof(Workspace) == 11468, "measure native package caller workspace after changing it");
#endif
// Decode the selected JSON record directly through the shared semantic package rules.
// JSON errors retain their specific status; semantic failures remain in work.manifest.
xyf_core::json::Status parse(const char* raw, uint32_t bytes, Workspace& work);
bool info(const char* raw, uint32_t bytes, Workspace& work, store::Info& out);
} }
#endif

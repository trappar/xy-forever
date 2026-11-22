#ifndef XYF_CONTENT_HPP
#define XYF_CONTENT_HPP
#include "package.hpp"
namespace xyf_core { namespace content {
enum class Status { ok, argument, capacity, policy, modified, io, source, replace, verify };
enum class Kind { keep, install, erase };
struct Operation {
    Kind kind;
    const package::Item* desired;
    const package::Item* previous;
};
struct Plan {
    const Operation* operations;
    U32 count;
};
struct IO {
    void* context;
    bool (*target_size)(void*, const char* relative, bool& exists, U32& size);
    bool (*read_target)(void*, const char*, U32 offset, Byte*, U32 count);
    bool (*read_staged)(void*, const char*, U32 offset, Byte*, U32 count);
    // Copy verified staged bytes to an owned temporary file, sync, then rename
    // atomically to the content path. Refuse symlinks and verify expected hash.
    bool (*replace)(void*, const char*, U32 size, const hash::Digest&);
    bool (*remove_matching)(void*, const char*, U32 size, const hash::Digest&);
};
struct Report {
    U32 kept, installed, removed;
};
// Previous items are an independently verified canonical generation, never a
// digest learned from a potentially modified current user file. All paths must
// be approved content destinations. Unknown modifications abort before writes.
Status plan(const package::Manifest& desired, const package::Item* previous, U32 previous_count,
            bool remove_previous_only, const IO&, Operation*, U32 capacity, Plan&);
// Rechecks every affected target and changed staged source before first write.
// Interrupted installs can resume: files already equal to desired are skipped.
// Caller persists update intent before apply and publishes default only after
// success. Cross-file changes are not an atomic filesystem transaction.
Status apply(const Plan&, const IO&, Report&);
}}
#endif

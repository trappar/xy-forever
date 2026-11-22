#ifndef XYF_LDR_HPP
#define XYF_LDR_HPP
#include "work.hpp"
namespace xyf_core {
typedef unsigned char Byte;
typedef unsigned int U32;
static_assert(sizeof(U32) == 4, "32-bit words required");
struct Range {
    U32 begin, end;
};
struct Policy {
    const Range* allowed;
    U32 allowed_count;
    const Range* protected_ranges;
    U32 protected_count;
};
enum class Error {
    ok,
    size,
    truncated,
    header,
    flags,
    first,
    overflow,
    expanded,
    final,
    entry,
    range,
    protected_range,
    capacity,
    cancelled
};
struct Summary {
    U32 records, expanded_bytes, entry;
};
// No RAM writes. Policy must include every live source/stack/loader alias.
Error inspect(const Byte* data, U32 size, const Policy& policy, Summary& result);
Error inspect(const Byte* data, U32 size, const Policy& policy, Summary& result, Work* work);
// Expand into scratch only; does not cast a payload address to a pointer.
Error expand(const Byte* data, U32 size, U32 offset, U32 delta, Byte* output, U32 capacity, U32& produced);
const char* error_name(Error error);
}
#endif

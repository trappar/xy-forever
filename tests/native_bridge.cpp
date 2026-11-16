#include "../native/ldr.hpp"
extern "C" int inspect_ldr(const unsigned char* data, unsigned int size,
    const xyf_core::Range* allowed, unsigned int allowed_count,
    const xyf_core::Range* protected_ranges, unsigned int protected_count,
    xyf_core::Summary* summary) {
    const xyf_core::Policy policy = {allowed, allowed_count, protected_ranges, protected_count};
    return static_cast<int>(xyf_core::inspect(data, size, policy, *summary));
}
extern "C" int expand_ldr(const unsigned char* data, unsigned int size,
    unsigned int offset, unsigned int delta, unsigned char* output,
    unsigned int capacity, unsigned int* produced) {
    return static_cast<int>(xyf_core::expand(data, size, offset, delta, output, capacity, *produced));
}

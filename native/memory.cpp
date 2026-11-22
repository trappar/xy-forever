// Freestanding C primitives for compiler-generated aggregate copies.
#include <stddef.h>
#include <stdint.h>
extern "C" void* memcpy(void* destination, const void* source, size_t count) {
    unsigned char* out = static_cast<unsigned char*>(destination);
    const unsigned char* in = static_cast<const unsigned char*>(source);
    for (size_t i = 0; i < count; ++i) out[i] = in[i];
    return destination;
}
extern "C" void* memset(void* destination, int value, size_t count) {
    unsigned char* out = static_cast<unsigned char*>(destination);
    for (size_t i = 0; i < count; ++i) out[i] = static_cast<unsigned char>(value);
    return destination;
}
extern "C" void* memmove(void* destination, const void* source, size_t count) {
    unsigned char* out = static_cast<unsigned char*>(destination);
    const unsigned char* in = static_cast<const unsigned char*>(source);
    if (reinterpret_cast<uintptr_t>(out) < reinterpret_cast<uintptr_t>(in))
        for (size_t i = 0; i < count; ++i) out[i] = in[i];
    else
        for (size_t i = count; i; --i) out[i - 1] = in[i - 1];
    return destination;
}
extern "C" int memcmp(const void* first, const void* second, size_t count) {
    const unsigned char* a = static_cast<const unsigned char*>(first);
    const unsigned char* b = static_cast<const unsigned char*>(second);
    for (size_t i = 0; i < count; ++i)
        if (a[i] != b[i]) return int(a[i]) - int(b[i]);
    return 0;
}

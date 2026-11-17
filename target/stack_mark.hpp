#ifndef XYF_STACK_MARK_HPP
#define XYF_STACK_MARK_HPP
#include <stdint.h>
// Decision 025: B's stack high-water mark. key_ui paints [begin, its SP - 256) with `paint` before the
// menu loop; used() is the number of bytes below `top` that no longer hold the paint (the deepest the stack
// reached since then). STATUS reports it live in usb_service's Result.stack_used.
namespace xy_forever { namespace stack_mark {
const uint32_t paint = 0x5ca1ab1eu;
inline uint32_t used(const volatile uint32_t* begin, const volatile uint32_t* top) {
    const volatile uint32_t* a = begin;
    while (a < top && *a == paint) ++a;
    return uint32_t(top - a) * 4u;
}
}}
#endif

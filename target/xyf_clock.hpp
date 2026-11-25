#ifndef XYF_CLOCK_HPP
#define XYF_CLOCK_HPP
#include <stdint.h>
// Millisecond clock for the cold code (400 MHz CCLK; ms = (cycles >> 10) * 21 / 8192,
// 0.13% fast, as before). The old formula ((units * 21) >> 13) overflowed its 32-bit
// product after ~524 s, which is why link sessions were capped at 300 s and the menu
// had to restart itself. ms_from_cycles is the exact floor(units * 21 / 8192) mod 2^32
// of the full 64-bit cycle counter, computed in 32-bit pieces: it wraps cleanly every
// ~49.7 days, so the unsigned difference of two readings is right across a wrap.
namespace xyf { namespace clock {
__attribute__((always_inline)) inline uint32_t ms_from_cycles(uint32_t low, uint32_t high) {
    const uint32_t units_low = (low >> 10) | (high << 22), units_high = high >> 10;
    // units * 21 = (21 * units_high) * 2^32 + (21 * (units_low >> 16)) * 2^16 + 21 * (units_low & 0xffff);
    // the first two terms are multiples of 2^13, so the floor applies to the last one only.
    return ((21u * units_high) << 19) + ((21u * (units_low >> 16)) << 3) + ((21u * (units_low & 0xffffu)) >> 13);
}
// Milliseconds for a 32-bit difference of cycles >> 10 (intervals up to ~3 h), without overflow.
inline uint32_t ms_from_units(uint32_t units) { return (units >> 13) * 21u + (((units & 0x1fffu) * 21u) >> 13); }
#if defined(__bfin__) && defined(XYF_HOST_CYCLES)
uint64_t cycles();   // a host test program's (tests/tfw_import_source_bridge.cpp), which builds target code as B's
#elif defined(__bfin__)
// the 64-bit cycle counter (400 MHz); always inlined: a call through a register is refused by the call audit
__attribute__((always_inline)) inline uint64_t cycles() {
    uint32_t low, high;
    __asm__ volatile("%0 = CYCLES; %1 = CYCLES2;" : "=d"(low), "=d"(high));
    return uint64_t(high) << 32 | low;
}
#endif
#if defined(__bfin__)
// always inlined too: an out-of-line copy's address, kept in a register for repeated calls, is unresolved in the
// call audit (#314: xy_forever_key_ui's loop)
__attribute__((always_inline)) inline uint32_t ms() {
    const uint64_t c = cycles();
    return ms_from_cycles(uint32_t(c), uint32_t(c >> 32));
}
#endif
}}
#endif

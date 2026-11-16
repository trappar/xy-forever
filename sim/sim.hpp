#ifndef XYF_SIM_HPP
#define XYF_SIM_HPP
// XY Forever device simulator (docs/ARCHITECTURE.md, "Simulator"). Force-included
// (-include) into every device source the simulator hosts; xy_forever/sim.py
// rewrites those sources mechanically so that:
//  - reinterpret_cast<T>(...) becomes xyf_sim::cast<T>(...): a device address
//    (a 32-bit integer) maps to emulated device memory, and a host pointer that
//    device code stores as a 32-bit word gets a stable fake address;
//  - stock<ADDRESS, R>(args...) and cold_storage's call<F>(ADDRESS)(args...)
//    reach the host shims in sim/stock.cpp with the same call shapes;
//  - the Blackfin cycle counter, cache and stack-pointer asm become host calls.
// Nothing here changes what the device code decides.
#include <stdint.h>
#include <stddef.h>
#include <type_traits>
namespace xyf_sim {
void* host(uint32_t address);                 // emulated memory (or a fake address); aborts if unmapped
uint32_t device(uintptr_t pointer);           // inverse of host()
void cycles(uint32_t& low, uint32_t& high);    // CYCLES/CYCLES2 at 400 MHz from the host clock
uint32_t stack_pointer();                     // a fixed address in the emulated module stack
uint64_t stock_call(uint32_t address, const uint64_t* args, unsigned count);

template<class To, class From> inline To cast(From from) {
    if constexpr (std::is_pointer<To>::value && std::is_integral<From>::value)
        return reinterpret_cast<To>(reinterpret_cast<uintptr_t>(host(uint32_t(from))));
    else if constexpr (std::is_integral<To>::value && std::is_pointer<From>::value) {
        if constexpr (sizeof(To) >= sizeof(uintptr_t)) return reinterpret_cast<To>(from);
        else return To(device(reinterpret_cast<uintptr_t>(from)));
    } else return reinterpret_cast<To>(from);
}
template<class T> inline uint64_t pack(T value) {
    if constexpr (std::is_pointer<T>::value) return uint64_t(reinterpret_cast<uintptr_t>(value));
    else return uint64_t(int64_t(value));
}
template<class R> inline R unpack(uint64_t value) {
    if constexpr (std::is_void<R>::value) return;
    else if constexpr (std::is_pointer<R>::value) return reinterpret_cast<R>(uintptr_t(value));
    else return R(value);
}
template<uint32_t A, class R, class... Args> inline R stock(Args... args) {
    const uint64_t packed[] = {pack(args)..., 0};
    return unpack<R>(stock_call(A, packed, sizeof...(Args)));
}
template<class F> struct Call;
template<class R, class... Args> struct Call<R(Args...)> {
    uint32_t address;
    R operator()(Args... args) const {
        const uint64_t packed[] = {pack(args)..., 0};
        return unpack<R>(stock_call(address, packed, sizeof...(Args)));
    }
};
}
#endif

// mtp_usb::stock_port(), the device binding of the MTP driver's Port to the stock 1.2.1
// USB function-driver entry points (docs/hardware/usb.md; USB stack functions only; changing
// the list needs the developer's explicit yes, docs/SAFETY.md). Every main-loop call into the stack runs
// with interrupts masked (002: the stack's own queue code runs unmasked in the USB interrupt).
// Built only with XYF_MTP_USB; the builder's MTP_USB_CALLS lists exactly these addresses.
#include "mtp_usb.hpp"

namespace xyf { namespace mtp_usb {
namespace {
typedef uint32_t U32;
template<U32 A, class R, class... Args> __attribute__((noinline, noclone)) R stock(Args... args) {
    return reinterpret_cast<R(*)(Args...)>(A)(args...);
}
int reg(Driver* d) { return stock<0x88a09d5cu, int>(d); }                       // register driver (slot 0x11803af0)
void unreg(Driver* d) { stock<0x88a09d44u, void>(d); }                          // unregister (only if the slot holds d)
void start(U32 flags) { stock<0x88a35044u, void>(flags); }                      // start(0): ours; 0x400: CDC serial
void stop(U32) { stock<0x88a34fecu, void>(U32(kStopKeepTimer)); }               // stop(0) only
Urb* alloc(uintptr_t dev, U32 ep, U32 len) { return stock<0x88a092eeu, Urb*>(dev, ep, len); }   // urb + buffer
int submit_in(Urb* u) { return stock<0x88a08f24u, int>(u); }                    // queue + start TX (IN only)
void recycle(Urb* u) { stock<0x88a08f08u, void>(u); }                           // OUT urb back to its queue
void free_urb(Urb* u) { stock<0x88a09418u, void>(u); }                          // urb + buffer
#if XYF_MTP_USB_KICK                                                            // off: needs the developer's yes
void kick(Endpoint* e) { stock<0x88a094aau, void>(e); }                         // idle endpoint takes its queue head
void kick_rx(U32 n) {                                                           // service a parked RX packet
    if (!rx_service_allowed(n)) return;                                         // never EP0 or another endpoint
    stock<0x88a0ba08u, void>(n);
}
bool rx_parked(U32 n) {                                                         // MUSB RxCSR: RxPktRdy
    return (*reinterpret_cast<volatile uint16_t*>(0x200d0106u + 0x10u * n) & 1u) != 0;
}
#else
void (*const kick)(Endpoint*) = 0;
void (*const kick_rx)(U32) = 0;
bool (*const rx_parked)(U32) = 0;
#endif
U32 mask() { U32 old; __asm__ volatile("CLI %0;" : "=d"(old) : : "memory"); return old; }
void unmask(U32 old) { __asm__ volatile("STI %0;" : : "d"(old) : "memory"); }
const Port kPort = {reg, unreg, start, stop, alloc, submit_in, recycle, free_urb, kick, kick_rx, rx_parked, mask, unmask};
}
const Port& stock_port() { return kPort; }
}}

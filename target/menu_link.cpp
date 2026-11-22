#include "menu_link.hpp"
#include "xyf_boot.hpp"
// The USB service's command link (see menu_link.hpp).
namespace xy_forever { namespace menu_link {
// Initialized data, not BSS: the cold link has no zero-initialization.
Result result __attribute__((section(".data"))) = {magic};
}}

namespace {
using namespace xy_forever;
using peer_services::Stock121PeerSession;
volatile uint32_t sequence __attribute__((section(".data"))) = 0;
}

namespace xy_forever { namespace menu_link {
bool tick() { return xyf_boot::tick(); }               // Test 065: keepalives on the file boot's session

#if defined(XYF_USB_SERVICE)
// Test 065: one link session per boot: the USB command plumbing goes through the file
// boot's session (b's home loop).
bool usb_link_up() { return xyf_boot::link_up(); }
void usb_patience(uint32_t ms) { xyf_boot::link_patience(ms); }
bool usb_idle() { return xyf_boot::link_idle(); }
uint32_t keepalives() { return xyf_boot::link_keepalives(); }
bool usb_command(uint32_t item) {
    // The low three bits stay 0: should it reach a's bootloader (seconds after a restart), the
    // stock select takes the argument & 7 as the partition: 0, the user area.
    return xyf_boot::link_value(Stock121PeerSession::command_tag << 16 | (item & 0xffu) << 8 |
                                (++sequence << 3 & 0xf8u));
}
bool usb_command_value(uint32_t value) { return xyf_boot::link_value(value); }
#endif
}}

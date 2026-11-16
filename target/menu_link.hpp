#ifndef XYF_MENU_LINK_HPP
#define XYF_MENU_LINK_HPP
#include <stdint.h>
#include "peer_services.hpp"
// The USB service's command link (processor b, boot model v3): every USB command
// item goes to a as one tagged command on the file boot's link session (Test 065:
// one link session per boot; xyf_boot link_*). The Test024 held session that ran
// before file boot, and with it the separate USB service session of builds 034-064,
// is gone (plan 0.4b). Result keeps its layout: STATUS and the census still carry it.
namespace xy_forever { namespace menu_link {
static const uint32_t magic = 0x314b4c4du;           // "MLK1"
struct Result {
    uint32_t magic, version, role, begin_result;
    uint32_t final_result, end_result, polls, safe_handoff;
    uint32_t play, link_ms, release_ms, end_ms;
    uint32_t keepalives, echoes, commands, answers;
    uint32_t activity, commands_seen, last_command, last_argument;
    uint32_t lost_ms, max_gap_ms, stale_deferred, start_cycles;
    peer_services::Diagnostic diagnostic;
};
static_assert(sizeof(Result) == 24 * 4 + sizeof(peer_services::Diagnostic), "Result layout");
extern Result result;
// b: one keepalive pass of the file boot's session (xyf_boot::tick).
bool tick();
#if defined(XYF_USB_SERVICE)
// XY Forever USB service (b): send one tagged command item to a on the file boot's
// session (a's loop answers it). False if a did not answer. usb_link_up(): the
// link is still up.
bool usb_command(uint32_t item);
bool usb_command_value(uint32_t value);     // raw tagged argument (set-default hash bytes)
bool usb_link_up();
uint32_t keepalives();                      // Test 065: keepalive count of the current link
// Test 048: while a writes a state slot it does not answer; b allows one answer
// up to ms (0: back to normal). usb_idle(): up, nothing pending.
void usb_patience(uint32_t ms);
bool usb_idle();
#endif
} }
#endif

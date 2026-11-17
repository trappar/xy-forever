#ifndef XYF_KEY_REPEAT_HPP
#define XYF_KEY_REPEAT_HPP
#include <stdint.h>
#include "xyf_menu.hpp"
// Key repeat for - and +, as on a computer keyboard: holding either repeats its press after
// delay_ms, then every interval_ms. The menu loop learns of presses and releases from the stock PSoC key
// diff (key_ui on_key: pressed 1, then 0); this keeps the one held key and says, once per loop pass, when
// it is due. At most one repeat per pass and the next counted from now, so a long pass (a job slice, a slow
// request) never lets a burst of moves out. Any other key's press, or its own release, ends it. Pure.
namespace xyf { namespace key_repeat {
static const uint32_t delay_ms = 400, interval_ms = 80, none = 0xffffffffu;
struct Held { uint32_t key, due_ms; };
inline void event(Held& h, uint32_t key, bool pressed, uint32_t now_ms) {
    if (pressed && (key == menu::key_minus || key == menu::key_plus)) { h.key = key; h.due_ms = now_ms + delay_ms; }
    else if (pressed || key == h.key) h.key = none;
}
inline bool due(Held& h, uint32_t now_ms) {
    if (h.key == none || int32_t(now_ms - h.due_ms) < 0) return false;
    h.due_ms = now_ms + interval_ms;
    return true;
}
} }
#endif

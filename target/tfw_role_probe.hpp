#ifndef XYF_TFW_ROLE_PROBE_HPP
#define XYF_TFW_ROLE_PROBE_HPP
#include <stdint.h>
namespace xy_forever { namespace tfw_role_probe {
static const uint32_t version = 1, tag = 0x01544600u, tag_mask = 0x00ffff00u;
static const uint32_t max_cut = 300000u, reply_bytes = 40;
}}
#endif

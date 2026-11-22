#ifndef XYF_BOOT_PARTITION_PROBE_HPP
#define XYF_BOOT_PARTITION_PROBE_HPP
#include <stdint.h>
// #7: a developer diagnostic, never a boot writer. Both processors use this version.
namespace xy_forever { namespace boot_partition_probe {
static const uint32_t version = 1, tag = 0x31504200u;
static const uint32_t max_bytes = 0x700000u, sector_bytes = 512;
static const uint8_t command = 0x64;
enum Operation { begin = 0, header = 1, sector = 2, end = 3 };
enum Action { refuse = 0, select, read };
inline Action action(uint32_t cls, uint32_t op, uint32_t argument, uint32_t selected) {
    return cls == 0x11907694u && op == 0x18u && argument <= 2 ? select :
           cls == 0x11907348u && op == 0x11u && selected >= 1 && selected <= 2 && argument <= max_bytes / sector_bytes ?
                                                                read :
                                                                refuse;
}
}}
#endif

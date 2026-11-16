#include "storage_probe.hpp"
namespace xy_forever { namespace storage_probe {
Byte marker_byte(U32 index) { return Byte((index * 73u + 19u) ^ (index >> 1)); }
namespace {
U32 checksum(const Report& report) {
    const Byte* data = reinterpret_cast<const Byte*>(&report);
    U32 crc = 0xffffffffu;
    for (U32 i = 0; i != sizeof(Report); ++i) {
        crc ^= i >= 12 && i < 16 ? 0 : data[i];
        for (U32 bit = 0; bit != 8; ++bit)
            crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}
}
void seal(Report& report) {
    report.magic = 0x32525058u; report.version = 1; report.bytes = sizeof(Report);
    report.checksum = checksum(report);
}
bool valid(const Report& report) {
    return report.magic == 0x32525058u && report.version == 1 &&
        report.bytes == sizeof(Report) && report.checksum == checksum(report);
}
} }

#include "../target/xyf_crc32.hpp"
extern "C" unsigned crc_update(unsigned crc, const unsigned char* data, unsigned n) { return xyf::crc::update(crc, data, n); }
extern "C" unsigned crc_update_volatile(unsigned crc, const unsigned char* data, unsigned n) {
    return xyf::crc::update_volatile(crc, data, n);
}

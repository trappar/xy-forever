#ifndef XYF_CENSUS_HPP
#define XYF_CENSUS_HPP
#include <stdint.h>
// Test017 cold-entry memory census. Read-only: volatile 32-bit loads of
// bootloader-initialized RAM (L1 data/scratch, loaded L2, DDR data/BSS, the
// validated live heap) and a few MMIO words already shown safe or side-effect
// free (IMASK/IPEND/ILAT, PORT A/B/C FER..LOCK). Never L1 instruction memory,
// never uninitialized (ECC) L2, never the unloaded DDR gap. No stock calls.
namespace xy_forever { namespace census {
static const uint32_t magic = 0x31534e43u;   // "CNS1"
static const uint32_t header_bytes = 64;
// Test 043: the product build fills 17 regions; its table holds 24 (other modes
// keep 16, byte-identical). A full table stops the census (no overlap).
#ifndef XYF_CENSUS_REGIONS
#define XYF_CENSUS_REGIONS 16
#endif
static const uint32_t max_regions = XYF_CENSUS_REGIONS;
// Fills out[0..capacity). Returns bytes written (0 on failure); header word 9
// holds a CRC32 of everything after the header.
uint32_t fill(uint8_t* out, uint32_t capacity, uint32_t role, uint32_t gpio);
} }
#endif

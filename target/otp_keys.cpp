#include "otp_keys.hpp"
// Read from the stock 1.2.1 bootloader listing (private/xy-forever/analysis/bootloader-121-listing):
//   11a05a32 init:  if B[0x11b01650] == 0 { R0 = 0; CALL 0x04000054; B[0x11b01650] = 1 }
//   11a05a1e read:  R0 = selector, R1 = buffer; CALL 0x04000048; R0 = the ROM's answer
//   11a05b22 public key, index 0: init; memset(scratch, 0, 0x200); read(9, scratch) != 0;
//            memcpy(out, scratch, 0x38)
//   11a05c9c AES key, index 0: init; memset(scratch, 0, 0x200); read(3, scratch) == 1;
//            read(4, scratch + 0x10) == 1; memcpy(out, scratch, 0x20)
// Stock's scratch is the SDRAM buffer 0x8803d8c4, never cleared afterwards; here it is on the
// stack and cleared. The init flag is the byte the stock wrappers share (on B the stock boot fetch
// 88a07240 has normally set it already, so the ROM init does not run again).
#if defined(__bfin__) && defined(XYF_TFW_CHECK)
namespace xy_forever { namespace otp_keys {
namespace {
using xyf_core::U32;
using xyf_core::Byte;
template<U32 A, class R, class... Args> __attribute__((noinline, noclone)) R stock(Args... args) {
    return reinterpret_cast<R(*)(Args...)>(A)(args...);
}
const U32 init_flag = 0x11b01650u, scratch_bytes = 0x200u;
void wipe(Byte* p, U32 count) { volatile Byte* clear = p; for (U32 i = 0; i != count; ++i) clear[i] = 0; }
void prepare() {
    volatile Byte& initialised = *reinterpret_cast<volatile Byte*>(init_flag);
    if (initialised) return;
    stock<0x04000054u, void>(0u);                        // ROM OTP init
    initialised = 1;
}
U32 rom_read(U32 selector, Byte* buffer) { return stock<0x04000048u, U32>(selector, buffer); }   // ROM OTP read
}

bool public_key(void*, Byte out[56]) {
    wipe(out, public_key_bytes);
    Byte scratch[scratch_bytes + 16] __attribute__((aligned(4)));   // 16 B beyond stock's 0x200
    prepare();
    wipe(scratch, sizeof(scratch));
    const bool answered = rom_read(9u, scratch) != 0;
    if (answered) for (U32 i = 0; i != public_key_bytes; ++i) out[i] = scratch[i];
    wipe(scratch, sizeof(scratch));
    return answered;
}

bool aes_key(void*, U32 index, Byte out[32]) {
    wipe(out, aes_key_bytes);
    if (index) return false;
    Byte scratch[scratch_bytes + 16] __attribute__((aligned(4)));   // 16 B beyond stock's 0x200
    prepare();
    wipe(scratch, sizeof(scratch));
    const bool answered = rom_read(3u, scratch) == 1u && rom_read(4u, scratch + 16) == 1u;
    if (answered) for (U32 i = 0; i != aes_key_bytes; ++i) out[i] = scratch[i];
    wipe(scratch, sizeof(scratch));
    return answered;
}
} }
#endif

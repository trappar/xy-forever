// Included by stock.cpp in namespace xyf_sim.
// ---- B's boot ROM: the OTP init and read (target/otp_keys.cpp) ------------------------------
bool rom_otp_initialised = false;                      // per processor start, as the ROM's own state
void otp_start() {
    if (role != 1) return;
    rom_otp_initialised = shared->otp_preinit.load() != 0;
    w8(0x11b01650u, rom_otp_initialised ? 1 : 0);
}
void rom_otp_failure() {
    if (shared->otp_fail == 5) {
        log("fault: B's OTP ROM hangs until a power cycle");
        for (;;) idle_pause(0, 1000000);              // clearing the fault does not return from a hung ROM call
    }
    if (shared->otp_fail == 6) crash("B's OTP ROM fault");
}
const uint8_t p224_generator[56] = {                   // a valid P-224 point that is not the vendor key
    0xb7, 0x0e, 0x0c, 0xbd, 0x6b, 0xb4, 0xbf, 0x7f, 0x32, 0x13, 0x90, 0xb9, 0x4a, 0x03, 0xc1, 0xd3, 0x56, 0xc2, 0x11,
    0x22, 0x34, 0x32, 0x80, 0xd6, 0x11, 0x5c, 0x1d, 0x21, 0xbd, 0x37, 0x63, 0x88, 0xb5, 0xf7, 0x23, 0xfb, 0x4c, 0x22,
    0xdf, 0xe6, 0xcd, 0x43, 0x75, 0xa0, 0x5a, 0x07, 0x47, 0x64, 0x44, 0xd5, 0x81, 0x99, 0x85, 0x00, 0x7e, 0x34};
uint32_t rom_otp_read(uint32_t selector, uint8_t* out) {
    ++shared->otp_reads;
    rom_otp_failure();
    const int32_t fault = shared->otp_fail;
    if (!rom_otp_initialised || fault == 1 || selector >= 16 || !shared->otp_length[selector]) return 0;
    for (uint32_t i = 0; i != shared->otp_length[selector]; ++i) out[i] = shared->otp_field[selector][i];
    if (selector == 9 && fault == 2) out[55] ^= 1;
    if (selector == 9 && fault == 3) for (uint32_t i = 0; i != 56; ++i) out[i] = p224_generator[i];
    if (selector == 3 && fault == 4) out[0] ^= 0x80;
    return 1;
}

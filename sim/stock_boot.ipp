// Included by stock.cpp; the pinned 1.2.1 writer/wrappers, not device code.
// 889f65f0/683c/6a6a/6abe dispatch by role byte 8807cac4: A local MMC, B RPC.
uint32_t local_select(uint32_t p) {
    if (p > 2) return 1;                              // hosted media has user + two boot partitions
    shared->partition = int32_t(selected = p);
    if (shared->fail_select.load() > 0 && --shared->fail_select == 0) {
        log("fault: select %u switched the partition, then failed", p);
        return 1;
    }
    return 0;
}
uint32_t boot_capacity() { return role ? b_call(0, 5, 0, 0, 0, 0, 0, 0, 0) : shared->capacity; }
uint32_t boot_select(uint32_t p) { return role ? b_call(cls_partition, 0x18, p, 0, 0, 0, 0, 0, 0) : local_select(p); }
uint32_t boot_read(uint32_t sector, uint8_t* out, uint32_t count) {
    return role ? b_call(cls_sector, count == 1 ? 0x11 : 0x12, sector, count, 0, 0, 0, out, count * 512u) :
           sectors_io(false, sector, count, out) ? 0 :
                                                   uint32_t(-1);
}
uint32_t boot_write(uint32_t sector, uint8_t* data, uint32_t count) {
    return role ? b_call(cls_write, sector ? 0x16 : 0x15, sector, count, 0, data, count * 512u, 0, 0) :
           sectors_io(true, sector, count, data) ? 0 :
                                                   uint32_t(-1);
}
uint32_t boot_trim(uint32_t block) {
    return role ? b_call(cls_partition, 0x14, block, 0, 0, 0, 0, 0, 0) : trim(block) ? 0 : uint32_t(-1);
}
// 88a06a7e(R0=writable padded staging, R1=size, R2=optional callback; hosted caller uses 0).
// Size on verified success, 0 on mismatch/invalid input. Exception: initial select failure
// returns the raw nonzero select result (88a06b70..74 -> 88a07156). No bounded wait:
// the subsequent selects/reads/trims/writes retry forever, and B's RPC itself may never return.
uint32_t stock_writer(uint8_t* image, uint32_t size) {
    const uint32_t capacity = boot_capacity();
    if (!image || (uintptr_t(image) & 3u) || size + 512 < 10000 || size + 512 > capacity) return 0;
    uint8_t header[512] = {}, back[512];
    const uint32_t words[3] = {3, size, crc32(image, size)};
    memcpy(header, words, sizeof(words));
    header[12] = 1;
    boot_crypto_mark(header);                        // crypt_prepare success represented, never real AES
    if (const uint32_t error = boot_select(1)) return error;
    while (boot_read(0, back, 1)) {}
    uint32_t target = 1;
    if (back[12]) {                                  // stock tests active byte, not header version
        stock_log("found active firmware, check next boot partition");
        target = 2;
        while (boot_select(2)) {}
        while (boot_read(0, back, 1)) {}
        if (back[12]) stock_log("found active firmware here too...overwrite!");
    }
    char text[96];
    for (uint32_t b = 0; b * erase_block < size + 512; ++b) {
        snprintf(text, sizeof(text), "erase block: %u", b);
        stock_log(text);
        while (boot_trim(b)) {}
    }
    const uint32_t sectors = (size >> 9) + 1;         // stock writes an extra sector for aligned size
    for (uint32_t s = 0; s < sectors; s += 128) {
        const uint32_t n = sectors - s < 128 ? sectors - s : 128;
        uint8_t* chunk = image + s * 512;
        boot_crypt(chunk, n * 512, s * 512);          // mutates padding too, once before write retries
        snprintf(text, sizeof(text), "write sectors: %u-%u", 1 + s, s + n);
        stock_log(text);
        while (boot_write(1 + s, chunk, n)) {}
    }
    bool mismatch = false;
    for (uint32_t s = 0; s != sectors; ++s) {
        while (boot_read(1 + s, back, 1)) {}
        const uint32_t bytes = size - s * 512 < 512 ? size - s * 512 : 512;
        // 88a06f22 resets R7 per sector: only the LAST sector's mismatch survives.
        // In particular the aligned-size extra sector compares no bytes and clears it.
        mismatch = memcmp(back, image + s * 512, bytes) != 0;
    }
    if (mismatch) stock_log("verify failed");
    else {
        stock_log("finally, write sector 0");
        while (boot_write(0, header, 1)) {}
        while (boot_read(0, back, 1)) {}
        mismatch = memcmp(back, header, 512) != 0;
    }
    // 88a06f9a -> 88a070be: even failed verify skips only the new header commit.
    // The old image is still invalidated. A cut after commit can leave both active.
    while (boot_select(3 - target)) {}
    stock_log("erase block 0 to disable other boot partition");
    while (boot_trim(0)) {}
    while (boot_select(0)) {}
    return mismatch ? 0 : size;
}

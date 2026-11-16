#include "boot_services.hpp"
#include "../native/tfw.hpp"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
using namespace xy_forever::services;
static unsigned checks;
static void require(bool ok) { ++checks; if (!ok) { fprintf(stderr, "check %u failed\n", checks); abort(); } }
static void path_tests() {
    const char* bad[] = {0, "", "a.ldr", "/os.ldr", "/yaffs2/xy-forever/", "/yaffs2/xy-forever/../os.ldr",
        "/yaffs2/xy-forever/./os.ldr", "/yaffs2/xy-forever//a.ldr", "/yaffs2/xy-foreverx/a.ldr", "/yaffs2/chained-loader/a.ldr",
        "/yaffs2/xy-forever/a\\b.ldr", "/yaffs2/xy-forever/a:b.ldr", "/yaffs2/xy-forever/a\nb.ldr"};
    for (unsigned i = 0; i != sizeof(bad) / sizeof(bad[0]); ++i) require(!loader_path(bad[i]));
    require(loader_path("/yaffs2/xy-forever/generations/001/payload/os.ldr"));
    const char* xyf_bad[] = {"/yaffs2/xy-forever/", "/yaffs2/xy-forever/../os.ldr", "/yaffs2/xy-foreverx/a",
        "/yaffs2/xy-forever//a", "/yaffs2/xy-forever/a:b", "/yaffs2/xy/a", "/xy-forever/state.txt"};
    for (unsigned i = 0; i != sizeof(xyf_bad) / sizeof(xyf_bad[0]); ++i) require(!loader_path(xyf_bad[i]));
    require(loader_path("/yaffs2/xy-forever/state.txt"));
    require(loader_path("/yaffs2/xy-forever/firmware/opxy-121/os.ldr"));
    char long_path[300]; memset(long_path, 'a', sizeof(long_path)); long_path[299] = 0;
    require(!loader_path(long_path));
}
struct FakeOtp {
    unsigned prepares, reads; U32 selectors[2]; bool prepare_ok; U32 fail_selector;
    FakeOtp() : prepares(0), reads(0), prepare_ok(true), fail_selector(0) {}
};
static bool otp_prepare(void* user) { FakeOtp& otp = *static_cast<FakeOtp*>(user); ++otp.prepares; return otp.prepare_ok; }
static U32 otp_read(void* user, U32 selector, Byte* scratch, U32 capacity) {
    FakeOtp& otp = *static_cast<FakeOtp*>(user);
    require(capacity == 512 && !(reinterpret_cast<uintptr_t>(scratch) & 7u));
    require(otp.reads < 2); otp.selectors[otp.reads++] = selector;
    for (U32 i = 0; i != capacity; ++i) require(scratch[i] == 0);
    for (U32 i = 0; i != capacity; ++i) scratch[i] = static_cast<Byte>(selector);
    return selector == otp.fail_selector ? 0 : 1;
}
static void otp_tests() {
    FakeOtp otp; const OtpIO io = {&otp, otp_prepare, otp_read}; Byte key[32];
    require(read_update_key(io, 0, key));
    require(otp.prepares == 1 && otp.reads == 2 && otp.selectors[0] == 3 && otp.selectors[1] == 4);
    for (unsigned i = 0; i != 32; ++i) require(key[i] == (i < 16 ? 3 : 4));
    otp.reads = 0;
    require(read_update_key(io, 1, key));
    require(otp.selectors[0] == 5 && otp.selectors[1] == 6);
    for (unsigned i = 0; i != 32; ++i) require(key[i] == (i < 16 ? 5 : 6));
    otp.reads = 0; otp.fail_selector = 4;
    require(!read_update_key(io, 0, key) && otp.reads == 2);
    for (unsigned i = 0; i != 32; ++i) require(key[i] == 0);
    otp.reads = 0; const unsigned prepares = otp.prepares;
    require(!read_update_key(io, 2, key) && !otp.reads && otp.prepares == prepares);
    otp.prepare_ok = false;
    require(!read_update_key(io, 0, key) && !otp.reads);
    require(!read_update_key(io, 0, 0));
}
static void binding_tests() {
    xyf_core::tfw::KeyProvider provider = {};
    require(!stock121_otp_provider(Phase::running_os, provider) && !provider.read);
    require(!stock121_otp_provider(Phase::unknown, provider) && !provider.read);
    require(!stock121_otp_provider(Phase::cold_after_teardown, provider) && !provider.read); // Host never casts firmware addresses.
}
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    if (!strcmp(argv[1], "path")) path_tests();
    else if (!strcmp(argv[1], "otp")) otp_tests();
    else if (!strcmp(argv[1], "binding")) binding_tests();
    else return 2;
    printf("%u checks passed\n", checks); return 0;
}

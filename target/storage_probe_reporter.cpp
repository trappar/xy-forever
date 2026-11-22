#include "storage_probe.hpp"
#ifndef XYF_REPORT_PATH
#define XYF_REPORT_PATH "/yaffs2/xy-forever/log/report.bin"
#endif
extern "C" void xy_forever_storage_report() __asm__("xy_forever_storage_report");
extern "C" void xy_forever_storage_report() {
    using namespace xy_forever::storage_probe;
    if ((*reinterpret_cast<volatile U32*>(0x2004000cu) >> 2) & 1u) return;
    // Exact OS121 filesystem_startup success flag, set after mount_all returns0.
    if (*reinterpret_cast<volatile Byte*>(0x8400e941u) != 1) return;
    const Report& report = *reinterpret_cast<const Report*>(carry_base);
    if (!valid(report)) return;
    typedef void* Open(const char*, const char*);
    typedef U32 Write(const void*, U32, U32, void*);
    typedef int Close(void*);
    void* version = reinterpret_cast<Open*>(0x8fed0348u)("/yaffs2/firmware.version", "rb");
    if (!version) return;
    if (reinterpret_cast<Close*>(0x8fed00aeu)(version)) return;
    // The operator supplies this directory. No mkdir, user-file scan or cleanup
    // occurs here. File creation is deferred until the normal OS has mounted FS.
#if defined(XYF_CENSUS_BYTES)
    // Test017: one file per processor, named by the cold role byte; report
    // followed by the census written below the reporter (excluded from heap).
    char path[] = XYF_REPORT_PATH;
    for (U32 i = 0; path[i]; ++i)
        if (path[i] == '#') path[i] = char('a' + (report.diagnostic.before.role & 15u));
    void* file = reinterpret_cast<Open*>(0x8fed0348u)(path, "wb");
    if (!file) return;
    reinterpret_cast<Write*>(0x8fed0780u)(&report, 1, sizeof(report), file);
    const U32 census = report.marker_size <= XYF_CENSUS_BYTES ? report.marker_size : 0;
    if (census) reinterpret_cast<Write*>(0x8fed0780u)(reinterpret_cast<const void*>(XYF_CENSUS_BASE), 1, census, file);
    reinterpret_cast<Close*>(0x8fed00aeu)(file);
#else
    void* file = reinterpret_cast<Open*>(0x8fed0348u)(XYF_REPORT_PATH, "wb");
    if (!file) return;
    reinterpret_cast<Write*>(0x8fed0780u)(&report, 1, sizeof(report), file);
    reinterpret_cast<Close*>(0x8fed00aeu)(file);
#endif
}

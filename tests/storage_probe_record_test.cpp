#include "../target/storage_probe.hpp"
#include <assert.h>
#include <stdio.h>
int main(int argc, char** argv) {
    using namespace xy_forever::storage_probe;
    Report report = {};
    assert(!valid(report));
    report.safe_handoff = 1;
    report.marker_size = report.marker_read = 512;
    report.diagnostic.magic = 0x53444358;
    report.diagnostic.version = 1;
    seal(report);
    assert(valid(report));
    Byte* bytes = reinterpret_cast<Byte*>(&report);
    for (U32 i = 0; i != sizeof(report); ++i) {
        bytes[i] ^= Byte(1u << (i % 8));
        assert(!valid(report));
        bytes[i] ^= Byte(1u << (i % 8));
    }
    assert(valid(report));
    for (U32 i = 0; i != 512; ++i)
        assert(marker_byte(i) == Byte((i * 73u + 19u) ^ (i >> 1)));
    if (argc == 2) {
        FILE* file = fopen(argv[1], "rb");
        assert(file);
        Report fixture;
        assert(fread(&fixture, 1, sizeof(fixture), file) == sizeof(fixture));
        assert(fgetc(file) == EOF);
        assert(fclose(file) == 0);
        assert(valid(fixture));
        assert(fixture.begin_result == 1 && fixture.read_result == 1 && fixture.cleanup_result == 1);
        assert(fixture.safe_handoff == 1 && fixture.marker_read == 0);
        assert(fixture.diagnostic.stage == xy_forever::cold_storage::Stage::idle);
        assert(fixture.heap_result == xy_forever::cold_heap::Result::unavailable);
    }
    return 0;
}

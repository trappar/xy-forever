// target/storage_probe.hpp: the report's checksum and the marker bytes. The fixture case (skipped unless asked
// for) checks the Python writer's report-only fixture, the file XYF_PROBE_FIXTURE names (tests/test_storage_probe.py).
#include "doctest.h"
#include "storage_probe.hpp"
#include <stdio.h>
#include <stdlib.h>
using namespace xy_forever::storage_probe;

TEST_CASE("report checksum and marker") {
    Report report = {};
    REQUIRE(!valid(report));
    report.safe_handoff = 1;
    report.marker_size = report.marker_read = 512;
    report.diagnostic.magic = 0x53444358;
    report.diagnostic.version = 1;
    seal(report);
    REQUIRE(valid(report));
    Byte* bytes = reinterpret_cast<Byte*>(&report);
    for (U32 i = 0; i != sizeof(report); ++i) {
        bytes[i] ^= Byte(1u << (i % 8));
        REQUIRE(!valid(report));
        bytes[i] ^= Byte(1u << (i % 8));
    }
    REQUIRE(valid(report));
    for (U32 i = 0; i != 512; ++i)
        REQUIRE(marker_byte(i) == Byte((i * 73u + 19u) ^ (i >> 1)));
}

TEST_CASE("the report-only fixture" * doctest::skip()) {
    const char* path = getenv("XYF_PROBE_FIXTURE");
    REQUIRE(path);
    FILE* file = fopen(path, "rb");
    REQUIRE(file);
    Report fixture;
    REQUIRE(fread(&fixture, 1, sizeof(fixture), file) == sizeof(fixture));
    REQUIRE(fgetc(file) == EOF);
    REQUIRE(fclose(file) == 0);
    REQUIRE(valid(fixture));
    REQUIRE((fixture.begin_result == 1 && fixture.read_result == 1 && fixture.cleanup_result == 1));
    REQUIRE((fixture.safe_handoff == 1 && fixture.marker_read == 0));
    REQUIRE(fixture.diagnostic.stage == xy_forever::cold_storage::Stage::idle);
    REQUIRE(fixture.heap_result == xy_forever::cold_heap::Result::unavailable);
}

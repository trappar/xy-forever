// target/cold_storage.hpp: the readiness policy (no card, mount or target execution).
#include "doctest.h"
#include "cold_storage.hpp"
using namespace xy_forever;
using namespace xy_forever::cold_storage;
TEST_CASE("retained state requires a local, unmounted, IRQ-ready device") {
    // Exact controller/IRQ state reported by hardware Tests 008 and 009.
    Snapshot fresh = {};
    fresh.initialized = 1; fresh.references = 1; fresh.handle = 0x11b00200u;
    fresh.interrupt_mask = 0x881fu; fresh.source_control = 5; fresh.vector_index = 28;
    fresh.vector_handler = 0x88a061d6u; fresh.vector_context = 0x11b00200u;
    fresh.rsi_control = 16; fresh.rsi_mask = 0xffc2u; fresh.rsi_status = 0x10021u;
    fresh.rsi_configuration = 0x607f0080u; fresh.rsi_clock = 0x2625a0u;
    fresh.completion = 0x88072d8cu; fresh.completion_initialized = 1;
    fresh.adma_control = 0x600u; fresh.adma_pointer = 0x11b00200u;
    REQUIRE(check_fresh(fresh) == Result::ok);
    Snapshot state = fresh; state.initialized = 0; REQUIRE(check_fresh(state) == Result::lifecycle);
    state = fresh; state.device = 0x88080000u; REQUIRE(check_fresh(state) == Result::device);
    state = fresh; state.readonly = 1; REQUIRE(check_fresh(state) == Result::lifecycle);
    state = fresh; state.role = 1; REQUIRE(check_fresh(state) == Result::role);
    state = fresh; state.references = 0; REQUIRE(check_fresh(state) == Result::lifecycle);
    state = fresh; state.adma_control = 0x80; REQUIRE(check_fresh(state) == Result::lifecycle);
    state = fresh; state.vector_handler += 2; REQUIRE(check_fresh(state) == Result::irq);
    Snapshot mounted_fresh = fresh; Snapshot before_fresh = fresh;
    before_fresh.device = mounted_fresh.device = 0x88aa5598u;
    mounted_fresh.mounted = mounted_fresh.readonly = 1;
    REQUIRE(check_fresh_active(before_fresh, mounted_fresh) == Result::ok);
    state = mounted_fresh; state.references = 2;   // init must stay a no-op
    REQUIRE(check_fresh_active(before_fresh, state) == Result::lifecycle);
    state = mounted_fresh; state.readonly = 0;
    REQUIRE(check_fresh_active(before_fresh, state) == Result::lifecycle);
    state = mounted_fresh; state.initialized = 0;   // shutdown must not happen
    REQUIRE(check_fresh_active(before_fresh, state) == Result::lifecycle);
    state = mounted_fresh; state.source_control = 4;
    REQUIRE(check_fresh_active(before_fresh, state) == Result::irq);
    U32 device = 1, mismatch = 0;
    REQUIRE((construct_fresh(device, mismatch) == Result::unavailable && !device && mismatch == 0xffffffffu));
    Diagnostic diagnostic = {};
    Stock121ReadOnlySession session(diagnostic);
    REQUIRE((!session.ready() && !session.direct_ready()));
    REQUIRE(session.begin_fresh(services::Phase::live_bootloader) == Result::unavailable);
    REQUIRE(session.begin_fresh(services::Phase::cold_after_teardown) == Result::unavailable);
    REQUIRE((diagnostic.stage == Stage::failed && diagnostic.result == Result::unavailable));
    REQUIRE((session.direct_open("/yaffs2/xy-forever/state.txt") < 0 && !session.direct_opendir("/yaffs2/xy-forever")));
}

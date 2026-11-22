// target/peer_services.hpp's host-checkable parts: DMA ranges, request slots, the lifecycle checks of a
// snapshot (its bytes, as the struct lays them out), command requests, and a session that cannot go live here.
#include <cstdlib>
#include <cstring>
#include <new>
#include <nanobind/nanobind.h>
#include <nanobind/stl/tuple.h>
#include "target/peer_services.hpp"

namespace nb = nanobind;
using namespace xy_forever;

void bind_peer_services(nb::module_ m) {
    m.def("dma_range", [](uint32_t address, uint32_t count) { return peer_services::dma_buffer_range(address, count); });
    m.def("request_slot", [](uint32_t driver, uint32_t request) {
        return peer_services::request_pool_slot(driver, request);
    });
    m.def("snapshot_bytes", [] { return unsigned(sizeof(peer_services::Snapshot)); });
    // check(which, snapshot bytes): 0 closed, 1 open, 2 drained, 3 quiet -> the Result
    m.def("check", [](unsigned which, nb::bytes data) {
        peer_services::Snapshot s;
        if (data.size() != sizeof(s)) throw nb::value_error("not a Snapshot's size");
        std::memcpy(&s, data.data(), sizeof(s));
        return unsigned(which == 0 ? peer_services::check_closed(s) : which == 1 ? peer_services::check_open(s) :
                        which == 3 ? peer_services::check_quiet(s) : peer_services::check_drained(s));
    });
    m.def("command_request", [](uint32_t op, uint32_t a0, uint32_t a1) {
        return peer_services::command_request(op, a0, a1);
    });
    m.def("command_ack", [](uint32_t value) { return peer_services::command_ack(value); });
    // host_session() -> (ready, begin's Result, ready after): a session built in zeroed memory, begun as the host.
    m.def("host_session", [] {
        void* memory = std::calloc(1, sizeof(peer_services::Stock121PeerSession));
        void* diagnostic = std::calloc(1, sizeof(peer_services::Diagnostic));
        if (!memory || !diagnostic) { std::free(memory); std::free(diagnostic); throw std::bad_alloc(); }
        peer_services::Stock121PeerSession* session =
            new (memory) peer_services::Stock121PeerSession(*static_cast<peer_services::Diagnostic*>(diagnostic));
        const bool before = session->safe_to_handoff();
        const unsigned begun =
            unsigned(session->begin(services::Phase::cold_after_teardown, 0, 65536, 0, 65536, 0, 1000));
        const bool after = session->safe_to_handoff();
        std::free(memory);
        std::free(diagnostic);
        return std::make_tuple(before, begun, after);
    });
}

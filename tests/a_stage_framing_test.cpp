// Exercise the production quarantine seam with independently addressed stock request fields.
// State/admission and storage effects are covered by test_a_install_stage's full simulator.
#include <assert.h>
#include <stdint.h>
#include <map>
using U32 = uintptr_t;
std::map<U32, U32> fields;
U32& word(U32 address) { return fields[address]; }
U32 byte(U32 address) { return fields[address]; }
namespace xyf {
namespace clock { U32 ms() { return 1; } }
namespace usbp {
enum { s_ok = 0, s_bad_request = 1, s_busy = 2 };
U32 get32(const uint8_t* p) {
    return U32(p[0]) | U32(p[1]) << 8 | U32(p[2]) << 16 | U32(p[3]) << 24;
}
}
}
namespace xy_forever { namespace a_install_stage {
enum { handle = 0x41535447, magic = 0x31534941, version = 1, header_bytes = 32,
       begin = 0, digest = 3, preflight = 5 };
unsigned applied = 0;
struct State {
    U32 active, admitted;
    bool expired(U32) { return false; }
    void clear() { active = admitted = 0; }
    U32 apply(const uint8_t*, U32, uint8_t*, U32, U32, U32& value) {
        ++applied; value = 1; return 0;
    }
};
} }
namespace file_engine { enum { query_class = 7 }; }
namespace file_client { enum { piece_max = 4096 }; }
namespace peer_services { bool boot_request(U32, U32, U32, U32) { return false; } }
namespace file_boot {
uint8_t staging[512];
const U32 staging_bytes = sizeof(staging);
void discard_prepared() { assert(false); } // preflight must not discard the normal lease
}
enum { write_class = 0x11907710, sector_class = 9, error_busy = 16, entry_read = 1 };
uint8_t probe_zero[512];
bool probe_mode = false, window_open = false;
U32 engine = entry_read;
bool boot_selected() { return false; }
bool held_files() { return false; }
template<U32, typename R, typename... Args> R stock(Args...) { assert(false); }
struct Stock121PeerSession {
    unsigned replies = 0;
    U32 result = 0, error = 0;
    void reply(U32, U32 r, U32 e) { ++replies; result = r; error = e; }
};
#define XYF_TFW_CHECK 1
#define __attribute__(x) // The firmware's ELF data section has no role in this host-only seam test.
#include "../target/storage_server_a_stage.ipp"
#undef __attribute__

int main() {
    const U32 request = 0x1000;
    const uint8_t body[32] = {0x41, 0x49, 0x53, 0x31, 1, 0, 0, 0, 5};
    auto frame = [&](U32 queue, U32 count, U32 length, U32 pointer) {
        fields.clear();
        word(request) = write_class; word(request + 5) = 0x2b;
        word(request + 8) = ais::handle; word(request + 0xc) = queue;
        word(request + 0x18) = count; word(request + 0x1c) = 0;
        word(request + 0x20) = pointer;
        word(request + 0x24) = length;
        a_stage.clear(); ais::applied = 0;
    };
    // A real unlinked stock request has zero queue linkage and a separate count.
    // A nonzero next-link must likewise never be interpreted as the write count.
    for (U32 queue : {U32(0), U32(0x12345678)}) {
        frame(queue, sizeof(body), sizeof(body), reinterpret_cast<U32>(body));
        Stock121PeerSession s;
        assert(stage_query(request, s));
        assert(s.replies == 1 && s.result == 0 && s.error == 1 && ais::applied == 1);
    }
    // Queue linkage matching the body cannot rescue a wrong count, length or pointer.
    for (auto shape : {0, 1, 2, 3, 4}) {
        frame(32, shape == 0 ? 0 : shape == 1 ? 31 : 32,
              shape == 2 ? 31 : shape == 3 ? 4097 : 32,
              shape == 4 ? 0 : reinterpret_cast<U32>(body));
        a_stage.active = 1;
        Stock121PeerSession s;
        assert(stage_query(request, s));
        assert(s.replies == 1 && s.result == xyf::usbp::s_bad_request);
        assert(!a_stage.active && !ais::applied);
    }
    // An inactive sentinel in the wrong operation stays quarantined before any FD dispatch.
    frame(0, 32, 32, reinterpret_cast<U32>(body));
    word(request + 5) = 0x15;
    Stock121PeerSession s;
    assert(stage_query(request, s));
    assert(s.replies == 1 && s.result == U32(0xffffffff) && s.error == error_busy);
    assert(!ais::applied);
}

// Execute production role-A dispatch with controlled IRQ arrival timing.
// Memory and transport are substitutes; this does not simulate the hardware.
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <map>
using U32 = uintptr_t;
using Byte = unsigned char;
namespace xyf_core { namespace peer {
enum class Phase { waiting, release };
enum class Status { ok, waiting, identity, protocol };
struct Request { U32 operation, argument0, argument1; };
struct Server {
    unsigned calls = 0;
    Phase phase() { return Phase::waiting; }
    bool ready() { return false; }
    void begin() {}
    Status process(Request, Byte*, U32, U32&) { ++calls; return Status::waiting; }
};
}}
enum class Result { waiting, choose_image, protocol };
enum class Stage { serving, draining };
struct Diagnostic {
    struct { U32 protocol_state = 1; } after;
    U32 link_initialized = 1, last_operation = 0, responses = 0;
    Stage stage = Stage::serving;
};
alignas(8) Byte request_bytes[64] = {};
const U32 request_address = reinterpret_cast<U32>(request_bytes);
const U32 primary_address = 0x1000;
std::map<U32, U32> words;
unsigned reads, arrive_on_read;
struct Word {
    U32 address;
    operator U32() const {
        if (address == primary_address + 4 && ++reads == arrive_on_read)
            words[address] = request_address;
        return words[address];
    }
    Word& operator=(U32 value) { words[address] = value; return *this; }
    Word& operator=(const Word& other) { return *this = U32(other); }
};
Word word(U32 address) { return {address}; }
Byte byte(U32 address) {
    if (address == primary_address) return 0;
    return *reinterpret_cast<Byte*>(address);
}
bool request_pool_slot(U32, U32 request) { return request == request_address; }
bool command_request(U32 op, U32 a0, U32 a1) {
    return op == 0x18 && (a0 >> 16) == 0x434d && !a1;
}
U32 command_ack(U32 value) { return ~value; }
bool boot_request(U32 vtable, U32 op, U32 a0, U32 a1) {
    if (vtable == 0x11907694u) return op == 0x18 && !command_request(op, a0, a1);
    return vtable == 0x11907348u && (op == 0x11 || op == 0x12);
}
template<U32 A, class R, class... Args> R stock(Args...) { return R(); }
struct Session {
    Diagnostic diagnostic_;
    xyf_core::peer::Server server_;
    U32 primary_ = primary_address, driver_ = 0, pending_ = 0;
    U32 commands_seen_ = 0, last_command_ = 0, last_argument_ = 0, last_activity_ = 0;
    U32 served_ = 0, scratch_capacity_ = 0, frozen_size_ = 0, blobs_ = 0;
    bool staged_ = true, agreed_ = false, handshake_only_ = false, rpc_ = true;
    bool blob_ = false, served_activity_ = false, committed_ = false, declined_ = false;
    bool peer_restart_ = false;                  // F1 (decision 016)
    U32 peer_request_ = 0, peer_ms_ = 0;
    Byte* scratch_ = nullptr;
    void* serve_context_ = nullptr;
    using Serve = void(*)(void*, U32, Session&);
    static void serve(void*, U32, Session& self) { ++self.handler_calls; }
    Serve serve_ = serve;
    unsigned handler_calls = 0;
    bool declined() { return declined_; }
    Result fail(Result r) { return r; }
    void respond(U32) { pending_ = 0; }
    Result dispatch(U32 now) {
#include "role_a_dispatch.inc"
        return Result::waiting;
    }
};
void reset(unsigned arrival, bool command) {
    words.clear(); reads = 0; arrive_on_read = arrival;
    for (Byte& b : request_bytes) b = 0;
    request_bytes[5] = command ? 0x18 : 0x16;
    words[request_address] = command ? 0x11907694u : 0x11907348u;
    words[request_address + 8] = command ? 0x434d0401u : 0;
}
int main() {
    for (unsigned arrival = 1; arrival <= 3; ++arrival) {
        for (bool command : {false, true}) {
            for (unsigned op : {0x11u, 0x16u}) {
                reset(arrival, command); Session s;
                if (!command) request_bytes[5] = op;
                for (unsigned poll = 1; poll <= arrival; ++poll) {
                    Result r = s.dispatch(100 + poll);
                    assert(r != Result::protocol);
                    if (poll < arrival) {
                        assert(s.handler_calls == 0 && s.commands_seen_ == 0);
                        assert(r == Result::choose_image);
                    }
                }
                assert(s.handler_calls == (command ? 0u : 1u));
                assert(s.commands_seen_ == (command ? 1u : 0u));
                assert(s.server_.calls == 0);
                assert(s.pending_ == 0 && words[primary_address + 4] == 0);
                assert(s.dispatch(200) == Result::choose_image);
                assert(s.handler_calls + s.commands_seen_ == 1);
            }
        }
    }
    // Non-RPC transfer requests reach the image server and stay pending while a blob or image is frozen.
    reset(1, false); request_bytes[5] = 0x11; Session image;
    image.rpc_ = false; image.blob_ = true;
    assert(image.dispatch(100) == Result::choose_image);
    assert(image.server_.calls == 1 && image.handler_calls == 0);
    assert(image.pending_ == request_address);
    assert(image.dispatch(101) == Result::choose_image);
    assert(image.server_.calls == 2);
    // F1 (decision 016): with nothing frozen a boot request is b's bootloader (b restarted alone): taken, held
    // unanswered, nothing else runs, and every later poll serves nothing.
    for (unsigned op : {0x11u, 0x12u, 0x18u}) {
        reset(1, op == 0x18); request_bytes[5] = Byte(op); words[request_address + 8] = op == 0x18 ? 1 : 0;
        Session boot; boot.rpc_ = false;
        assert(boot.dispatch(100) == Result::waiting);
        assert(boot.peer_restart_ && boot.peer_request_ == request_address && boot.pending_ == 0);
        assert(words[primary_address + 4] == 0 && boot.server_.calls == 0 && boot.handler_calls == 0);
        assert(boot.diagnostic_.responses == 0);
        reset(1, true); boot.peer_request_ = request_address;
        assert(boot.dispatch(101) == Result::waiting && boot.commands_seen_ == 0);   // not even a command
    }
    // In RPC mode the serve hook decides (it knows install mode): a boot request goes to it.
    reset(1, false); request_bytes[5] = 0x12; Session rpc;
    assert(rpc.dispatch(100) == Result::waiting && rpc.handler_calls == 1 && !rpc.peer_restart_);
    // After b's handshake was seen, a boot request is held in RPC mode too.
    reset(1, false); request_bytes[5] = 0x11; Session seen; seen.peer_restart_ = true;
    assert(seen.dispatch(100) == Result::waiting && seen.handler_calls == 0 && seen.peer_request_ == request_address);
    // The legacy held service still serves requests through its own branch.
    reset(1, false); Session legacy;
    legacy.staged_ = false; legacy.handshake_only_ = true;
    assert(legacy.dispatch(100) == Result::waiting);
    assert(legacy.handler_calls == 1);
    // An invalid pool address must never reach a handler.
    reset(0, false); words[primary_address + 4] = 0xdead; Session invalid;
    assert(invalid.dispatch(100) == Result::protocol);
    assert(invalid.handler_calls == 0);
    puts("dispatch arrival checks passed");
}

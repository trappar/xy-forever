// The link between the processors (stock SPI requests), simulated. Each processor has a
// receiving thread that plays its stock SPI interrupt code:
//  - B: completes the request a response belongs to (the stock master's request object: the
//    done byte at +4 and the result word at +0x10; data into the caller's buffer);
//  - A: answers generic ops itself as the stock slave ISR does (1 and 10 handshake, op-10
//    echoes with the RX header at 0x11b000c0, 7 release, 5 boot capacity) and queues every
//    class request into a slave pool slot (driver + 0x3c, 0x9c, 0xfc, 0x15c), published at
//    primary + 4 for A's loop (Stock121PeerSession::poll) to take; the answer leaves when
//    A's loop or a stock executor queues it (889f6508 -> a_respond).
// Slot layout used by the simulator (the fields storage_server.cpp and peer_services.cpp
// read are the stock ones): +0 class, +5 op, +8 argument 0, +0x10 result,
// +0x14 errno, +0x18 argument 1 (write count; paths: the stock path buffer 0x8803f6e0),
// +0x1c request body bytes (16 bit; zero for writes), +0x20 body address, +0x24 body bytes.
// Write-class +0xc is queue linkage, cleared by 889f652a; other builders retain their model.
#include "runtime.hpp"
#include <deque>
#include <errno.h>
#include <map>
#include <mutex>
#include <poll.h>
#include <string.h>
#include <sys/uio.h>
#include <thread>
#include <unistd.h>
#include <vector>

namespace xyf_sim {
std::atomic<int> a_held{0};
// Both sides of a link (the board and each processor).
bool send_message(int fd, const Message& m, const void* body) {
    static std::mutex lock;
    std::lock_guard<std::mutex> hold(lock);
    if (role == 0 || role == 1) { ++shared->outflight[role]; active(); }   // sim/clock.cpp
    // Header and body in one writev: the reader wakes once, with the whole message there.
    iovec v[2] = {{const_cast<Message*>(&m), sizeof(m)}, {const_cast<void*>(body), body ? m.body : 0}};
    for (int k = 0, count = v[1].iov_len ? 2 : 1; k != count;) {
        const ssize_t n = writev(fd, v + k, count - k);
        if (n < 0 && errno == EINTR) continue;
        if (n < 0 && errno == EAGAIN) { pollfd p = {fd, POLLOUT, 0}; ::poll(&p, 1, -1); continue; }
        if (n <= 0) return false;
        size_t done = size_t(n);
        for (; k != count && done >= v[k].iov_len; ++k) done -= v[k].iov_len;
        if (k != count) { v[k].iov_base = static_cast<uint8_t*>(v[k].iov_base) + done; v[k].iov_len -= done; }
    }
    return true;
}

namespace {
struct Wait {
    std::atomic<int> done{0};
    uint32_t result = 0, error = 0;
    void* out = 0; uint32_t capacity = 0, got = 0;   // got: the bytes of a streamed answer placed so far
    uint32_t* session = 0;          // a session request_ array: done byte +4, result +0x10
    bool queued = false;            // held by the b_queue hook (counted in b_queued until answered)
};
std::mutex lock;
std::map<uint32_t, Wait*> waits;
// Tags differ per processor start, so a late answer to the previous B (a lone reset) never
// completes a request of the new one.
uint32_t next_tag = (uint32_t(now_us()) & 0x7fff0000u) | 1u;
struct Built { uint32_t cls, op, a0, a1, a2; const void* body; uint32_t bytes; void* out; uint32_t capacity; };
std::map<void*, Built> built;
std::map<void*, Wait*> session_waits;
std::deque<void*> held;                                     // b_queue: submitted, not yet serialized

std::vector<uint8_t> link_in;                               // read from the link, not yet a whole message
// The next message, reading what the link has in one call (no system call while one is buffered); false when
// the link thread's wake pipe fired first (kick(): a control command, or A's slave opened with requests held).
bool receive(Message& m, std::vector<uint8_t>& body) {
    for (;;) {
        if (link_in.size() >= sizeof(m)) {
            memcpy(&m, link_in.data(), sizeof(m));
            if (m.magic != message_magic) exit_processor(exit_halt);
            if (link_in.size() >= sizeof(m) + m.body) {
                body.assign(link_in.begin() + sizeof(m), link_in.begin() + long(sizeof(m) + m.body));
                link_in.erase(link_in.begin(), link_in.begin() + long(sizeof(m) + m.body));
                return true;
            }
        }
        pollfd p[2] = {{link_fd, POLLIN, 0}, {shared->wake[3 + role][0], POLLIN, 0}};
        if (::poll(p, 2, -1) <= 0) continue;
        if (p[1].revents) { char b[64]; while (read(p[1].fd, b, sizeof(b)) > 0) {} }
        if (!(p[0].revents & (POLLIN | POLLHUP | POLLERR))) return false;
        uint8_t chunk[65536];
        const ssize_t n = read(link_fd, chunk, sizeof(chunk));
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) exit_processor(exit_halt);                // the board is gone: end with it
        link_in.insert(link_in.end(), chunk, chunk + n);
    }
}

// ---- B -----------------------------------------------------------------------------------------
// The stock master takes each body of an answer into the caller's buffer after the ones before it.
void place(Wait* w, const std::vector<uint8_t>& body) {
    if (!w->out || body.empty() || w->got >= w->capacity) return;
    const uint32_t n = body.size() < w->capacity - w->got ? uint32_t(body.size()) : w->capacity - w->got;
    memcpy(static_cast<uint8_t*>(w->out) + w->got, body.data(), n);
    w->got += n;
}
void more(const Message& m, const std::vector<uint8_t>& body) {     // a streamed answer's body (k_body)
    std::lock_guard<std::mutex> hold(lock);
    auto it = waits.find(m.tag);
    if (it != waits.end()) place(it->second, body);
}
void complete(const Message& m, const std::vector<uint8_t>& body) {
    std::lock_guard<std::mutex> hold(lock);
    auto it = waits.find(m.tag);
    if (it == waits.end()) return;                          // a late answer to a forgotten request
    Wait* w = it->second;
    waits.erase(it);
    place(w, body);
    w->result = m.result; w->error = m.error;
    if (w->queued) --shared->b_queued;
    if (w->session) {
        w->session[4] = m.result; w->session[5] = m.error;
        std::atomic_thread_fence(std::memory_order_release);
        reinterpret_cast<volatile uint8_t*>(w->session)[4] = 1;
        for (auto s = session_waits.begin(); s != session_waits.end(); ++s)
            if (s->second == w) { session_waits.erase(s); break; }
        delete w;
        return;
    }
    std::atomic_thread_fence(std::memory_order_release);
    w->done = 1;
}
uint32_t send(Wait* w, uint32_t cls, uint32_t op, uint32_t a0, uint32_t a1, uint32_t a2, const void* body,
              uint32_t bytes) {
    Message m = {message_magic, k_request, 0, cls, op, a0, a1, a2, 0, 0, bytes};
    {
        std::lock_guard<std::mutex> hold(lock);
        m.tag = next_tag++;
        waits[m.tag] = w;
    }
    send_message(link_fd, m, body);
    return m.tag;
}
// The stock driver serializes a request when it sends it: a path or query request's path is read
// from its buffer, and its length taken, then (not when the builder ran).
void serialize(void* request, Wait* w) {
    Built b;
    {
        std::lock_guard<std::mutex> hold(lock);
        auto it = built.find(request);
        if (it == built.end()) crash("submit of a request no builder made");
        b = it->second;
    }
    if (b.body && (b.cls == cls_path || b.cls == cls_query)) b.bytes = uint32_t(strlen(static_cast<const char*>(b.body)) + 1);
    if (w->queued) log("B's SPI driver sends a held request: op %02x%s%s", b.op, b.cls == cls_path || b.cls == cls_query ?
                       " path " : "", b.cls == cls_path || b.cls == cls_query ? static_cast<const char*>(b.body) : "");
    send(w, b.cls, b.op, b.a0, b.a1, b.a2, b.body, b.bytes);
}
void release_held() {                                       // b_queue cleared: the driver sends what it held
    for (;;) {
        void* request = 0; Wait* w = 0;
        {
            std::lock_guard<std::mutex> hold(lock);
            if (held.empty()) return;
            request = held.front(); held.pop_front();
            auto it = session_waits.find(request);
            if (it == session_waits.end()) continue;
            w = it->second;
        }
        serialize(request, w);
    }
}

// ---- A -----------------------------------------------------------------------------------------
struct Incoming { Message m; std::vector<uint8_t> body; };
std::deque<Incoming> queue;
std::mutex a_lock;
bool in_service = false;
uint32_t service_slot = 0, service_tag = 0, service_cls = 0, service_op = 0, header_seq = 0;
unsigned next_slot = 0;
void answer(const Message& request, uint32_t result) {
    Message m = {message_magic, k_response, request.tag, request.cls, request.op, 0, 0, 0, result, 0, 0};
    send_message(link_fd, m, 0);
}
void generic(const Message& m) {                            // the stock slave ISR's own answers
    switch (m.op) {
    case 1: answer(m, 0xfedebedau); return;
    case 10:
        w32(0x11b000c0u, 10u | (++header_seq & 0xffu) << 8); w32(0x11b000c4u, m.a0); w32(0x11b000c8u, m.a1);
        if (m.a0 == 0x12345678u) w8(driver() + 0x34, 1);    // handshake done: protocol state 1
        answer(m, ~m.a0);
        return;
    case 7: w8(primary, 1); answer(m, 0); return;           // release: the ISR acknowledges it itself
    case 5: answer(m, shared->capacity); return;            // boot partition capacity (88a086c0)
    default: answer(m, 0xffffffffu); return;
    }
}
}

bool a_service() {
    std::lock_guard<std::mutex> hold(a_lock);
    if (in_service || queue.empty() || r32(primary + 4)) return false;
    Incoming in = std::move(queue.front());
    queue.pop_front();
    const uint32_t slot = driver() + 0x3c + 0x60 * (next_slot++ & 3u);
    memset(host(slot), 0, 0x60);
    w32(slot, in.m.cls); w8(slot + 5, uint8_t(in.m.op)); w32(slot + 8, in.m.a0);
    if (in.m.cls != cls_write) w32(slot + 0xc, in.m.a2);
    w32(slot + 0x18, in.m.a1);
    const uint32_t bytes = uint32_t(in.body.size());
    if (in.m.cls != cls_write)
        *static_cast<volatile uint16_t*>(host(slot + 0x1c)) = uint16_t(bytes > 0xffffu ? 0xffffu : bytes);
    if (bytes && (in.m.cls == cls_path || in.m.cls == cls_query)) {
        if (shared->full_path.exchange(0)) {                 // test hook: 240 bytes, no NUL added
            const uint32_t n = bytes < 240 ? bytes : 240;
            memcpy(host(0x8803f6e0u), in.body.data(), n);
            log("fault: a %u-byte path body reached the path buffer whole", n);
        } else {
        const uint32_t n = bytes < 239 ? bytes : 239;
        memcpy(host(0x8803f6e0u), in.body.data(), n);
        w8(0x8803f6e0u + n, 0);
        }
        w32(slot + 0x18, 0x8803f6e0u);
    } else if (bytes) {
        memcpy(host(a_in_buffer), in.body.data(), bytes < a_buffer_bytes ? bytes : a_buffer_bytes);
        w32(slot + 0x20, a_in_buffer); w32(slot + 0x24, bytes);
    }
    // A-only RAM corruption, after every chunk crossed the real transport. B's identical
    // numeric address belongs to its separate SDRAM object and must remain untouched.
    if (in.m.a0 == 0x41535447u && bytes >= 32 && r32(a_in_buffer + 8) == 2 && shared->a_stage_corrupt.exchange(0)) {
        w8(0x8b820000u, r8(0x8b820000u) ^ 1u);
        log("fault: corrupted A staging after transfer, before local admission");
    }
    in_service = true; service_slot = slot; service_tag = in.m.tag; service_cls = in.m.cls; service_op = in.m.op;
    std::atomic_thread_fence(std::memory_order_release);
    w32(primary + 4, slot);                                 // pending request for A's loop
    return true;
}

void a_respond(uint32_t request) {
    Message m = {message_magic, k_response, 0, 0, 0, 0, 0, 0, r32(request + 0x10), r32(request + 0x14), 0};
    {
        std::lock_guard<std::mutex> hold(a_lock);
        if (!in_service || request != service_slot) { log("answer for a request not in service (%08x)", request); return; }
        m.tag = service_tag; m.cls = service_cls; m.op = service_op;
        in_service = false;
    }
    const bool data = (m.cls == cls_sector && (m.op == 0x11 || m.op == 0x12 || m.op == 0x2f)) ||
                      (m.cls == cls_read && m.op == 0x2a) || m.cls == cls_query;
    const uint32_t bytes = data ? r32(request + 0x24) : 0;
    m.body = bytes;
    send_message(link_fd, m, bytes ? host(r32(request + 0x20)) : 0);
    a_service();
}

void a_stream(uint32_t request) {
    Message m = {message_magic, k_body, 0, 0, 0, 0, 0, 0, 0, 0, r32(request + 0x24)};
    {
        std::lock_guard<std::mutex> hold(a_lock);
        if (!in_service || request != service_slot) { log("a body for a request not in service (%08x)", request); return; }
        m.tag = service_tag; m.cls = service_cls; m.op = service_op;
    }
    send_message(link_fd, m, m.body ? host(r32(request + 0x20)) : 0);
    w32(primary + 4, request);                                          // re-posted: A's loop serves it again
}

void link_start() {
    std::thread([] {
        Message m; std::vector<uint8_t> body;
        std::deque<Incoming> inbox;                          // A: held until its slave is set up
        for (;;) {
            const bool got = receive(m, body);
            bool work = got;
            if (role == 0) {
                if (got && m.kind == k_request) inbox.push_back({m, body});
                // The stock slave answers only while A's session has it open (slave_setup sets
                // SPI0 control bit 13; the close clears it); before that B's request waits.
                while (!inbox.empty() && (r32(0x20057004u) & 0x2000u)) {
                    if (!inbox.front().m.cls) generic(inbox.front().m);
                    else { std::lock_guard<std::mutex> hold(a_lock); queue.push_back(std::move(inbox.front())); }
                    inbox.pop_front();
                    work = true;
                }
                a_held = int(inbox.size());
                work = a_service() || work;
            } else {
                if (got && m.kind == k_response) complete(m, body);
                if (got && m.kind == k_body) more(m, body);
                if (!shared->b_queue.load()) release_held();
            }
            // Virtual clock (sim/clock.cpp): the main thread has something to look at before the
            // message counts as taken, so the board never steps the clock in between.
            if (work) kick(role, false);                     // this thread looks again anyway
            if (got) link_taken();
        }
    }).detach();
}

uint32_t b_call(uint32_t cls, uint32_t op, uint32_t a0, uint32_t a1, uint32_t a2, const void* body, uint32_t bytes,
                void* out, uint32_t capacity, uint32_t* error) {
    Wait w; w.out = out; w.capacity = capacity;
    send(&w, cls, op, a0, a1, a2, body, bytes);
    const uint64_t start = device_cycles();
    bool noted = false;
    while (!w.done.load(std::memory_order_acquire)) {        // stock 889f7b12: spins until answered
        idle_pause(0);
        host_poll();
        if (!noted && device_cycles() - start > 2000000000u) {     // 5 s of device time
            noted = true;
            log("waiting for A's answer to op %02x (the stock client waits without a bound)", op);
        }
    }
    if (error) *error = w.error;
    return w.result;
}

// 889f562e (1.2.1 listing): PINT handler removed and masked, both DMA channels' config 0, SPI control
// 0, the three DMA/SPI handlers removed. The request it was serving is never completed (its done byte
// and the driver's active pointer stay as they were), but nothing can reach it any more.
void b_link_closed() {
    std::lock_guard<std::mutex> hold(lock);
    const size_t open = waits.size() + held.size();
    for (auto& s : session_waits) delete s.second;
    session_waits.clear();
    waits.clear();
    held.clear();
    shared->b_queued = 0;
    if (open) log("B's master close: %zu open link request(s) abandoned (SPI and DMA off)", open);
}
void b_build(void* request, uint32_t cls, uint32_t op, uint32_t a0, uint32_t a1, uint32_t a2, const void* body,
             uint32_t bytes, void* out, uint32_t capacity) {
    std::lock_guard<std::mutex> hold(lock);
    built[request] = {cls, op, a0, a1, a2, body, bytes, out, capacity};
    reinterpret_cast<volatile uint8_t*>(request)[4] = 0;
}
void b_submit(void* request) {
    Wait* w = new Wait;
    w->session = static_cast<uint32_t*>(request);
    {
        std::lock_guard<std::mutex> hold(lock);
        auto it = built.find(request);
        if (it == built.end()) crash("submit of a request no builder made");
        const Built& b = it->second;
        if (b.cls && !(b.cls == cls_partition && b.op == 0x18)) {   // b_queue: file requests only
            const int32_t n = shared->b_queue.load();
            if (n > 1) shared->b_queue = n - 1;
            w->queued = n == 1;
        }
        w->out = it->second.out; w->capacity = it->second.capacity;
        auto old = session_waits.find(request);              // a request object reused before its answer
        if (old != session_waits.end()) {
            for (auto it = waits.begin(); it != waits.end(); ++it) if (it->second == old->second) { waits.erase(it); break; }
            if (old->second->queued) --shared->b_queued;
            delete old->second;
            session_waits.erase(old);
        }
        session_waits[request] = w;
        if (w->queued) {
            ++shared->b_queued;
            bool in = false;
            for (void* r : held) in = in || r == request;
            if (!in) held.push_back(request);
            log("B's SPI driver busy (b_queue): request op %02x held", it->second.op);
            return;
        }
    }
    serialize(request, w);
}
}

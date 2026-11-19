// Host shims for the stock 1.2.1 bootloader functions the hosted XY Forever code calls,
// with the same call shapes (sim.hpp: stock<ADDRESS, R>(args...)). Grouped by what they
// stand for: the USB CDC endpoint (a local TCP port), interrupt registration and reset,
// the SPI link (sim/link.cpp), B's stock storage client (each call one request to A, waiting
// without a bound as the stock RPC does), A's stock request executors, the yaffs device and
// its direct API (a host folder standing in for /yaffs2), the posix provider registry, and
// the boot partition (a host file: two partitions, the stock writer's commit-last order).
// A function the simulator does not stand in for ends the processor (crash), never guesses.
#include "runtime.hpp"
#include <arpa/inet.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <string.h>
#include <string>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace xyf_sim {
namespace {
uint32_t crc32(const uint8_t* data, uint32_t bytes, uint32_t value = 0) {
    value = ~value;
    for (uint32_t i = 0; i != bytes; ++i) {
        value ^= data[i];
        for (int b = 0; b != 8; ++b) value = (value >> 1) ^ (0xedb88320u & (0u - (value & 1u)));
    }
    return ~value;
}
template<class F> F* function(uint32_t address) { return reinterpret_cast<F*>(host(address)); }

// ---- USB CDC endpoint (B): 127.0.0.1:port while the service has USB on ----------------------
int listener = -1, client = -1;
void cdc_close_client(const char* why) {
    if (client < 0) return;
    close(client); client = -1;
    log("USB host %s", why);
}
void cdc_start() {
    if (listener >= 0) return;
    listener = socket(AF_INET, SOCK_STREAM, 0);
    const int one = 1;
    setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    sockaddr_in a = {};
    a.sin_family = AF_INET; a.sin_port = htons(uint16_t(shared->port)); a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(listener, reinterpret_cast<sockaddr*>(&a), sizeof(a)) || listen(listener, 4))
        crash("USB endpoint 127.0.0.1:%d: %s", shared->port, strerror(errno));
    fcntl(listener, F_SETFL, O_NONBLOCK);
    shared->b_listening = 1;
    log("USB on: listening on 127.0.0.1:%d", shared->port);
}
void cdc_stop() {
    // The port goes first, as a detached device's does: once the host sees its connection drop, a
    // new one is refused until USB is on again (never accepted by a port about to close).
    shared->b_listening = 0;
    const bool on = listener >= 0;
    if (on) { close(listener); listener = -1; }
    cdc_close_client("detached (USB off)");
    if (on) log("USB off");
}
void cdc_accept() {
    if (listener < 0) return;
    const int c = accept(listener, 0, 0);
    if (c < 0) return;
    cdc_close_client("replaced by a new connection");
    client = c;
    fcntl(client, F_SETFL, O_NONBLOCK);
    const int one = 1;
    setsockopt(client, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
    setsockopt(client, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof(one));
}
int cdc_available() {
    cdc_accept();
    if (client < 0) return 0;
    int n = 0;
    if (ioctl(client, FIONREAD, &n) || n < 0) n = 0;
    if (!n) {
        char probe;
        const ssize_t r = recv(client, &probe, 1, MSG_PEEK | MSG_DONTWAIT);
        if (r == 0 || (r < 0 && errno != EAGAIN && errno != EWOULDBLOCK)) cdc_close_client("closed the port");
    }
    return n;
}
int cdc_read(uint8_t* out, uint32_t n) {
    if (client < 0) return 0;
    const ssize_t got = recv(client, out, n, MSG_DONTWAIT);
    if (got == 0) { cdc_close_client("closed the port"); return 0; }
    if (got > 0) xyf_sim::active();
    return got > 0 ? int(got) : 0;
}
int cdc_write(const uint8_t* data, uint32_t n) {
    if (client < 0) return int(n);                     // nobody attached: the bytes are lost
    uint32_t allowed = n;
    const int32_t pull = shared->cable_pull;
    if (pull > 0 && uint32_t(pull) < allowed) allowed = uint32_t(pull);
    const ssize_t sent = send(client, data, allowed, MSG_DONTWAIT);
    if (sent > 0) xyf_sim::active();
    if (sent < 0) {
        if (errno == EAGAIN || errno == EWOULDBLOCK) return -1;
        cdc_close_client("went away");
        return int(n);
    }
    if (pull > 0) {
        shared->cable_pull = pull - int32_t(sent);
        if (pull - int32_t(sent) <= 0) {
            shared->cable_pull = 0;
            log("fault: cable pulled after %d bytes of a reply", int(sent));
            cdc_close_client("lost (cable pulled)");
            return int(n);
        }
    }
    return int(sent);
}

// ---- interrupts -------------------------------------------------------------------------
void irq_register(uint32_t id, uint32_t handler, uint32_t context) {
    const uint32_t index = id + 1;
    w8(0x11b00ac4u + id, uint8_t(index));
    w32(0x11b008d0u + index * 4, handler); w32(0x11b005d4u + index * 8, context);
    w32(0x20004800u + id * 8, r32(0x20004800u + id * 8) | 5u);
}
void irq_unregister(uint32_t id) { w8(0x11b00ac4u + id, 0); w32(0x20004800u + id * 8, 0); }

#include "stock_files.ipp"

// yaffs device construction (889f963e): the fresh device shape cold_storage checks, allocated at
// the stock heap's break and linked into the retained device list (sentinel 0x11907f8c).
uint32_t construct(uint32_t name) {
    const uint32_t context = 0x88a65bf8u, brk = r32(context + 12), device = brk;
    w32(context + 12, brk + ((0xe94u + 7u) & ~7u));
    memset(host(device), 0, 0xe94);
    static const uint32_t shape[][2] = {
        {0x00, 0}, {0x04, 1}, {0x08, 0x1000}, {0x0c, 0x80}, {0x14, 0}, {0x18, 0x11ff}, {0x1c, 5}, {0x20, 10},
        {0x28, 1}, {0x30, 1}, {0x34, 1}, {0x3c, 1000}, {0x7c, 1}, {0x80, media_write}, {0x84, 0x88a345f4u},
        {0x88, 0x88a34522u}, {0x8c, 0x88a34348u}, {0x90, 0x88a3434au}, {0x94, 0x88a3434eu}, {0xa0, 0x889f9524u},
        {0x54, 0x889fb350u}};
    for (auto& s : shape) w32(device + s[0], s[1]);
    w32(device, name);
    const uint32_t sentinel = 0x11907f8cu, node = device + 0xb4, tail = r32(sentinel + 4);
    w32(node, sentinel); w32(node + 4, tail);
    if (tail == sentinel) w32(sentinel, node); else w32(tail, node);
    w32(sentinel + 4, node);
    yaffs_device = device;
    return device;
}
int mount(uint32_t device, int read_only_mount) {
    if (device != yaffs_device || r32(device + 0xe4)) return fail(16);
    const int32_t failing = shared->fail_mount;
    if (failing > 0 && (shared->fail_mount = failing - 1) == 0) { log("fault: yaffs mount failed"); return fail(5); }
    std::atomic<int32_t>& slow = read_only_mount ? shared->mount_ms : shared->rw_mount_ms;   // 193: and read-write
    if (const int32_t ms = slow.load()) {                // ends early once the fault is cleared
        log("fault: the %s mount takes %d ms", read_only_mount ? "read-only" : "read-write", ms);
        for (const uint64_t end = device_cycles() + uint64_t(ms) * 400000u; device_cycles() < end && slow.load();)
            idle_pause(end, 5000);
    }
    if (read_only_mount && shared->needs_repair.load()) {   // 192: the scan wants to repair (erase)
        const uint32_t gate = r32(device + 0x88);
        if (gate >= 0xc0000000u) function<int()>(gate)();      // a deny gate: called as the media would
        else media_change("repair erase on a read-only mount");
        log("yaffs: no checkpoint (unclean shutdown): the read-only mount needs a repair and fails");
        return fail(5);
    }
    if (!read_only_mount) media_change("read-write mount");
    if (shared->rw_open.exchange(0)) {
        ++shared->unclean_mounts;
        log("yaffs: the last read-write mount was never unmounted (no checkpoint): a full scan on the device");
    }
    if (!read_only_mount) { shared->rw_open = 1; ++shared->rw_mounts; }
    w32(device + 0xe4, 1); w32(device + 0xe8, read_only_mount ? 1 : 0);
    if (!read_only_mount) {                            // the stock mount fills only empty tag slots
        if (!r32(device + 0x9c)) w32(device + 0x9c, 0x88a0310cu);
        if (!r32(device + 0xa8)) w32(device + 0xa8, 0x88a030fau);
    }
    return 0;
}
int unmount(uint32_t device) {
    if (device != yaffs_device || !r32(device + 0xe4)) return fail(22);
    if (shared->fail_unmount.load()) { log("fault: yaffs unmount failed"); return fail(5); }   // test hook (while set)
    if (any_open()) return fail(16);
    if (!read_only()) {
        if (const int32_t ms = shared->unmount_ms.load()) {           // ends early once the fault is cleared
            log("fault: the checkpoint takes %d ms", ms);
            for (const uint64_t end = device_cycles() + uint64_t(ms) * 400000u; device_cycles() < end &&
                 shared->unmount_ms.load();) idle_pause(end, 5000);
        }
        media_change("read-write unmount (checkpoint)"); shared->rw_open = 0;
        if (shared->needs_repair.exchange(0)) log("yaffs: repaired; the checkpoint is written");
    }
    w32(device + 0xe4, 0);                             // read-write: the checkpoint is written here (no-op)
    return 0;
}

// ---- the posix layer on A (the stock provider registry, then yaffs) --------------------------
bool provider() { return r32(0x8803f6bcu) != r32(0x8803f6c0u); }
void register_provider(uintptr_t object, uint32_t name) {
    const uint32_t table = 0x88b189c0u;                // as Test 033 saw it
    w32(table, device(object)); w32(table + 4, name);
    w32(0x8803f6bcu, table); w32(0x8803f6c0u, table + 8); w32(0x8803f6c4u, table + 8);
}
int posix_open(const char* path, uint32_t flags) {      // newlib flags: "wb" = 0x8301 (write, 0x200, 0x100)
    if (!provider()) return fail(19);
    const bool write = (flags & 3u) != 0, create = (flags & 0x300u) != 0;
    return open_file(path, write, create, create, false);
}
void finish(uint32_t request, int result) {
    w32(request + 0x10, uint32_t(result)); w32(request + 0x14, result < 0 ? uint32_t(last_error) : 0);
    a_respond(request);
}

// ---- boot partition (A executors; B's stock writer) -----------------------------------------
const uint32_t erase_block = 0x10000u;                 // trim unit (bytes)
int bootpart() {
    const int fd = open(shared->bootpart, O_RDWR | O_CREAT, 0644);
    if (fd < 0) crash("boot partition %s: %s", shared->bootpart, strerror(errno));
    return fd;
}
bool sectors_io(bool write, uint32_t sector, uint32_t count, uint8_t* data) {
    if (selected != 1 && selected != 2) return false;
    const uint64_t at = uint64_t(sector) * 512u, bytes = uint64_t(count) * 512u;
    if (at + bytes > shared->capacity) return false;
    const int fd = bootpart();
    const off_t offset = off_t((selected - 1) * uint64_t(shared->capacity) + at);
    ssize_t n;
    int error = 0;
    if (write && shared->fail_write > 0 && write_fault(0, error)) { close(fd); return false; }   // fault hook
    if (write) n = pwrite(fd, data, bytes, offset);
    else { memset(data, 0, bytes); n = pread(fd, data, bytes, offset); if (n >= 0) n = ssize_t(bytes); }
    close(fd);
    if (write && n == ssize_t(bytes)) storage_op_done();
    return n == ssize_t(bytes);
}
bool trim(uint32_t block) {
    std::vector<uint8_t> zero(erase_block, 0);
    const uint32_t first = block * (erase_block / 512u);
    const uint32_t count = first * 512u < shared->capacity ? (shared->capacity - first * 512u) / 512u : 0;
    return count && sectors_io(true, first, count < erase_block / 512u ? count : erase_block / 512u, zero.data());
}

// B: one stock log line to the hooked sinks (usb_service's install log), as 11a01d96 does.
void stock_log(const char* text) {
    if (r32(0x11b00074u) < 3) return;
    char line[160];
    line[0] = line[1] = line[2] = line[3] = 1;         // 4 binary time bytes (never zero: Test 036)
    snprintf(line + 4, sizeof(line) - 4, "mmc_tools.cpp: %s", text);
    for (uint32_t i = 0; i != 4; ++i)
        if (const uint32_t sink = r32(0x8803d06cu + 4 * i)) {
            if (sink < 0xc0000000u) continue;          // a stock sink: not hosted
            function<void(int, const char*)>(sink)(3, line);
        }
}
uint32_t b_select(uint32_t p) { return b_call(cls_partition, 0x18, p, 0, 0, 0, 0, 0, 0); }
uint32_t b_read(uint32_t sector, uint8_t* out, uint32_t count) {
    return b_call(cls_sector, count == 1 ? 0x11 : 0x12, sector, count, 0, 0, 0, out, count * 512u);
}
uint32_t b_write(uint32_t sector, const uint8_t* data, uint32_t count) {
    return b_call(cls_write, sector ? 0x16 : 0x15, sector, count, 0, data, count * 512u, 0, 0);
}
bool active(const uint8_t* sector) { uint32_t v; memcpy(&v, sector, 4); return v >= 2 && sector[12]; }
// 88a06a7e(buf, size, 0) (docs/hardware/usb.md, Boot partition and USB): target the first
// non-active partition (1, else 2; 2 if both are active), trim, write sectors 1.. in batches of
// 128, verify, then sector 0 last (the commit), then erase block 0 of the other partition.
// No encryption (the device's OTP key would encrypt the image in place).
uint32_t stock_writer(const uint8_t* image, uint32_t size) {
    char text[96];
    const uint32_t capacity = b_call(0, 5, 0, 0, 0, 0, 0, 0, 0);
    if (size + 512 < 10000 || size + 512 > capacity) { stock_log("file too large for boot partition"); return 0; }
    const uint32_t crc = crc32(image, size);
    uint8_t sector[512];
    uint32_t target = 1;
    if (b_select(1) || b_read(0, sector, 1)) return 0;
    if (active(sector)) {
        stock_log("found active firmware, check next boot partition");
        target = 2;
        if (b_select(2) || b_read(0, sector, 1)) return 0;
        if (active(sector)) stock_log("found active firmware here too...overwrite!");
    }
    if (b_select(target)) return 0;
    for (uint32_t b = 0; b * erase_block < size + 512; ++b) {
        snprintf(text, sizeof(text), "erase block: %u", b); stock_log(text);
        if (b_call(cls_partition, 0x14, b, 0, 0, 0, 0, 0, 0)) return 0;
    }
    const uint32_t sectors = (size + 511) / 512;
    std::vector<uint8_t> chunk(128 * 512), back(128 * 512);
    for (uint32_t s = 0; s < sectors; s += 128) {
        const uint32_t n = sectors - s < 128 ? sectors - s : 128;
        memset(chunk.data(), 0, n * 512);
        memcpy(chunk.data(), image + s * 512, size - s * 512 < n * 512 ? size - s * 512 : n * 512);
        snprintf(text, sizeof(text), "write sectors: %u-%u", 1 + s, s + n); stock_log(text);
        if (b_write(1 + s, chunk.data(), n)) return 0;
        if (b_read(1 + s, back.data(), n) || memcmp(back.data(), chunk.data(), n * 512)) { stock_log("verify failed"); return 0; }
    }
    memset(sector, 0, sizeof(sector));
    const uint32_t header[3] = {3, size, crc};
    memcpy(sector, header, 12); sector[12] = 1; sector[13] = 0;
    stock_log("finally, write sector 0");
    uint8_t check[512];
    if (b_write(0, sector, 1) || b_read(0, check, 1) || memcmp(check, sector, 512)) return 0;
    stock_log("erase block 0 to disable other boot partition");
    if (b_select(3 - target) || b_call(cls_partition, 0x14, 0, 0, 0, 0, 0, 0, 0)) return 0;
    b_select(0);
    return 1;
}

#include "stock_executors.ipp"                          // A's stock request executors
}

void storage_op_done() {
    ++shared->storage_ops;
    const int32_t left = shared->cut_after;
    if (left > 0 && (shared->cut_after = left - 1) == 0) {
        log("fault: power cut armed: cutting after this storage operation");
        Message m = {message_magic, k_event, 0, 0, e_cut, 0, 0, 0, 0, 0, 0};
        send_message(link_fd, m, 0);
        for (;;) idle_pause(0, 1000000);               // the board cuts the power
    }
}
// ---- B's boot ROM: the OTP init and read (target/otp_keys.cpp) ------------------------------
bool rom_otp_initialised = false;                      // per processor start, as the ROM's own state
const uint8_t p224_generator[56] = {                   // a valid P-224 point that is not the vendor key
    0xb7, 0x0e, 0x0c, 0xbd, 0x6b, 0xb4, 0xbf, 0x7f, 0x32, 0x13, 0x90, 0xb9, 0x4a, 0x03, 0xc1, 0xd3, 0x56, 0xc2, 0x11,
    0x22, 0x34, 0x32, 0x80, 0xd6, 0x11, 0x5c, 0x1d, 0x21, 0xbd, 0x37, 0x63, 0x88, 0xb5, 0xf7, 0x23, 0xfb, 0x4c, 0x22,
    0xdf, 0xe6, 0xcd, 0x43, 0x75, 0xa0, 0x5a, 0x07, 0x47, 0x64, 0x44, 0xd5, 0x81, 0x99, 0x85, 0x00, 0x7e, 0x34};
uint32_t rom_otp_read(uint32_t selector, uint8_t* out) {
    ++shared->otp_reads;
    const int32_t fault = shared->otp_fail;
    if (!rom_otp_initialised || fault == 1 || selector >= 16 || !shared->otp_length[selector]) return 0;
    for (uint32_t i = 0; i != shared->otp_length[selector]; ++i) out[i] = shared->otp_field[selector][i];
    if (selector == 9 && fault == 2) out[55] ^= 1;
    if (selector == 9 && fault == 3) for (uint32_t i = 0; i != 56; ++i) out[i] = p224_generator[i];
    if (selector == 3 && fault == 4) out[0] ^= 0x80;
    return 1;
}
int cdc_fd() { return client >= 0 ? client : listener; }

// A's stock bootloader (sim/boot.cpp) after a reset: it serves the boot-partition layer B's
// bootloader uses through the same stock executors (select, sector reads). Other requests: the
// bootloader's own answer is not modelled [U]; they get -1 (ENODEV) so nothing waits on them.
void a_boot_request(uint32_t request) {
    const uint32_t cls = r32(request), op = r8(request + 5);
    log("A's bootloader: class %08x op %02x argument %08x", cls, op, r32(request + 8));
    if (cls == cls_partition && op == 0x18) return executor(0x889f76cau, request);
    if (cls == cls_sector && (op == 0x11 || op == 0x12)) return executor(0x889f6b7cu, request);
    log("A's bootloader: class %08x op %02x not modelled: answered -1", cls, op);
    last_error = 19;
    finish(request, -1);
}

uint64_t stock_call(uint32_t address, const uint64_t* a, unsigned count) {
    (void)count;
    host_poll();
    auto u = [a](unsigned i) { return uint32_t(a[i]); };
    auto p = [a](unsigned i) { return reinterpret_cast<void*>(uintptr_t(a[i])); };
    auto s = [a](unsigned i) { return static_cast<const char*>(reinterpret_cast<void*>(uintptr_t(a[i]))); };
    switch (address) {
    // USB (B)
    case 0x88a35044u: cdc_start(); return 0;
    case 0x88a34fecu: cdc_stop(); return 0;
    case 0x88a12c7cu: w32(0x880734c0u, u(1)); return 0;
    case 0x88a13328u: return uint64_t(int64_t(cdc_available()));
    case 0x88a1334au: return uint64_t(int64_t(cdc_read(static_cast<uint8_t*>(p(1)), u(2))));
    case 0x88a132f6u: return uint64_t(int64_t(cdc_write(static_cast<const uint8_t*>(p(1)), u(2))));
    // interrupts, reset
    case 0x88a30798u: irq_register(u(0), u(1), u(2)); return 0;
    case 0x88a319fau: return 0;
    case 0x11a03518u:                                   // always completes: B's hang is in its boot
        log("stock reset"); exit_processor(exit_reset);
    // SPI driver setup and teardown (peer_services.cpp master_setup / slave_setup / end)
    case 0x889f533au: case 0x889f53aeu: case 0x889f537au: case 0x889f52f4u: case 0x889f4e3eu:
    case 0x889f52c0u: case 0x11a04f78u: case 0x889f5db0u: case 0x889f626cu: case 0x889f4f10u:
        return 0;
    case 0x88a304c4u: case 0x88a3046cu: return 0;
    case 0x889f5010u:                                   // DMA channel interrupts of the link
        if (role) { irq_register(0x26, 0x889f4f5eu, u(0)); irq_register(0x24, 0x889f4fa2u, u(0)); }
        else { irq_register(0x2e, 0x889f4f5eu, u(0)); irq_register(0x2c, 0x889f4fa2u, u(0)); }
        return 0;
    case 0x889f562eu: case 0x889f5e38u: {              // master / slave close
        if (role && shared->b_close_stuck.exchange(0)) { log("fault: B's master close did not take"); return 0; }
        if (role) b_link_closed();
        const uint32_t d = u(0), spi = r32(d + 8);
        const uint32_t ids[2][4] = {{0x2e, 0x2c, 0x2d, 0x2f}, {0x26, 0x24, 0x25, 0x27}};
        for (uint32_t id : ids[role ? 1 : 0]) irq_unregister(id);
        w32(spi, 0); w32(r32(d + 0xc) + 8, 0); w32(r32(d + 0x10) + 8, 0);
        return 0;
    }
    // link requests (session builders and submit; A's answer)
    case 0x889f4e82u: b_build(p(0), 0, u(1), u(2), 0, 0, 0, 0, 0, 0); return 0;
    case 0x889f65a4u: b_build(p(0), 0, u(1), 0, 0, 0, 0, 0, 0, 0); return 0;
    // the stock request builders (the session's and B's file client: file_client.cpp), with
    // the stock argument layout: partition (req, op, a0, a1 -> +0x18, a2 -> +0x1c), sector (req, op,
    // sector, count, 0, out, bytes), path (req, op, path, a0, a1), query (req, op, path, 0, 0, out,
    // bytes), read (req, 0x2a, fd, count, out, 0x1000), write (req, 0x2b, fd, count, data, count),
    // readdir (req, 0x2f, folder, 0, out, 0x10c)
    case 0x889f87d4u: b_build(p(0), cls_partition, u(1), u(2), u(3), u(4), 0, 0, 0, 0); return 0;
    case 0x889f8872u: b_build(p(0), cls_sector, u(1), u(2), u(3), 0, 0, 0, p(5), u(6)); return 0;
    case 0x889f8d22u: b_build(p(0), cls_path, u(1), u(3), 0, 0, s(2), uint32_t(strlen(s(2)) + 1), 0, 0); return 0;
    case 0x889f8e36u: b_build(p(0), cls_query, u(1), 0, 0, 0, s(2), uint32_t(strlen(s(2)) + 1), p(5), u(6)); return 0;
    case 0x889f6c5cu: b_build(p(0), cls_read, u(1), u(2), 0, u(3), 0, 0, p(4), u(3)); return 0;
    case 0x889f8c10u: b_build(p(0), cls_write, u(1), u(2), 0, u(3), p(4), u(5), 0, 0); return 0;
    case 0x889f88acu: b_build(p(0), cls_sector, u(1), u(2), 0, 0, 0, 0, p(4), u(5)); return 0;
    case 0x11a0060cu: b_submit(p(1)); return 0;
    case 0x889f6508u: a_respond(u(1)); return 0;
    // A's own posix calls (they reach yaffs on role 0). B's stock posix client (889f869e ->
    // 889f7b12, an unbounded wait) is no longer called: on B these end the processor.
    case 0x889f3b50u:                                   // posix open
        if (role) crash("B called the stock posix open (B uses its own timed file client)");
        if (shared->fail_open.load() > 0 && --shared->fail_open == 0) {
            log("fault: A's posix open failed"); return uint64_t(int64_t(fail(5)));
        }
        return uint64_t(int64_t(posix_open(s(1), u(2))));
    case 0x889f3516u:                                   // posix stat
        if (role) crash("B called the stock posix stat (B uses its own timed file client)");
        return uint64_t(int64_t(stat_record(s(0), static_cast<uint32_t*>(p(1)))));
    case 0x889f44f4u:                                   // posix closedir
        if (role) crash("B called the stock posix closedir (B uses its own timed file client)");
        return uint64_t(int64_t(close_directory(u(0))));
    case 0x889fa834u:                                   // yaffs rename
        if (role) crash("B called the yaffs rename (A's only)");
        return uint64_t(int64_t(rename_object(s(0), s(1))));
    case 0x889f285au:                                   // posix close
        if (role) crash("B called the stock posix close (B uses its own timed file client)");
        return uint64_t(int64_t(close_file(int(u(0)))));
    // B: boot partition
    case 0x889f65f0u: return b_call(0, 5, 0, 0, 0, 0, 0, 0, 0);
    case 0x88a06a7eu: return stock_writer(static_cast<const uint8_t*>(p(0)), u(1));
    case 0x889f683cu: return b_select(u(0));
    case 0x889f6a6au: return b_read(u(0), static_cast<uint8_t*>(p(1)), 1);
    case 0x889f6abeu: return b_read(u(0), static_cast<uint8_t*>(p(1)), u(2));
    // A: provider registration, executors
    case 0x889f3910u: register_provider(uintptr_t(a[0]), u(1)); return 0;
    case 0x889f76cau: case 0x889f6feau: case 0x889f6cd6u: case 0x889f6dcau: case 0x889f6b7cu: case 0x889f72e4u:
        executor(address, u(0)); return 0;
    // A: yaffs device and direct API (cold_storage.cpp)
    case 0x889f963eu: return construct(u(0));
    case 0x889fae2eu: return uint64_t(int64_t(mount(u(0), int(u(2)))));
    case 0x889fb02cu: return uint64_t(int64_t(unmount(u(0))));
    case 0x889fbfe6u: {
        const uint32_t f = u(1);                        // yaffs flags: 1 write, 2 read-write, 0x40 create, 0x200 trunc
        return uint64_t(int64_t(open_file(s(0), (f & 3u) != 0, (f & 0x40u) != 0, (f & 0x200u) != 0, (f & 0x80u) != 0)));
    }
    case 0x889fb928u: return uint64_t(int64_t(read_file(int(u(0)), static_cast<uint8_t*>(p(1)), u(2))));
    case 0x889fbb0cu: return uint64_t(int64_t(write_file(int(u(0)), static_cast<const uint8_t*>(p(1)), u(2))));
    case 0x889fb6fau: return uint64_t(int64_t(close_file(int(u(0)))));
    case 0x889fb50cu: return open_directory(s(0));
    case 0x889fb5e6u: { const uint32_t e = next_entry(u(0)); return e ? uint64_t(reinterpret_cast<uintptr_t>(host(e))) : 0; }
    case 0x889fbffcu: return uint64_t(int64_t(close_directory(u(0))));
    case 0x889f9982u: return uint64_t(int64_t(-last_error));
    case 0x88a344a8u: return 1;
    // B's boot ROM (target/otp_keys.cpp): processor A never reads its OTP
    case 0x04000054u:
        if (!role) crash("ROM OTP init on processor A");
        ++shared->otp_inits; rom_otp_initialised = true; return 0;
    case 0x04000048u:
        if (!role) crash("ROM OTP read on processor A");
        return rom_otp_read(u(0), static_cast<uint8_t*>(p(1)));
    }
    crash("stock function %08x is not simulated", address);
}
}

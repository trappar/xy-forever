// Run the target source owner and OTP dispatcher against a controlled file client.
#include "../target/tfw_import_source.hpp"
#include "../target/otp_keys.hpp"
#include "../target/tfw_hash_rpc.hpp"
#include "../target/usb_proto.hpp"
#include <algorithm>
#include <cassert>
#include <cstring>
#include <fstream>
#include <iterator>
#include <vector>

using namespace xyf_core;
using xy_forever::tfw_import::Source;
using xy_forever::tfw_import::SourcePhase;
namespace P = xyf::usbp;
namespace rpc = xy_forever::tfw_hash_rpc;

namespace {
const char path[] = "/yaffs2/xy-forever/inbox/synthetic.tfw";
struct Peer {
    std::vector<Byte> input, public_key;
    hash::Digest hash;
    U32 stat_size, declared_size, final_size, position, operation;
    U32 stats, opens, reads, closes, cancels, public_reads, aes_reads;
    bool file, lease, waited, reported_busy, fail_close;
} peer;

std::vector<Byte> load(const char* name) {
    std::ifstream stream(name, std::ios::binary);
    return {std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>()};
}

void begin(Source& source) {
    char request[sizeof(path)];
    std::copy(std::begin(path), std::end(path), request);
    assert(source.begin(request) == P::s_ok);
    request[sizeof(path) - 2] = 'x';  // the caller's request buffer is free for the next frame
    assert(!std::strcmp(source.path(), path) && !source.reader().read);
}

SourcePhase hashing(Source& source) {
    for (U32 i = 0; i != 100 && source.phase() == SourcePhase::hashing; ++i) source.hash_step();
    assert(!peer.operation && !peer.lease && !peer.file);
    return source.phase();
}

bool stop(void*) { return false; }
bool until_public_key(void*) { return peer.public_reads == 0; }
bool until_stopped(void* stop) { return !*static_cast<bool*>(stop); }
}

namespace xy_forever { namespace file_client {
uint32_t status(uint32_t code) { return code == refused ? P::s_refused : P::s_link; }

Reply stat(const char* name, uint8_t* record) {
    assert(!std::strcmp(name, path) && !peer.file);
    ++peer.stats;
    P::put32(record + 8, 0x8000);
    P::put32(record + 0x1c, peer.stat_size);
    return {done, 0, 0};
}

Reply tfw_hash_send(const char* name, uint32_t op) {
    assert(!std::strcmp(name, path) && !peer.operation && !peer.file);
    assert(op == rpc::start ? !peer.lease : peer.lease);
    peer.operation = op;
    peer.waited = false;
    return {done, 0, 0};
}

Reply left_poll(bool wait) {
    assert(!wait && peer.operation);
    if (!peer.waited) { peer.waited = true; return {busy, -1, 0}; }
    const U32 op = peer.operation;
    peer.operation = 0;
    switch (op) {
    case rpc::start:
        peer.lease = true;
        return {done, P::s_ok, peer.declared_size};
    case rpc::status:
        if (!peer.reported_busy) { peer.reported_busy = true; return {done, P::s_busy, peer.final_size / 2}; }
        return {done, P::s_ok, peer.final_size};
    case rpc::cancel:
        peer.lease = false;
        ++peer.cancels;
        return {done, P::s_ok, 0};
    default:
        assert(op >= rpc::digest && op < rpc::digest + rpc::digest_words);
        return {done, P::s_ok, P::get32(peer.hash.bytes + 4 * (op - rpc::digest))};
    }
}

Reply open(const char* name, uint32_t flags) {
    assert(!std::strcmp(name, path) && !flags && !peer.file && !peer.lease);
    peer.file = true;
    peer.position = 0;
    ++peer.opens;
    return {done, 7, 0};
}

Reply seek(int fd, int offset) {
    assert(fd == 7 && peer.file && offset >= 0);
    peer.position = U32(offset);
    return {done, offset, 0};
}

Reply read(int fd, uint8_t* out, uint32_t count) {
    assert(fd == 7 && peer.file && count && count <= piece_max);
    ++peer.reads;
    const U32 left = peer.position <= peer.input.size() ? U32(peer.input.size()) - peer.position : 0;
    const U32 bytes = std::min(count, left);
    for (U32 i = 0; i != bytes; ++i) out[i] = peer.input[peer.position++];
    return {done, int32_t(bytes), 0};
}

Reply close(int fd) {
    assert(fd == 7 && peer.file);
    if (peer.fail_close) return {done, -1, 9};
    ++peer.closes;
    peer.file = false;
    return {done, 0, 0};
}
} }

namespace xy_forever { namespace otp_keys {
bool public_key(void*, Byte out[56]) {
    assert(peer.file && !peer.lease && peer.public_key.size() == 56);
    ++peer.public_reads;
    std::copy(peer.public_key.begin(), peer.public_key.end(), out);
    return true;
}
bool aes_key(void*, U32 index, Byte out[32]) {
    assert(peer.file && !peer.lease && !index && peer.public_reads);
    ++peer.aes_reads;
    for (U32 i = 0; i != 32; ++i) out[i] = Byte(i);
    return true;
}
} }

int main(int argc, char** argv) {
    assert(argc == 5);
    peer.input = load(argv[1]);
    const auto archive = load(argv[2]);
    peer.public_key = load(argv[3]);
    const auto digest = load(argv[4]);
    assert(digest.size() == sizeof(peer.hash.bytes));
    std::copy(digest.begin(), digest.end(), peer.hash.bytes);
    peer.stat_size = peer.declared_size = peer.final_size = U32(peer.input.size());
    Source source;
    assert(source.begin("/yaffs2/upgrade/file.tfw") == P::s_bad_request && !peer.stats);

    begin(source);
    assert(hashing(source) == SourcePhase::hashed && peer.cancels == 1 && !source.reader().read);
    assert(source.open() == P::s_ok && source.phase() == SourcePhase::authenticated);
    assert(peer.stats == 2 && peer.opens == 1 && peer.public_reads == 1 && peer.aes_reads == 1);
    const auto reader = source.reader();
    assert(reader.size == archive.size());
    std::vector<Byte> decrypted(reader.size);
    assert(input::read(reader, 0, decrypted.data(), reader.size) && decrypted == archive);
    assert(input::read(reader, 0, decrypted.data(), 513) && peer.aes_reads == 1);
    assert(!input::read(reader, reader.size, decrypted.data(), 1));
    peer.fail_close = true;
    assert(source.close() == P::s_storage && peer.file && !source.reader().read);
    assert(source.phase() == SourcePhase::failed && !input::read(reader, 0, decrypted.data(), 1));
    assert(source.begin(path) == P::s_busy);
    peer.fail_close = false;
    assert(source.close() == P::s_ok && !peer.file && peer.opens == peer.closes);

    ++peer.declared_size;
    begin(source);
    assert(hashing(source) == SourcePhase::failed && source.error() == P::s_bad_request);
    assert(peer.cancels == 2 && peer.opens == 1);
    --peer.declared_size;

    --peer.final_size;
    begin(source);
    assert(hashing(source) == SourcePhase::failed && source.error() == P::s_bad_request);
    assert(peer.cancels == 3 && peer.opens == 1);
    ++peer.final_size;

    begin(source);
    assert(source.hash_step() == SourcePhase::hashing && peer.operation == rpc::start);
    source.cancel_hash();
    assert(hashing(source) == SourcePhase::cancelled && peer.cancels == 4 && peer.opens == 1);

    begin(source);
    assert(hashing(source) == SourcePhase::hashed);
    const U32 stats = peer.stats;
    Work before_stat(nullptr, stop);
    assert(source.open(&before_stat) == P::s_cancelled);
    assert(peer.stats == stats && peer.opens == 1);

    for (bool short_file : {false, true}) {
        begin(source);
        assert(hashing(source) == SourcePhase::hashed);
        const Byte end = peer.input.back();
        if (short_file) peer.input.pop_back();
        else peer.input.push_back(0);
        assert(source.open() == P::s_bad_request && !source.reader().read);
        assert(!peer.file && peer.opens == peer.closes && peer.public_reads == 1);
        if (short_file) peer.input.push_back(end);
        else peer.input.pop_back();
    }

    peer.hash.bytes[0] ^= 1;
    begin(source);
    assert(hashing(source) == SourcePhase::hashed);
    assert(source.open() == P::s_sha && !source.reader().read);
    assert(!peer.file && peer.public_reads == 2 && peer.aes_reads == 1);
    peer.hash.bytes[0] ^= 1;

    peer.public_reads = 0;
    begin(source);
    assert(hashing(source) == SourcePhase::hashed);
    Work before_key(nullptr, until_public_key);
    assert(source.open(&before_key) == P::s_cancelled && !source.reader().read);
    assert(!peer.file && peer.public_reads == 1 && peer.aes_reads == 1);
    assert(peer.opens == peer.closes);

    peer.input[0x84] = 1;  // an AES selector the device never reads
    begin(source);
    assert(hashing(source) == SourcePhase::hashed);
    assert(source.open() == P::s_image && !source.reader().read);
    assert(!peer.file && peer.public_reads == 1 && peer.aes_reads == 1);
    peer.input[0x84] = 0;

    begin(source);
    assert(hashing(source) == SourcePhase::hashed);
    bool stopped = false;
    Work during_read(&stopped, until_stopped);
    assert(source.open(&during_read) == P::s_ok);
    const U32 reads = peer.reads;
    stopped = true;
    assert(!input::read(source.reader(), 8192, decrypted.data(), 512));
    assert(during_read.cancelled() && peer.reads == reads);
    assert(source.close() == P::s_ok && peer.opens == peer.closes);
}

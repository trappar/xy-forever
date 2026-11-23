// Run the target source owner and OTP dispatcher against a controlled file client.
#include "../target/tfw_import_source.hpp"
#include "../target/otp_keys.hpp"
#include "../target/usb_proto.hpp"
#include "../target/xyf_clock.hpp"
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

namespace {
const char path[] = "/yaffs2/xy-forever/inbox/synthetic.tfw";
struct Peer {
    std::vector<Byte> input;
    U32 stat_size, position;
    U32 stats, opens, reads, closes, public_reads, aes_reads;
    U32 ahead_bytes;   // a read left out (read_start) into stream: its answer, the bytes read
    U32 ahead_checks; // #279: checkpoints with a read left out, so while the source's caller works on the half before
    bool file, fail_close, ahead;
} peer;
Byte stream_bytes[xy_forever::file_client::stream_capacity];

std::vector<Byte> load(const char* name) {
    std::ifstream stream(name, std::ios::binary);
    return {std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>()};
}

void begin(Source& source) {
    char request[sizeof(path)];
    std::copy(std::begin(path), std::end(path), request);
    assert(source.begin(request) == P::s_ok && source.phase() == SourcePhase::ready);
    request[sizeof(path) - 2] = 'x'; // the caller's request buffer is free for the next frame
    assert(!std::strcmp(source.path(), path) && !source.reader().read && !peer.file);
}

bool stop(void*) { return false; }
bool until_key(void*) { return peer.aes_reads == 0; }
bool until_stopped(void* stop) { return !*static_cast<bool*>(stop); }
bool count_ahead(void*) {
    if (peer.ahead) ++peer.ahead_checks;
    return true;
}
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

Reply left_poll(bool wait) { // the source's read-ahead: answered at once
    assert(wait && peer.ahead);
    peer.ahead = false;
    return {done, int32_t(peer.ahead_bytes), 0};
}

Reply open(const char* name, uint32_t flags) {
    assert(!std::strcmp(name, path) && !flags && !peer.file);
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

bool left_out() { return peer.ahead; }

Reply read_start(int fd, uint32_t at, uint32_t count) {
    assert(fd == 7 && peer.file && !peer.ahead && count && count <= stream_max && !(at % 32) &&
           at + count <= stream_capacity);
    ++peer.reads;
    const U32 left = peer.position <= peer.input.size() ? U32(peer.input.size()) - peer.position : 0;
    const U32 bytes = std::min(count, left);
    for (U32 i = 0; i != bytes; ++i) stream_bytes[at + i] = peer.input[peer.position++];
    peer.ahead = true;
    peer.ahead_bytes = bytes;
    return {done, 0, 0};
}

const uint8_t* stream(uint32_t at, uint32_t count) {
    assert(at + count <= stream_capacity);
    return stream_bytes + at;
}

Reply close(int fd) {
    assert(fd == 7 && peer.file && !peer.ahead);
    if (peer.fail_close) return {done, -1, 9};
    ++peer.closes;
    peer.file = false;
    return {done, 0, 0};
}
}}

namespace xyf { namespace clock {
uint64_t cycles() { return 0; }
}}

namespace xy_forever { namespace otp_keys {
bool public_key(void*, Byte out[56]) { // never: the import checks no signature (#279)
    ++peer.public_reads;
    for (U32 i = 0; i != 56; ++i) out[i] = 0;
    return false;
}
bool aes_key(void*, U32 index, Byte out[32]) { // index 0 only, as the device's
    assert(peer.file);
    if (index) return false;
    ++peer.aes_reads;
    for (U32 i = 0; i != 32; ++i) out[i] = Byte(i);
    return true;
}
}}

int main(int argc, char** argv) {
    assert(argc == 3);
    peer.input = load(argv[1]);
    const auto archive = load(argv[2]);
    peer.stat_size = U32(peer.input.size());
    Source source;
    assert(source.begin("/yaffs2/upgrade/file.tfw") == P::s_bad_request && !peer.stats);

    begin(source);
    assert(source.open() == P::s_ok && source.phase() == SourcePhase::open);
    assert(peer.stats == 2 && peer.opens == 1 && !peer.public_reads && peer.aes_reads == 1);
    for (U32 i = 0; i != 32; ++i) assert(source.identity().bytes[i] == peer.input[0x80 + 0x9c + i]); // as declared
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

    // close(true) keeps it ready to open again (admission, then the staging window).
    begin(source);
    assert(source.open() == P::s_ok && source.close(true) == P::s_ok && source.phase() == SourcePhase::ready);
    assert(source.open() == P::s_ok && source.close() == P::s_ok && source.phase() == SourcePhase::idle);
    assert(source.open() == P::s_state && peer.opens == peer.closes);

    begin(source);
    const U32 stats = peer.stats;
    Work before_stat(nullptr, stop);
    assert(source.open(&before_stat) == P::s_cancelled && source.phase() == SourcePhase::cancelled);
    assert(peer.stats == stats && peer.opens == peer.closes);

    ++peer.stat_size; // the file changed between begin and open
    begin(source);
    --peer.stat_size;
    assert(source.open() == P::s_bad_request && !peer.file);

    for (bool short_file : {false, true}) {
        begin(source);
        const Byte end = peer.input.back();
        if (short_file) peer.input.pop_back();
        else peer.input.push_back(0);
        assert(source.open() == P::s_bad_request && !source.reader().read);
        assert(!peer.file && peer.opens == peer.closes);
        if (short_file) peer.input.push_back(end);
        else peer.input.pop_back();
    }

    // No signature is checked: a damaged one still opens.
    peer.input[0x80 + 0x14] ^= 1;
    begin(source);
    assert(source.open() == P::s_ok && !peer.public_reads && source.close() == P::s_ok);
    peer.input[0x80 + 0x14] ^= 1;

    const U32 keys = peer.aes_reads;
    begin(source);
    Work before_key(nullptr, until_key);
    peer.aes_reads = 0;
    assert(source.open(&before_key) == P::s_cancelled && !source.reader().read);
    assert(!peer.file && peer.aes_reads == 1 && peer.opens == peer.closes);
    peer.aes_reads = keys;

    peer.input[0x84] = 1; // an AES selector the device never reads
    begin(source);
    assert(source.open() == P::s_image && !source.reader().read);
    assert(!peer.file && !peer.public_reads);
    peer.input[0x84] = 0;

    begin(source);
    bool stopped = false;
    Work during_read(&stopped, until_stopped);
    assert(source.open(&during_read) == P::s_ok);
    const U32 reads = peer.reads;
    stopped = true;
    assert(!input::read(source.reader(), 8192, decrypted.data(), 512));
    assert(during_read.cancelled() && peer.reads == reads);
    assert(source.close() == P::s_ok && peer.opens == peer.closes);

    // #279: read straight through, the next half is out (read ahead) while the reader decrypts the one before.
    begin(source);
    Work counting(nullptr, count_ahead);
    assert(source.open(&counting) == P::s_ok);
    peer.ahead_checks = 0;
    assert(input::read(source.reader(), 0, decrypted.data(), reader.size) && decrypted == archive);
    assert(reader.size > 3 * xy_forever::file_client::stream_max && peer.ahead_checks > 3);
    assert(source.close() == P::s_ok && peer.opens == peer.closes && !peer.ahead);
}

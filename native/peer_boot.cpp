#include "peer_boot.hpp"
#include "inflate.hpp"
namespace xyf_core { namespace peer {
namespace {
const Byte extension[8] = {'C', 'L', 'P', 'E', 'E', 'R', '0', '1'};
void put(Byte* out, U32 value) {
    for (U32 i = 0; i < 4; ++i) out[i] = Byte(value >> (8 * i));
}
U32 get(const Byte* in) {
    U32 value = 0;
    for (U32 i = 0; i < 4; ++i) value |= U32(in[i]) << (8 * i);
    return value;
}
bool same(const Byte* a, const Byte* b, U32 size) {
    U32 difference = 0;
    for (U32 i = 0; i < size; ++i) difference |= a[i] ^ b[i];
    return !difference;
}
bool hash_image(const Byte* image, U32 size, hash::Digest& out) {
    hash::Sha256 hash;
    return hash.update(image, size) && hash.finish(out);
}
U32 crc_image(const Byte* image, U32 size) { return zip::crc32_update(0xffffffffu, image, size) ^ 0xffffffffu; }
// Copy count bytes; word-wise when both ends are aligned (boot buffers are uncached).
void copy_bytes(Byte* to, const Byte* from, U32 count) {
    typedef U32 __attribute__((may_alias)) Word;
    U32 i = 0;
    if (!((reinterpret_cast<unsigned long>(to) | reinterpret_cast<unsigned long>(from)) & 3u))
        for (; count - i >= 4; i += 4) *reinterpret_cast<Word*>(to + i) = *reinterpret_cast<const Word*>(from + i);
    for (; i < count; ++i) to[i] = from[i];
}
U32 batch(U32 next, U32 sectors) {
    U32 remaining = sectors + 1 - next;
    return remaining < maximum_sectors ? remaining : maximum_sectors;
}
bool operation(U32 value) { return value == 7 || value == 0x11 || value == 0x12 || value == 0x18; }
bool command(const Request& request) {
    if (request.operation == 7 || request.operation == 0x11) return !request.argument0 && !request.argument1;
    if (request.operation == 0x18) return request.argument0 <= 1 && !request.argument1;
    if (request.operation == 0x12)
        return request.argument0 && request.argument0 <= (maximum_image >> 9) + 1 && request.argument1 &&
               request.argument1 <= maximum_sectors &&
               request.argument1 <= (maximum_image >> 9) + 2 - request.argument0;
    return false;
}
}
Status encode_command(const Request& request, U32 sequence, Byte* output, U32 capacity, U32& produced) {
    produced = 0;
    if (!command(request) || sequence > 255 || !output) return Status::argument;
    if (capacity < 12) return Status::capacity;
    put(output, request.operation | (sequence << 8));
    put(output + 4, request.argument0);
    put(output + 8, request.argument1);
    produced = 12;
    return Status::ok;
}
Status decode_command(const Byte* bytes, U32 size, Request& request, U32& sequence) {
    request.operation = 0;
    request.argument0 = 0;
    request.argument1 = 0;
    sequence = 0;
    if (!bytes || size != 12) return Status::argument;
    const U32 header = get(bytes);
    Request decoded = {header & 255, get(bytes + 4), get(bytes + 8)};
    if ((header >> 16) || !command(decoded)) return Status::protocol;
    request = decoded;
    sequence = (header >> 8) & 255;
    return Status::ok;
}
Status encode_result(U32 op, U32 sequence, U32 result, Byte* output, U32 capacity, U32& produced) {
    produced = 0;
    if (!operation(op) || sequence > 255 || !output) return Status::argument;
    if (capacity < 8) return Status::capacity;
    put(output, op | (sequence << 8));
    put(output + 4, result);
    produced = 8;
    return Status::ok;
}
Status decode_result(const Byte* bytes, U32 size, U32 op, U32 sequence, U32& result) {
    result = 0;
    if (!bytes || size != 8 || !operation(op) || sequence > 255) return Status::argument;
    if (get(bytes) != (op | (sequence << 8))) return Status::protocol;
    result = get(bytes + 4);
    return Status::ok;
}
Server::Server()
    : image_(0), size_(0), crc_(0), sectors_(0), next_sector_(1), digest_(), phase_(Phase::stopped),
      metadata_read_(false), recheck_(true) {}
void Server::begin() {
    stop();
    phase_ = Phase::waiting;
}
void Server::stop() {
    image_ = 0;
    size_ = 0;
    crc_ = 0;
    sectors_ = 0;
    next_sector_ = 1;
    metadata_read_ = false;
    phase_ = Phase::stopped;
}
bool Server::ready() const { return phase_ == Phase::ready; }
Phase Server::phase() const { return phase_; }
Status Server::freeze(const Byte* image, U32 size, const hash::Digest& admitted) {
    if (phase_ != Phase::waiting) return Status::stopped;
    if (!image || !size || size > maximum_image) {
        stop();
        return Status::argument;
    }
    hash::Digest actual;
    if (!hash_image(image, size, actual) || !hash::equal(actual, admitted)) {
        stop();
        return Status::identity;
    }
    image_ = image;
    size_ = size;
    digest_ = actual;
    crc_ = crc_image(image, size);
    recheck_ = true;
    // Stock reads one extra sector even when the logical length is aligned.
    sectors_ = (size >> 9) + 1;
    next_sector_ = 1;
    phase_ = Phase::select;
    return Status::ok;
}
Status Server::freeze_unhashed(const Byte* image, U32 size, U32 crc) {
    if (phase_ != Phase::waiting) return Status::stopped;
    if (!image || !size || size > maximum_image) {
        stop();
        return Status::argument;
    }
    image_ = image;
    size_ = size;
    digest_ = hash::Digest();
    crc_ = crc;
    recheck_ = false;
    sectors_ = (size >> 9) + 1;
    next_sector_ = 1;
    phase_ = Phase::select;
    return Status::ok;
}
Status Server::freeze_hashing(const Byte* image, U32 size, hash::Digest& digest) {
    if (phase_ != Phase::waiting) return Status::stopped;
    if (!image || !size || size > maximum_image) {
        stop();
        return Status::argument;
    }
    if (!hash_image(image, size, digest)) {
        stop();
        return Status::identity;
    }
    image_ = image;
    size_ = size;
    digest_ = digest;
    crc_ = crc_image(image, size);
    recheck_ = true;
    sectors_ = (size >> 9) + 1;
    next_sector_ = 1;
    phase_ = Phase::select;
    return Status::ok;
}
Status Server::process(const Request& req, Byte* out, U32 capacity, U32& produced) {
    produced = 0;
    if (phase_ == Phase::waiting) return Status::waiting;
    if (phase_ == Phase::stopped || phase_ == Phase::ready) return Status::stopped;
    if (req.operation == 0x18 && req.argument1 == 0) {
        if (req.argument0 == 1 && (phase_ == Phase::select || phase_ == Phase::metadata || phase_ == Phase::data)) {
            if (phase_ == Phase::select) phase_ = Phase::metadata;
            return Status::ok;
        }
        if (req.argument0 == 0 && metadata_read_ && next_sector_ == sectors_ + 1 &&
            (phase_ == Phase::data || phase_ == Phase::restore || phase_ == Phase::release)) {
            // Last point the owner can refuse: on the stock link, generic 7 is
            // acknowledged by the stock slave ISR before this code sees it, so
            // the owner's final identity check happens on restore instead.
            if (recheck_ && crc_image(image_, size_) != crc_) {
                stop();
                return Status::identity;
            }
            phase_ = Phase::release;
            return Status::ok;
        }
    } else if (req.operation == 0x11 && req.argument0 == 0 && req.argument1 == 0 &&
               (phase_ == Phase::metadata || phase_ == Phase::data)) {
        if (!out || capacity < sector_bytes) return Status::capacity;
        for (U32 i = 0; i < sector_bytes; ++i) out[i] = 0;
        put(out, 3);
        put(out + 4, size_);
        put(out + 8, crc_);
        out[12] = 1;
        for (U32 i = 0; i < 8; ++i) out[16 + i] = extension[i];
        put(out + 24, 1);
        for (U32 i = 0; i < 32; ++i) out[28 + i] = digest_.bytes[i];
        produced = sector_bytes;
        metadata_read_ = true;
        phase_ = Phase::data;
        return Status::ok;
    } else if (req.operation == 0x12 && phase_ == Phase::data && metadata_read_) {
        const U32 first = req.argument0, count = req.argument1;
        if (first && count && count <= maximum_sectors && first <= next_sector_ && first <= sectors_ &&
            count <= sectors_ + 1 - first) {
            const U32 bytes = count * sector_bytes, offset = (first - 1) * sector_bytes;
            if (!out || capacity < bytes) return Status::capacity;
            const U32 real = offset >= size_ ? 0 : size_ - offset < bytes ? size_ - offset : bytes;
            copy_bytes(out, image_ + offset, real);
            for (U32 i = real; i < bytes; ++i) out[i] = 0;
            produced = bytes;
            if (first + count > next_sector_) next_sector_ = first + count;
            return Status::ok;
        }
    } else if (req.operation == 7 && req.argument0 == 0 && req.argument1 == 0 && phase_ == Phase::release) {
        // No veto here: the peer already holds a zero result (see restore).
        phase_ = Phase::ready;
        return Status::ok;
    }
    stop();
    return Status::protocol;
}
Client::Client()
    : destination_(0), capacity_(0), size_(0), crc_(0), sectors_(0), next_sector_(1), running_(0xffffffffu), digest_(),
      phase_(Phase::stopped), hashed_(true) {}
void Client::stop() { phase_ = Phase::stopped; }
bool Client::ready() const { return phase_ == Phase::ready; }
Phase Client::phase() const { return phase_; }
U32 Client::image_size() const { return size_; }
const hash::Digest& Client::digest() const { return digest_; }
U32 Client::crc() const { return crc_; }
Status Client::initialize(Byte* destination, U32 capacity, bool hashed) {
    stop();
    size_ = 0;
    crc_ = 0;
    sectors_ = 0;
    next_sector_ = 1;
    running_ = 0xffffffffu;
    hashed_ = hashed;
    if (!destination || capacity < sector_bytes) return Status::argument;
    destination_ = destination;
    capacity_ = capacity;
    phase_ = Phase::select;
    return Status::ok;
}
Status Client::request(Request& req) const {
    req.operation = 0;
    req.argument0 = 0;
    req.argument1 = 0;
    if (phase_ == Phase::admit) return Status::waiting;
    if (phase_ == Phase::select) {
        req.operation = 0x18;
        req.argument0 = 1;
    } else if (phase_ == Phase::metadata) req.operation = 0x11;
    else if (phase_ == Phase::data) {
        req.operation = 0x12;
        req.argument0 = next_sector_;
        req.argument1 = batch(next_sector_, sectors_);
    } else if (phase_ == Phase::restore) req.operation = 0x18;
    else if (phase_ == Phase::release) req.operation = 7;
    else return Status::stopped;
    return Status::ok;
}
Status Client::accept(U32 result, const Byte* bytes, U32 count) {
    if (phase_ == Phase::stopped || phase_ == Phase::ready) return Status::stopped;
    if (result) {
        stop();
        return Status::protocol;
    }
    if (phase_ == Phase::select || phase_ == Phase::restore || phase_ == Phase::release) {
        if (count) {
            stop();
            return Status::protocol;
        }
        phase_ = phase_ == Phase::select ? Phase::metadata : phase_ == Phase::restore ? Phase::release : Phase::ready;
        return Status::ok;
    }
    if (!bytes) {
        stop();
        return Status::argument;
    }
    if (phase_ == Phase::metadata) {
        if (count != sector_bytes || get(bytes) != 3 || bytes[12] != 1 || bytes[13] || bytes[14] || bytes[15] ||
            !same(bytes + 16, extension, 8) || get(bytes + 24) != 1) {
            stop();
            return Status::protocol;
        }
        const U32 size = get(bytes + 4);
        if (!size || size > maximum_image) {
            stop();
            return Status::protocol;
        }
        const U32 sectors = (size >> 9) + 1;
        if (sectors > capacity_ / sector_bytes) {
            stop();
            return Status::capacity;
        }
        for (U32 i = 60; i < sector_bytes; ++i)
            if (bytes[i]) {
                stop();
                return Status::protocol;
            }
        size_ = size;
        sectors_ = sectors;
        crc_ = get(bytes + 8);
        running_ = 0xffffffffu;
        for (U32 i = 0; i < 32; ++i) digest_.bytes[i] = bytes[28 + i];
        phase_ = Phase::data;
        return Status::ok;
    }
    if (phase_ == Phase::data) {
        const U32 sectors = batch(next_sector_, sectors_), expected = sectors * sector_bytes;
        if (count != expected) {
            stop();
            return Status::protocol;
        }
        const U32 offset = (next_sector_ - 1) * sector_bytes;
        copy_bytes(destination_ + offset, bytes, count);
        // The CRC runs per batch as sectors arrive (in order, each once), over the image bytes only.
        const U32 useful = offset >= size_ ? 0 : size_ - offset < count ? size_ - offset : count;
        running_ = zip::crc32_update(running_, bytes, useful);
        next_sector_ += sectors;
        if (next_sector_ == sectors_ + 1) {
            for (U32 i = size_; i < sectors_ * sector_bytes; ++i)
                if (destination_[i]) {
                    stop();
                    return Status::protocol;
                }
            hash::Digest actual;
            if ((running_ ^ 0xffffffffu) != crc_ ||
                (hashed_ && (!hash_image(destination_, size_, actual) || !hash::equal(actual, digest_)))) {
                stop();
                return Status::identity;
            }
            phase_ = Phase::admit;
        }
        return Status::ok;
    }
    stop();
    return Status::protocol;
}
Status Client::admit(const hash::Digest& admitted) {
    if (phase_ != Phase::admit) return Status::stopped;
    // The received bytes were hashed and CRC-checked at the last sector; re-check
    // the CRC so a change during the caller's admission still stops the agreement.
    // Hashed transfers re-check the CRC here; unhashed ones checked it at the last sector
    // and check their boot copy at hand-off (each copy is CRC-checked once).
    if (!hash::equal(admitted, digest_) || (hashed_ && crc_image(destination_, size_) != crc_)) {
        stop();
        return Status::identity;
    }
    phase_ = Phase::restore;
    return Status::ok;
}
}}

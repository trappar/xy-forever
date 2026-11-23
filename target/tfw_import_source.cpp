#include "tfw_import_source.hpp"
#if defined(__bfin__) && defined(XYF_TFW_CHECK)
#include "boot_services.hpp"
#include "tfw_device_check.hpp"
#include "tfw_hash_rpc.hpp"
#include "usb_proto.hpp"
#include "xyf_clock.hpp"

namespace xy_forever { namespace tfw_import {
namespace {
namespace P = xyf::usbp;
namespace rpc = tfw_hash_rpc;
uint32_t read_status(const file_client::Reply& r) {
    if (r.outcome) return file_client::status(r.outcome);
    return r.value < 0 ? uint32_t(P::s_storage) : uint32_t(P::s_ok);
}
bool inbox_path(const char* path) {
    if (!services::loader_path(path) || file_engine::decide_engine(file_engine::op_read, path) != file_engine::allow)
        return false;
    static const char inbox[] = "/yaffs2/xy-forever/inbox/";
    for (uint32_t i = 0; i != sizeof(inbox) - 1; ++i)
        if (path[i] != inbox[i]) return false;
    return path[sizeof(inbox) - 1];
}
}

Source::Source()
    : path_{}, size_(0), progress_(0), error_(0), operation_(0), position_(0), cached_at_(0), cached_bytes_(0),
      half_(0), ahead_at_(0), ahead_bytes_(0), tail_at_(0), reads_(0), waited_(0), cached_(nullptr),
      phase_(SourcePhase::idle), pending_(false), lease_(false), ahead_(false), ahead_ready_(false), owner_kept_(false),
      fd_(-1), owner_{}, digest_{}, tail_{} {}

uint32_t Source::file_size(uint32_t& bytes) const {
    uint8_t record[file_client::record_bytes];
    const auto r = file_client::stat(path_, record);
    if (r.outcome) return file_client::status(r.outcome);
    if (r.value) return r.error == file_client::enoent ? P::s_not_found : P::s_storage;
    if ((P::get32(record + 8) & 0xf000u) != 0x8000u) return P::s_bad_request;
    bytes = P::get32(record + 0x1c);
    return P::s_ok;
}

uint32_t Source::begin(const char* path) {
    if (phase_ == SourcePhase::hashing || fd_ >= 0) return P::s_busy;
    if (!inbox_path(path)) return P::s_bad_request;
    for (uint32_t i = 0; i != sizeof(path_); ++i)
        if (!(path_[i] = path[i])) break;
    progress_ = 0;
    error_ = file_size(size_);
    if (!error_ && size_ < xyf_core::tfw::prefix_bytes) error_ = P::s_bad_request;
    if (!error_ && size_ > xyf_core::tfw::max_package_bytes) error_ = P::s_too_large;
    if (error_) {
        phase_ = SourcePhase::failed;
        return error_;
    }
    phase_ = SourcePhase::hashing;
    operation_ = rpc::start;
    return P::s_ok;
}

void Source::finish_hash() {
    pending_ = lease_ = false;
    phase_ = error_ == P::s_cancelled ? SourcePhase::cancelled : error_ ? SourcePhase::failed : SourcePhase::hashed;
}

void Source::cancel_hash() {
    if (phase_ == SourcePhase::hashing) error_ = P::s_cancelled;
}

SourcePhase Source::hash_step() {
    if (phase_ != SourcePhase::hashing) return phase_;
    if (!pending_) {
        if (error_) {
            if (!lease_) {
                finish_hash();
                return phase_;
            }
            operation_ = rpc::cancel;
        }
        const auto sent = file_client::tfw_hash_send(path_, operation_);
        if (sent.outcome == file_client::busy) return phase_;
        if (sent.outcome) {
            error_ = file_client::status(sent.outcome);
            finish_hash();
        } else pending_ = true;
        return phase_;
    }
    const auto r = file_client::left_poll(false);
    if (r.outcome == file_client::busy) return phase_;
    pending_ = false;
    if (r.outcome) {
        error_ = file_client::status(r.outcome);
        finish_hash();
        return phase_;
    }
    const uint32_t code = uint32_t(r.value);
    if (operation_ == rpc::cancel) {
        if (code != P::s_ok && !(error_ && code == P::s_state)) error_ = P::s_storage;
        finish_hash();
    } else if (operation_ == rpc::start && code == P::s_ok) {
        lease_ = true;
        if (!error_ && r.error != size_) error_ = P::s_bad_request;
        operation_ = rpc::status;
    } else if (operation_ == rpc::status && (code == P::s_busy || code == P::s_ok)) {
        if (!error_ && (r.error < progress_ || r.error > size_ || (code == P::s_ok && r.error != size_)))
            error_ = P::s_bad_request;
        if (!error_) progress_ = r.error;
        if (code == P::s_ok) operation_ = rpc::digest;
    } else if (operation_ >= rpc::digest && code == P::s_ok) {
        P::put32(digest_.bytes + 4 * (operation_ - rpc::digest), r.error);
        if (++operation_ == rpc::digest + rpc::digest_words) operation_ = rpc::cancel;
    } else if (!error_) {
        error_ = code == P::s_bad_request || code == P::s_not_found || code == P::s_storage || code == P::s_busy ||
                         code == P::s_unsupported ?
                     code :
                     uint32_t(P::s_link);
    }
    return phase_;
}

uint32_t Source::open(xyf_core::Work* work) {
    if (phase_ != SourcePhase::hashed) return P::s_state;
    uint32_t observed = 0;
    uint32_t code = xyf_core::checkpoint(work) ? file_size(observed) : uint32_t(P::s_cancelled);
    if (!code && size_ != observed) code = P::s_bad_request;
    if (!code && xyf_core::checkpoint(work)) {
        const auto r = file_client::open(path_, 0);
        code = read_status(r);
        if (!code) fd_ = r.value;
    }
    if (!code && xyf_core::checkpoint(work)) {
        const auto r = file_client::seek(fd_, int(size_ - 1));
        code = read_status(r);
        if (!code && uint32_t(r.value) != size_ - 1) code = P::s_storage;
    }
    if (!code && xyf_core::checkpoint(work)) {
        uint8_t last[2];
        const auto r = file_client::read(fd_, last, 2);
        code = read_status(r);
        if (!code && r.value != 1) code = P::s_bad_request;
    }
    if (!code && xyf_core::checkpoint(work)) {
        cached_at_ = position_ = tail_at_ = size_;
        cached_bytes_ = ahead_bytes_ = 0;
        ahead_ready_ = false;
        const xyf_core::input::Reader source = {this, size_, read_at};
        xyf_core::Byte header[xyf_core::tfw::header_bytes];
        const auto status = tfw_device_check::authenticate(path_, {source, digest_, archive_, header, work});
        using xyf_core::tfw::Status;
        if (status == Status::ciphertext_hash) code = P::s_sha;
        else if (status != Status::ok) code = error_ ? error_ : uint32_t(P::s_image);
    }
    if (xyf_core::cancelled(work)) code = P::s_cancelled;
    if (code) {
        const uint32_t closed = close();
        error_ = closed ? closed : code;
        phase_ = error_ == P::s_cancelled ? SourcePhase::cancelled : SourcePhase::failed;
        return error_;
    }
    phase_ = SourcePhase::authenticated;
    return P::s_ok;
}

uint32_t Source::close(bool reopen) {
    if (phase_ == SourcePhase::hashing) return P::s_busy;
    reopen = reopen && phase_ == SourcePhase::authenticated;
    archive_.close();
    finish_ahead();                                                     // the read-ahead's answer first, if out
    ahead_ready_ = false;
    if (fd_ >= 0) {
        const auto r = file_client::close(fd_);
        const uint32_t code = read_status(r);
        if (code || r.value) {
            phase_ = SourcePhase::failed;
            error_ = code ? code : uint32_t(P::s_storage);
            return error_;
        }
        fd_ = -1;
    }
    phase_ = reopen ? SourcePhase::hashed : SourcePhase::idle;
    return P::s_ok;
}

// #279: one request reads a half (stream_max, two of A's 0x1000-byte bodies) into the stream buffer, left out, so A
// reads the next bytes while B decrypts, inflates and hashes these. Sequential reads ask for the next half as soon
// as they start on one; a jump (the archive's directory, a header) is read on its own, as before.
const uint32_t half_bytes = file_client::stream_max;
static_assert(2 * half_bytes <= file_client::stream_capacity && half_bytes % 512 == 0, "two halves");

bool Source::finish_ahead() {
    if (!ahead_) return true;
    ahead_ = false;
    const auto r = file_client::left_poll(true);
    const uint32_t code = read_status(r);
    if (code || uint32_t(r.value) != ahead_bytes_) {
        if (!error_) error_ = code ? code : uint32_t(P::s_bad_request);
        return false;
    }
    ahead_ready_ = true;
    return true;
}

bool Source::ask(uint32_t at, uint32_t bytes) {
    if (file_client::left_out()) {                                      // the owner's write: kept for it
        owner_ = file_client::left_poll(true);
        owner_kept_ = true;
    }
    if (position_ != at) {
        const auto seek = file_client::seek(fd_, int(at));
        error_ = read_status(seek);
        if (!error_ && uint32_t(seek.value) != at) error_ = P::s_storage;
        if (error_) return false;
        position_ = at;
    }
    ++reads_;
    const auto r = file_client::read_start(fd_, (half_ ^ 1u) * half_bytes, bytes);
    error_ = r.outcome ? file_client::status(r.outcome) : 0;
    if (error_) return false;
    position_ = at + bytes;
    ahead_ = true;
    ahead_ready_ = false;
    ahead_at_ = at;
    ahead_bytes_ = bytes;
    return true;
}

bool Source::load(uint32_t offset) {
    const uint64_t start = xyf::clock::cycles();
    const bool ok = load_from(offset);
    waited_ += xyf::clock::cycles() - start;
    return ok;
}

bool Source::load_from(uint32_t offset) {
    const bool next = ahead_bytes_ && offset >= ahead_at_ && offset - ahead_at_ < ahead_bytes_;
    if (!next) {                                                        // a jump: the half it is in, read now
        if (!finish_ahead()) return false;
        const uint32_t at = offset & ~511u;
        if (!ask(at, size_ - at < half_bytes ? size_ - at : half_bytes)) return false;
    }
    if (!finish_ahead()) return false;
    if (cached_bytes_ && cached_at_ + cached_bytes_ == ahead_at_ && cached_bytes_ >= 16) {
        for (uint32_t i = 0; i != 16; ++i) tail_[i] = cached_[cached_bytes_ - 16 + i];
        tail_at_ = ahead_at_ - 16;
    } else tail_at_ = size_;
    half_ ^= 1u;
    cached_at_ = ahead_at_;
    cached_bytes_ = ahead_bytes_;
    cached_ = file_client::stream(half_ * half_bytes, cached_bytes_);   // its cache lines invalidated
    ahead_bytes_ = 0;
    ahead_ready_ = false;
    const uint32_t end = cached_at_ + cached_bytes_;                    // the next half, while B works on this one
    if (next && end < size_) ask(end, size_ - end < half_bytes ? size_ - end : half_bytes);
    error_ = 0;                                                         // a failed read-ahead is asked again
    return true;
}

bool Source::read_at(void* context, xyf_core::U32 offset, xyf_core::Byte* out, xyf_core::U32 count) {
    auto& source = *static_cast<Source*>(context);
    if (source.fd_ < 0 || source.error_ || offset > source.size_ || count > source.size_ - offset) return false;
    if (offset >= source.tail_at_ && count <= 16 && offset - source.tail_at_ <= 16 - count) {
        for (uint32_t i = 0; i != count; ++i) out[i] = source.tail_[offset - source.tail_at_ + i];
        return true;
    }
    while (count) {
        if ((offset < source.cached_at_ || offset - source.cached_at_ >= source.cached_bytes_) && !source.load(offset))
            return false;
        const uint32_t skip = offset - source.cached_at_;
        const uint32_t available = source.cached_bytes_ - skip;
        const uint32_t n = count < available ? count : available;
        for (uint32_t i = 0; i != n; ++i) *out++ = source.cached_[skip + i];
        offset += n;
        count -= n;
    }
    return true;
}

xyf_core::input::Reader Source::reader() { return archive_.reader(); }
}}
#endif

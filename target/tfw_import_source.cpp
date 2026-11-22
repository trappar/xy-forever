#include "tfw_import_source.hpp"
#if defined(__bfin__) && defined(XYF_TFW_CHECK)
#include "boot_services.hpp"
#include "tfw_device_check.hpp"
#include "tfw_hash_rpc.hpp"
#include "usb_proto.hpp"

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
      phase_(SourcePhase::idle), pending_(false), lease_(false), fd_(-1), digest_{} {}

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
        const auto r = file_client::read(fd_, cache_, 2);
        code = read_status(r);
        if (!code && r.value != 1) code = P::s_bad_request;
    }
    if (!code && xyf_core::checkpoint(work)) {
        cached_at_ = position_ = size_;
        cached_bytes_ = 0;
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

uint32_t Source::close() {
    if (phase_ == SourcePhase::hashing) return P::s_busy;
    archive_.close();
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
    phase_ = SourcePhase::idle;
    return P::s_ok;
}

bool Source::load(uint32_t offset) {
    const uint32_t at = offset & ~511u;
    if (position_ != at) {
        const auto seek = file_client::seek(fd_, int(at));
        error_ = read_status(seek);
        if (!error_ && uint32_t(seek.value) != at) error_ = P::s_storage;
        if (error_) return false;
    }
    const uint32_t bytes = size_ - at < sizeof(cache_) ? size_ - at : sizeof(cache_);
    const auto got = file_client::read(fd_, cache_, bytes);
    error_ = read_status(got);
    if (!error_ && uint32_t(got.value) != bytes) error_ = P::s_bad_request;
    if (error_) return false;
    cached_at_ = at;
    cached_bytes_ = bytes;
    position_ = at + bytes;
    return true;
}

bool Source::read_at(void* context, xyf_core::U32 offset, xyf_core::Byte* out, xyf_core::U32 count) {
    auto& source = *static_cast<Source*>(context);
    if (source.fd_ < 0 || source.error_ || offset > source.size_ || count > source.size_ - offset) return false;
    while (count) {
        if ((offset < source.cached_at_ || offset - source.cached_at_ >= source.cached_bytes_) && !source.load(offset))
            return false;
        const uint32_t skip = offset - source.cached_at_;
        const uint32_t available = source.cached_bytes_ - skip;
        const uint32_t n = count < available ? count : available;
        for (uint32_t i = 0; i != n; ++i) *out++ = source.cache_[skip + i];
        offset += n;
        count -= n;
    }
    return true;
}

xyf_core::input::Reader Source::reader() { return archive_.reader(); }
}}
#endif

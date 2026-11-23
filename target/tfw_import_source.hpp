#ifndef XYF_TFW_IMPORT_SOURCE_HPP
#define XYF_TFW_IMPORT_SOURCE_HPP

#include "file_client.hpp"
#include "file_engine.hpp"
#include "../native/tfw.hpp"

namespace xy_forever { namespace tfw_import {
enum class SourcePhase { idle, hashing, hashed, authenticated, failed, cancelled };
// Caller holds B's exclusive storage job from begin() until close().
// That excludes MTP, package sessions and all inbox mutations between A's hash and B's
// reopen and through all member reads. A begins with no held handles and a read-only
// session. One caller owns B's left-request slot during hash_step(). Loss of the link
// leaves A to expire its job. If a B handle cannot be closed, the caller must finish
// the existing file-session teardown before releasing ownership.
class Source {
public:
    Source();
    uint32_t begin(const char* inbox_path);
    SourcePhase hash_step();
    void cancel_hash();
    // Opens the same file read-only, checks its length, and verifies the signed header
    // and A's digest before admitting the archive. Work must outlive all member reads.
    uint32_t open(xyf_core::Work* work = nullptr);
    uint32_t close(bool reopen = false);
    const xyf_core::hash::Digest& identity() const { return digest_; }
    // The decrypted reader is borrowed and is valid only until close().
    xyf_core::input::Reader reader();
    const char* path() const { return path_; }
    // #279: the owner's request that was left out (file_client::write_start's kind), collected here so the source
    // could leave its own read out; the owner takes its answer here instead of from left_poll.
    bool owner_kept() const { return owner_kept_; }
    file_client::Reply take_owner() {
        owner_kept_ = false;
        return owner_;
    }
    // #279: the source's read-ahead collected, so the owner can leave its own request out; false: it failed (the
    // next read of the source fails).
    bool settle() { return finish_ahead(); }
    // #279: B's cycles spent waiting for A's reads (seek, read, the read-ahead's answer), and the reads asked.
    uint64_t waited() const { return waited_; }
    uint32_t reads() const { return reads_; }
    uint32_t size() const { return size_; }
    uint32_t progress() const { return progress_; }
    uint32_t error() const { return error_; }
    SourcePhase phase() const { return phase_; }

private:
    Source(const Source&) = delete;
    Source& operator=(const Source&) = delete;
    uint32_t file_size(uint32_t& bytes) const;
    void finish_hash();
    bool load(uint32_t offset);
    bool load_from(uint32_t offset);
    bool ask(uint32_t at, uint32_t bytes);
    bool finish_ahead();
    static bool read_at(void* context, xyf_core::U32 offset, xyf_core::Byte* out, xyf_core::U32 count);
    char path_[file_engine::max_path];
    uint32_t size_, progress_, error_, operation_, position_, cached_at_, cached_bytes_;
    // #279 read-ahead: file_client's stream buffer in two halves. cached_ is the half being read (half_); while it
    // is, the next bytes (ahead_at_, ahead_bytes_) are asked into the other one (read_start, left out: ahead_).
    // tail_: the 16 bytes before cached_at_ (the half before), which the AES chaining reads again.
    uint32_t half_, ahead_at_, ahead_bytes_, tail_at_, reads_;
    uint64_t waited_;
    const xyf_core::Byte* cached_;
    SourcePhase phase_;
    bool pending_, lease_, ahead_, ahead_ready_, owner_kept_;
    int fd_;
    file_client::Reply owner_;
    xyf_core::hash::Digest digest_;
    xyf_core::tfw::Archive archive_;
    xyf_core::Byte tail_[16];
};
}}
#endif

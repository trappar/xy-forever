#include "tfw_signature.hpp"
namespace xyf_core { namespace tfw {
void signed_digest(const Byte* header, hash::Digest& digest) {
    // Stock saves +0x10..+0x50, zeroes it, hashes the 0x300 bytes and restores it; the same
    // message without writing to the header.
    Byte zero[16];
    for (U32 i = 0; i < sizeof(zero); ++i) zero[i] = 0;
    hash::Sha256 hasher;
    hasher.update(header, signature_hole_begin);
    for (U32 at = signature_hole_begin; at < signature_hole_end; at += sizeof(zero)) hasher.update(zero, sizeof(zero));
    hasher.update(header + signature_hole_end, crypto_header_bytes - signature_hole_end);
    hasher.finish(digest);
}
Signature verify_header(const Byte* header, U32 size, const Byte* public_key) {
    if (!header || !public_key) return Signature::argument;
    if (size != crypto_header_bytes) return Signature::size;
    if (header[signature_key_index] != 0) return Signature::key_index;
    hash::Digest digest;
    signed_digest(header, digest);
    switch (p224::verify(public_key, digest.bytes, sizeof(digest.bytes), header + signature_r, header + signature_s)) {
    case p224::Status::valid: return Signature::valid;
    case p224::Status::key: return Signature::key;
    case p224::Status::range: return Signature::range;
    case p224::Status::mismatch: return Signature::mismatch;
    case p224::Status::argument: break;
    }
    return Signature::argument;
}
namespace {
bool check(void* context, const Byte* header, U32 size) {
    return verify_header(header, size, static_cast<const Byte*>(context)) == Signature::valid;
}
}
HeaderCheck signature_check(const Byte* public_key) {
    HeaderCheck result = {const_cast<Byte*>(public_key), check};
    return result;
}
const char* signature_name(Signature result) {
    switch (result) {
    case Signature::valid: return "verified";
    case Signature::size: return "size";
    case Signature::key_index: return "key_index";
    case Signature::key: return "public_key";
    case Signature::range: return "range";
    case Signature::mismatch: return "mismatch";
    case Signature::argument: return "argument";
    }
    return "unknown";
}
} }

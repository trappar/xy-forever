#ifndef XYF_TFW_HPP
#define XYF_TFW_HPP
#include "aes.hpp"
#include "hash.hpp"
#include "input.hpp"
#include "package_policy.hpp"
#include "work.hpp"
namespace xyf_core { namespace tfw {
enum class Status {
    ok,
    io,
    header,
    manifest,
    size,
    sku,
    ciphertext_hash,
    key,
    padding,
    filename,
    signature,
    argument,
    cancelled
};
static const U32 op_xy_sku = 0x00084001u;
struct KeyProvider {
    void* context;
    bool (*read)(void*, U32 index, Byte key32[32]);
};
// The signature seam (tfw_signature.hpp: signature_check): called with the 0x300-byte crypto header
// exactly as read (TESG manifest, IV, sizes, ciphertext SHA-256, P-224 r/s) after its
// structural checks and before the key is requested or any field is relied on.
struct HeaderCheck {
    void* context;
    bool (*verify)(void*, const Byte* crypto_header, U32 size);
};
// Bytes the caller provides to Archive::open_digested: outer wrapper + crypto header.
static const U32 header_bytes = 0x380, crypto_header_bytes = 0x300, prefix_bytes = 320;
static const U32 max_package_bytes = 256u * 1024u * 1024u;
struct Header {
    U32 crypto_offset, ciphertext_offset, ciphertext_size, archive_size, key_index, sku;
    package::Version version;
    hash::Digest ciphertext_hash;
    Byte iv[16];
};
// Random-access view of the decrypted ZIP. No firmware/key file is written.
// The source must remain immutable. open() verifies no signature; open_digested()
// runs the caller's HeaderCheck (tfw::signature_check for the vendor key).
// An optional Work must outlive the open Archive and is shared with consumers of reader().
// If reader() returns false, the consumer can use Work::cancelled() to distinguish cancellation.
class Archive {
public:
    Archive();
    ~Archive();
    Status open(const input::Reader& source, const KeyProvider& keys, U32 expected_sku = op_xy_sku);
    // Reads only the header (into `header`, header_bytes long), the encrypted name and the
    // last cipher block: never the rest of the ciphertext. The caller supplies the SHA-256
    // of the ciphertext from this same immutable source (on the device: A's keyless hash
    // job; see ciphertext_digest), compared with the header's after `signature` accepts it.
    // Refuses the clear container (key index 0xfe). The signature callback runs synchronously
    // between checkpoints.
    Status open_digested(const input::Reader& source, const KeyProvider& keys, const hash::Digest& ciphertext_sha256,
                         const HeaderCheck& signature, Byte* header, U32 expected_sku = op_xy_sku,
                         Work* work = nullptr);
    void close();
    input::Reader reader();
    const Header& header() const { return header_; }
    const char* filename() const { return filename_; }

private:
    Archive(const Archive&);
    Archive& operator=(const Archive&);
    Status finish(const Header& header, const KeyProvider& keys);
    bool raw_read(U32 offset, Byte* destination, U32 count);
    static bool zip_read(void* context, U32 offset, Byte* destination, U32 count);
    input::Reader source_;
    crypto::Aes256 aes_;
    Header header_;
    char filename_[129];
    Work* work_;
    bool ready_;
};
// Keyless: checks the header's structure and hashes the ciphertext (every byte after the
// header, opaque; nothing is decrypted). `buffer` holds at least header_bytes.
// With a Work, each read and hash update covers at most 512 bytes.
Status ciphertext_digest(const input::Reader& source, Byte* buffer, U32 capacity, hash::Digest& digest,
                         U32 expected_sku = op_xy_sku, Work* work = nullptr);
// For sequential readers without a file size. Validates the first prefix_bytes and gives
// the declared ciphertext span [start, end). The caller verifies end against actual EOF.
Status ciphertext_span(const Byte* prefix, U32& start, U32& end, U32 expected_sku = op_xy_sku);
const char* status_name(Status status);
}}
#endif

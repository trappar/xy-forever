#ifndef XYF_TFW_OS_HPP
#define XYF_TFW_OS_HPP
#include "tfw.hpp"
#include "inflate.hpp"
namespace xyf_core { namespace tfw_os {
// OS-only extraction from an official .tfw (docs/decisions/003-tfw-install.md).
// Decrypts only the package header's name block, the ZIP end record and central directory,
// and the two wanted members, firmware/blackfin/bin/os.ldr and os.version; every other
// member (bootloader, companions, content) is known only by its directory record. AES-CBC
// works in 16-byte blocks: a wanted range is read from the block that holds its first byte,
// plus the cipher block before it (the chaining value, not decrypted), to the block that
// holds its last byte. Everything is checked before the first byte reaches the writer;
// only a writer failure can stop the output part-way. No heap, no library calls.
enum class Status {
    ok, argument, capacity, io, container, signature, ciphertext_hash, key, directory,
    unsupported, name, duplicate, layout, missing, member, checksum, version, image, write
};
// Working-memory and output bounds. scratch_bytes() = image + directory + 16 * entries.
struct Limits {
    U32 image_bytes, directory_bytes, entries;
    Limits() : image_bytes(3u * 1024u * 1024u), directory_bytes(256u * 1024u), entries(2048) {}
};
static const U32 max_limit_bytes = 16u * 1024u * 1024u, max_entries = 65535, version_bytes = 128;
// 0 when a limit is out of range.
U32 scratch_bytes(const Limits& limits);
// Caller-owned, not shared between concurrent calls. Holds the AES schedule while a call
// runs; extract() erases it before returning. `scratch` must be 4-byte aligned.
struct Workspace {
    tfw::Archive archive;
    zip::InflateWorkspace inflate;
    Byte header[tfw::header_bytes];
    Byte local[30 + 255];
    Byte text[version_bytes];
    Byte* scratch;
    U32 capacity;
};
struct Writer { void* context; bool (*write)(void*, const Byte*, U32 count); };
struct Result {
    U32 bytes, crc32, entry, records, written;  // os.ldr facts; written = bytes given to the writer
    hash::Digest sha256;                        // of os.ldr
    package::Version os_version;                // os.version (the library entry's version)
    package::Version package_version;           // the signed header's version (firmware.version)
    tfw::Status container;                      // detail of container/signature/hash/key failures
    zip::Status inflate;                        // detail of member failures
    Error ldr;                                  // detail of image failures
};
// `ciphertext_sha256`: SHA-256 of the whole ciphertext computed by the caller without the
// key (tfw::ciphertext_digest; on the device A's hash job). `signature`: tfw::signature_check;
// the key is requested only after it accepts the header. `ram_policy` and `entry_point`:
// the LDR policy file boot admits images with (cold.cpp's ranges, entry 0x11a00000).
Status extract(const input::Reader& package, const tfw::KeyProvider& keys,
               const hash::Digest& ciphertext_sha256, const tfw::HeaderCheck& signature,
               const Policy& ram_policy, U32 entry_point, const Limits& limits,
               Workspace& workspace, const Writer& output, Result& result);
// "1.2.5+0"; returns the length (0 when it does not fit, with the terminator).
U32 version_text(const package::Version& version, char* out, U32 capacity);
const char* status_name(Status status);
} }
#endif

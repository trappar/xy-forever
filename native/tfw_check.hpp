#ifndef XYF_TFW_CHECK_HPP
#define XYF_TFW_CHECK_HPP
#include "tfw.hpp"
namespace xyf_core { namespace tfw_check {
// The `tfw.check` command's verdict (docs/decisions/003-tfw-install.md, step 3 up to
// the directory): is this file an official package that this device's own keys open? In 003's
// order: the header's structure (no key), the P-224 signature with the device's public key, the
// AES key on the encrypted name block and the padding, then the ZIP end record and every central
// directory record. Nothing is extracted or written, the payload hash is not checked (that is
// A's keyless hash job, #14), and only the header, the name block, the last cipher block and
// the directory are read.
//
// A file may also be a cut (cut_magic): a package's first `head` bytes and its bytes from `tail`
// on, behind a 16-byte header {magic, package bytes, head, tail} (u32 little-endian). The check
// then reads the package through the cut and fails with `read` for any byte outside it. It lets a
// device test run on a package too big for the device's file write (scripts/tfw_probe.py).
enum class Reason : U32 {
    pass,           // an official package (signature valid) whose directory the device key opens
    read,           // a read failed (storage, the link, a byte outside a cut)
    not_package,    // not a .tfw (or a cut of one), or its sizes disagree with the file
    key_index,      // an AES key index other than 0 (a signature key index other than 0 fails the
                    // header's format check: not_package); no other key is read
    no_otp,         // the device's one-time-programmable memory gave no key
    public_key,     // the device's public key is not a point on P-224
    signature,      // the header signature does not verify with the device's public key
    decrypt,        // the device's AES key does not decrypt the name block or the padding
    directory       // the decrypted ZIP end record or central directory is malformed
};
// Bytes from offset into out: count, fewer at the end of the file, or -1 on an error.
struct File { void* context; int (*read)(void*, U32 offset, Byte* out, U32 count); };
// The device's keys, each written into a buffer the caller passes and erases. public_key: Qx || Qy
// (56 bytes); aes: the extractor's key provider (tfw::KeyProvider). false: no key.
struct Keys { void* context; bool (*public_key)(void*, Byte out[56]); tfw::KeyProvider aes; };
static const U32 cut_magic = 0x54434658u, cut_header_bytes = 16;   // "XFCT"
// Caller-owned working memory; on the device it is on the stack (the AES schedule is in
// `archive`, erased before check() returns).
struct Workspace {
    tfw::Archive archive;
    Byte header[tfw::header_bytes];
    Byte record[46 + 255];
};
struct Result {
    Reason reason;
    bool public_key_read;          // the device answered for its public key (the hash below is valid)
    hash::Digest public_key_sha256;
    tfw::Status container;         // the extractor's detail (tests)
    U32 entries;                   // directory records checked
};
Reason check(const File& file, const Keys& keys, Workspace& workspace, Result& result);
const char* reason_name(Reason reason);
} }
#endif

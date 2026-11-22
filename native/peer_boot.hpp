#ifndef XYF_PEER_BOOT_HPP
#define XYF_PEER_BOOT_HPP
#include "hash.hpp"
namespace xyf_core { namespace peer {
// Storage RPCs observed in bootloader 1.2.1. The codecs below cover its staged
// command/result headers, not SPI DMA transactions or the data-body handshake.
struct Request {
    U32 operation, argument0, argument1;
};
enum class Status { ok, waiting, argument, capacity, protocol, identity, stopped };
enum class Phase { stopped, waiting, select, metadata, data, admit, restore, release, ready };
static const U32 sector_bytes = 512;
static const U32 maximum_image = 8u * 1024u * 1024u;
static const U32 maximum_sectors = 128;
Status encode_command(const Request& request, U32 sequence, Byte* output, U32 capacity, U32& produced);
Status decode_command(const Byte* bytes, U32 size, Request& request, U32& sequence);
Status encode_result(U32 operation, U32 sequence, U32 result, Byte* output, U32 capacity, U32& produced);
Status decode_result(const Byte* bytes, U32 size, U32 operation, U32 sequence, U32& result);
// Selected images must already pass LDR admission. Borrowed source storage must
// remain immutable and live through handoff; output buffers must not overlap it.
// No NAND operations occur here.
// Each side hashes the image once (SHA-256, ~0.7 s per 2.4 MB at boot time); the
// later checks that the frozen or received bytes did not change use the CRC-32
// fixed with that hash (owner: before release; client: at admit).
class Server {
public:
    Server();
    void begin();
    Status freeze(const Byte* image, U32 size, const hash::Digest& admitted);
    // As freeze(), but computes the identity itself (one SHA-256 pass) and returns it.
    Status freeze_hashing(const Byte* image, U32 size, hash::Digest& digest);
    // No SHA-256: the metadata carries a zero digest; the image is identified by size
    // and CRC-32 (the library's info.txt check), which the caller already computed over
    // exactly these bytes. Each copy is CRC-checked once: no restore re-check (the
    // client checks what it received; both check their boot copy at hand-off).
    Status freeze_unhashed(const Byte* image, U32 size, U32 crc);
    Status process(const Request& request, Byte* output, U32 capacity, U32& produced);
    void stop();
    bool ready() const;
    Phase phase() const;

private:
    const Byte* image_;
    U32 size_, crc_, sectors_, next_sector_;
    hash::Digest digest_;
    Phase phase_;
    bool metadata_read_, recheck_;
};
// Loader-owned metadata extension carries the selected SHA-256. The first
// fourteen bytes retain stock v3 clear boot-partition semantics. The extension
// is a new loader design, not a claim about a stock authentication mechanism.
// The client pauses at admit until its caller validates the received LDR and
// supplies that admitted image's digest. The owner may enter after a successful
// release response is generated. Lost replies can strand the peer: this is image
// agreement, not an atomic simultaneous launch protocol.
class Client {
public:
    Client();
    // hashed = false: no SHA-256 of the received image (an unhashed owner sends a zero
    // digest); the CRC-32 from the metadata is still checked at the end and at admit.
    Status initialize(Byte* destination, U32 capacity, bool hashed = true);
    Status request(Request& request) const;
    Status accept(U32 device_result, const Byte* bytes, U32 size);
    Status admit(const hash::Digest& admitted);
    void stop();
    bool ready() const;
    Phase phase() const;
    U32 image_size() const;
    const hash::Digest& digest() const;
    U32 crc() const;                 // the metadata CRC-32, checked over the received bytes
private:
    Byte* destination_;
    U32 capacity_, size_, crc_, sectors_, next_sector_, running_;
    hash::Digest digest_;
    Phase phase_;
    bool hashed_;
};
}}
#endif
